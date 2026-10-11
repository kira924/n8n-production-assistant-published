"""Meaningful API checks: pricing, human approval, tamper resistance and retry safety."""
import os
import secrets
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

# Keep the module-level app's default state out of the deliverable folder.
os.environ.setdefault("ERP_DEMO_DB", str(Path(tempfile.gettempdir()) / "n8n-erp-import-check.sqlite3"))

import pytest
from fastapi.testclient import TestClient

from erp_demo.app import create_app


@pytest.fixture
def demo(tmp_path):
    reviewer_key = secrets.token_urlsafe(24)
    db_path = tmp_path / "demo.sqlite3"
    client = TestClient(create_app(db_path, reviewer_key=reviewer_key))
    return client, {"X-Reviewer-Key": reviewer_key}, db_path


def future_date():
    candidate = date.today() + timedelta(days=2)
    if candidate.day == 15:
        candidate += timedelta(days=1)
    return candidate.isoformat()


def payload(request_id="demo-request-001", **changes):
    result = {
        "request_id": request_id, "brief": "تصوير مقابلة داخل استوديو باستخدام كاميرتين",
        "production_date": future_date(), "duration_hours": 2, "camera_count": 2,
        "include_audio": True, "editing_hours": 2,
    }
    result.update(changes)
    return result


def make_quote(client, request_id="demo-request-001", **changes):
    response = client.post("/quotes", json=payload(request_id, **changes))
    assert response.status_code == 200, response.text
    return response.json()


def approve(client, headers, quote_id):
    response = client.post("/reviews", json={"quote_id": quote_id, "decision": "approved"}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["approval_token"]


def order_body(quote_id, token, key="create-demo-001"):
    return {"quote_id": quote_id, "approval_token": token, "idempotency_key": key}


def test_authoritative_pricing_and_quote_deduplication(demo):
    client, _, _ = demo
    quote = make_quote(client)
    assert quote["currency"] == "SAR" and quote["synthetic"] is True
    assert quote["total_sar"] == 2240  # 700 studio + 800 cameras + 240 audio + 500 editing
    assert quote["fields"]["include_audio"] is True
    assert make_quote(client)["quote_id"] == quote["quote_id"]
    altered = make_quote(client, editing_hours=0)
    assert altered["quote_id"] != quote["quote_id"] and altered["total_sar"] == 1740


def test_cannot_claim_a_different_total(demo):
    client, _, _ = demo
    quote = make_quote(client)
    assert client.post("/quotes/verify", json={"quote_id": quote["quote_id"], "claimed_total_sar": 1}).status_code == 409
    verified = client.post("/quotes/verify", json={"quote_id": quote["quote_id"], "claimed_total_sar": 2240})
    assert verified.status_code == 200 and verified.json()["quote"] == quote


@pytest.mark.parametrize("changes", [
    {"production_date": "2026-02-30"},
    {"production_date": (date.today() - timedelta(days=1)).isoformat()},
    {"duration_hours": "2"}, {"include_audio": "true"}, {"camera_count": 0},
])
def test_invalid_dates_and_untyped_fields_are_rejected(demo, changes):
    client, _, _ = demo
    assert client.post("/quotes", json=payload(**changes)).status_code == 422


def test_unavailable_quote_exists_but_cannot_be_approved(demo):
    client, headers, _ = demo
    quote = make_quote(client, camera_count=3)
    assert quote["available"] is False and "two" in quote["availability_reason"]
    assert client.post("/reviews", json={"quote_id": quote["quote_id"], "decision": "approved"}, headers=headers).status_code == 409
    maintenance_date = date.today() + timedelta(days=1)
    while maintenance_date.day != 15:
        maintenance_date += timedelta(days=1)
    response = client.get("/availability", params={"production_date": maintenance_date.isoformat(), "duration_hours": 2, "camera_count": 1})
    assert response.status_code == 200 and response.json()["available"] is False


def test_review_requires_separate_trusted_credential(demo):
    client, _, _ = demo
    quote = make_quote(client)
    review = {"quote_id": quote["quote_id"], "decision": "approved"}
    assert client.post("/reviews", json=review).status_code == 403
    assert client.post("/reviews", json=review, headers={"X-Reviewer-Key": "wrong-key"}).status_code == 403
    assert client.post("/work-orders", json=order_body(quote["quote_id"], "invented-token" )).status_code == 403
    assert client.post("/work-orders", json={"quote_id": quote["quote_id"], "idempotency_key": "attempt-no-token"}).status_code == 422


def test_missing_reviewer_configuration_fails_closed(tmp_path):
    client = TestClient(create_app(tmp_path / "demo.sqlite3", reviewer_key=""))
    quote = make_quote(client)
    assert client.get("/health").json()["reviewer_configured"] is False
    assert client.post("/reviews", json={"quote_id": quote["quote_id"], "decision": "approved"}).status_code == 503


def test_token_cannot_be_used_for_other_quote_or_modified_fields(demo):
    client, headers, _ = demo
    first = make_quote(client)
    second = make_quote(client, "demo-request-002", editing_hours=1)
    token = approve(client, headers, first["quote_id"])
    assert client.post("/work-orders", json=order_body(second["quote_id"], token)).status_code == 403
    modified_body = order_body(first["quote_id"], token)
    modified_body["total_sar"] = 1
    assert client.post("/work-orders", json=modified_body).status_code == 422


def test_denial_revokes_previous_approval(demo):
    client, headers, _ = demo
    quote = make_quote(client)
    token = approve(client, headers, quote["quote_id"])
    denial = client.post("/reviews", json={"quote_id": quote["quote_id"], "decision": "denied"}, headers=headers)
    assert denial.status_code == 200 and denial.json()["approval_token"] is None
    assert client.post("/work-orders", json=order_body(quote["quote_id"], token)).status_code == 403


def test_expired_token_is_rejected(demo):
    client, headers, db_path = demo
    quote = make_quote(client)
    token = approve(client, headers, quote["quote_id"])
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE reviews SET expires_at = 0")
    assert client.post("/work-orders", json=order_body(quote["quote_id"], token)).status_code == 403


def test_duplicate_retry_returns_same_order_and_conflicting_key_fails(demo):
    client, headers, _ = demo
    quote = make_quote(client)
    token = approve(client, headers, quote["quote_id"])
    body = order_body(quote["quote_id"], token)
    first = client.post("/work-orders", json=body)
    retry = client.post("/work-orders", json=body)
    assert first.status_code == retry.status_code == 200 and first.json() == retry.json()
    different_token_same_key = {**body, "approval_token": "different-token"}
    assert client.post("/work-orders", json=different_token_same_key).status_code == 409
    assert client.post("/work-orders", json=order_body(quote["quote_id"], token, key="another-create-key")).status_code == 403
    metrics = client.get("/metrics").json()
    assert metrics["work_order_count"] == 1 and metrics["failed_request_count"] == 2
    assert metrics["request_count"] >= 6 and metrics["mean_duration_ms"] >= 0


def test_same_request_id_cannot_create_two_work_orders(demo):
    client, headers, _ = demo
    first = make_quote(client)
    first_token = approve(client, headers, first["quote_id"])
    assert client.post("/work-orders", json=order_body(first["quote_id"], first_token)).status_code == 200
    revised = make_quote(client, editing_hours=1)
    revised_token = approve(client, headers, revised["quote_id"])
    assert client.post("/work-orders", json=order_body(revised["quote_id"], revised_token, key="revised-create-key")).status_code == 409


def test_order_and_approval_survive_api_restart(demo):
    client, headers, db_path = demo
    quote = make_quote(client)
    token = approve(client, headers, quote["quote_id"])
    body = order_body(quote["quote_id"], token)
    restarted = TestClient(create_app(db_path, reviewer_key=headers["X-Reviewer-Key"]))
    response = restarted.post("/work-orders", json=body)
    assert response.status_code == 200
    again = TestClient(create_app(db_path, reviewer_key=headers["X-Reviewer-Key"]))
    assert again.post("/work-orders", json=body).json() == response.json()
    order_id = response.json()["order_id"]
    assert again.get(f"/work-orders/{order_id}").json()["quote"]["total_sar"] == 2240


def test_concurrent_retries_create_exactly_one_order(demo):
    client, headers, db_path = demo
    quote = make_quote(client)
    token = approve(client, headers, quote["quote_id"])
    body = order_body(quote["quote_id"], token, key="concurrent-create-key")
    workers = [TestClient(create_app(db_path, reviewer_key=headers["X-Reviewer-Key"])) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda worker: worker.post("/work-orders", json=body), workers))
    assert all(response.status_code == 200 for response in responses)
    assert len({response.json()["order_id"] for response in responses}) == 1
    assert client.get("/metrics").json()["work_order_count"] == 1


def test_unknown_quote_and_bad_query_date_fail_cleanly(demo):
    client, _, _ = demo
    assert client.get("/quotes/q_missing").status_code == 404
    assert client.get("/availability", params={"production_date": "not-a-date", "duration_hours": 2, "camera_count": 1}).status_code == 422
