"""Synthetic pricing, review tokens, and idempotent work-order storage.

The web gateway performs human reviews. n8n receives only planning tools.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from storage import connect as database_connect, database_errors


LOGGER = logging.getLogger("erp_demo.audit")
LOGGER.setLevel(logging.INFO)
if not LOGGER.handlers:
    audit_handler = logging.StreamHandler()
    audit_handler.setFormatter(logging.Formatter("%(message)s"))
    LOGGER.addHandler(audit_handler)
LOGGER.propagate = False
PRICE_BOOK_VERSION = "synthetic-sar-v1"
SERVICES = [
    {"code": "studio", "name": "Synthetic studio", "unit": "hour", "unit_price_sar": 350},
    {"code": "camera", "name": "Synthetic camera package", "unit": "camera-hour", "unit_price_sar": 200},
    {"code": "audio", "name": "Synthetic audio package", "unit": "hour", "unit_price_sar": 120},
    {"code": "editing", "name": "Synthetic editing service", "unit": "hour", "unit_price_sar": 250},
]
RATES = {service["code"]: service["unit_price_sar"] for service in SERVICES}


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def validate_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValueError("production_date must be YYYY-MM-DD") from None
    if parsed.isoformat() != value:
        raise ValueError("production_date must be YYYY-MM-DD")
    if not date.today() <= parsed <= date.today() + timedelta(days=366):
        raise ValueError("production_date must be today or within the next 366 days")
    return value


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QuoteInput(StrictBody):
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{4,80}$")
    brief: str = Field(min_length=5, max_length=4000)
    production_date: str
    duration_hours: int = Field(strict=True, ge=1, le=8)
    camera_count: int = Field(strict=True, ge=1, le=3)
    include_audio: bool = Field(strict=True)
    editing_hours: int = Field(strict=True, ge=0, le=8)

    _validate_date = field_validator("production_date")(validate_date)

    @field_validator("brief")
    @classmethod
    def trim_brief(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 5:
            raise ValueError("brief must contain at least five non-whitespace characters")
        return value


class VerifyInput(StrictBody):
    quote_id: str = Field(min_length=4, max_length=80)
    claimed_total_sar: Decimal = Field(ge=0, allow_inf_nan=False)


class ReviewInput(StrictBody):
    quote_id: str = Field(min_length=4, max_length=80)
    decision: Literal["approved", "denied"]


class OrderInput(StrictBody):
    quote_id: str = Field(min_length=4, max_length=80)
    approval_token: str = Field(min_length=10, max_length=200)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{4,120}$")


def availability(production_date: str, duration_hours: int, camera_count: int, include_audio: bool) -> dict:
    unavailable = date.fromisoformat(production_date).day == 15 or camera_count > 2
    reason = "Resources available in synthetic demo inventory"
    if date.fromisoformat(production_date).day == 15:
        reason = "Synthetic maintenance: the studio is unavailable on day 15 of each month"
    elif camera_count > 2:
        reason = "Only two synthetic camera packages are available"
    return {
        "synthetic": True, "production_date": production_date, "available": not unavailable,
        "reason": reason,
        "resources": {"studio_hours_capacity": 8, "camera_packages_capacity": 2, "audio_packages_capacity": 1},
        "requested": {"duration_hours": duration_hours, "camera_count": camera_count, "include_audio": include_audio},
    }


def create_app(db_path: str | Path | None = None, reviewer_key: str | None = None,
               *, database_url: str | None = None, database_prefix='production_desk') -> FastAPI:
    database_path = Path(db_path or os.environ.get("ERP_DEMO_DB") or Path(__file__).parent / "data" / "demo.sqlite3")
    database_path.parent.mkdir(parents=True, exist_ok=True)
    configured_key = reviewer_key if reviewer_key is not None else os.environ.get("DEMO_REVIEWER_KEY", "")
    token_lifetime = 1800  # Local demo review tokens expire after 30 minutes.

    @contextmanager
    def connect():
        with database_connect(database_path, 'erp', database_url, database_prefix) as connection:
            yield connection

    with connect() as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS quotes (
                quote_id TEXT PRIMARY KEY, request_id TEXT NOT NULL,
                quote_fingerprint TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reviews (
                review_id TEXT PRIMARY KEY, quote_id TEXT NOT NULL REFERENCES quotes(quote_id),
                decision TEXT NOT NULL, token_hash TEXT UNIQUE, expires_at REAL,
                revoked INTEGER NOT NULL DEFAULT 0, consumed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS work_orders (
                order_id TEXT PRIMARY KEY, quote_id TEXT NOT NULL REFERENCES quotes(quote_id),
                request_id TEXT UNIQUE NOT NULL, idempotency_key TEXT UNIQUE NOT NULL,
                payload_fingerprint TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS request_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                correlation_id TEXT NOT NULL, request_id TEXT, path TEXT NOT NULL,
                method TEXT NOT NULL, status_code INTEGER NOT NULL, duration_ms REAL NOT NULL,
                created_at TEXT NOT NULL
            );
        """)

    application = FastAPI(
        title="Synthetic Production ERP Demo", version="1.0.0",
        description="Local portfolio demo using synthetic data and SAR prices. No real company integration.",
    )
    application.state.database_path = str(database_path)

    def load_quote(connection: sqlite3.Connection, quote_id: str) -> dict:
        row = connection.execute("SELECT payload FROM quotes WHERE quote_id = ?", (quote_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Quote not found")
        return json.loads(row["payload"])

    @application.middleware("http")
    async def audit(request: Request, call_next):
        started = time.perf_counter()
        correlation_id = request.headers.get("X-Request-ID", "")[:120] or uuid.uuid4().hex
        request.state.business_request_id = None
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = correlation_id
            return response
        finally:
            event = {
                "correlation_id": correlation_id, "request_id": request.state.business_request_id,
                "path": request.url.path, "method": request.method, "status_code": status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3), "created_at": timestamp(),
            }
            # Logs deliberately omit brief, request bodies, headers and approval tokens.
            LOGGER.info(canonical_json(event))
            try:
                with connect() as connection:
                    connection.execute(
                        "INSERT INTO request_events (correlation_id, request_id, path, method, status_code, duration_ms, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        tuple(event[key] for key in ("correlation_id", "request_id", "path", "method", "status_code", "duration_ms", "created_at")),
                    )
            except database_errors():
                LOGGER.exception("Could not persist request metrics")

    @application.get("/health")
    def health():
        return {"status": "ok", "synthetic": True, "reviewer_configured": bool(configured_key)}

    @application.get("/catalog")
    def catalog():
        return {
            "synthetic": True, "currency": "SAR", "price_book_version": PRICE_BOOK_VERSION,
            "services": SERVICES,
            "capacity": {"studio_hours": 8, "camera_packages": 2, "audio_packages": 1},
            "availability_policy": "Synthetic maintenance on day 15 of each month; dates today through 366 days ahead. Work orders do not reserve real inventory.",
        }

    @application.get("/availability")
    def get_availability(
        production_date: str,
        duration_hours: int = Query(ge=1, le=8),
        camera_count: int = Query(ge=1, le=3),
        include_audio: bool = True,
    ):
        try:
            validate_date(production_date)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        return availability(production_date, duration_hours, camera_count, include_audio)

    @application.post("/quotes")
    def quote(body: QuoteInput, request: Request):
        fields = body.model_dump()
        request.state.business_request_id = body.request_id
        quote_fingerprint = fingerprint({"fields": fields, "price_book_version": PRICE_BOOK_VERSION})
        quantities = {"studio": body.duration_hours, "camera": body.duration_hours * body.camera_count,
                      "audio": body.duration_hours if body.include_audio else 0, "editing": body.editing_hours}
        line_items = [
            {"code": code, "quantity": quantity, "unit_price_sar": RATES[code], "line_total_sar": quantity * RATES[code]}
            for code, quantity in quantities.items() if quantity
        ]
        resource_status = availability(body.production_date, body.duration_hours, body.camera_count, body.include_audio)
        canonical_quote = {
            "quote_id": "q_" + uuid.uuid4().hex, "request_id": body.request_id, "fields": fields,
            "line_items": line_items, "total_sar": sum(item["line_total_sar"] for item in line_items),
            "currency": "SAR", "synthetic": True, "price_book_version": PRICE_BOOK_VERSION,
            "available": resource_status["available"], "availability_reason": resource_status["reason"],
            "quote_fingerprint": quote_fingerprint, "created_at": timestamp(),
        }
        with connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT payload FROM quotes WHERE quote_fingerprint = ?", (quote_fingerprint,)).fetchone()
            if existing:
                connection.commit()
                return json.loads(existing["payload"])
            connection.execute(
                "INSERT INTO quotes VALUES (?, ?, ?, ?, ?)",
                (canonical_quote["quote_id"], body.request_id, quote_fingerprint, canonical_json(canonical_quote), canonical_quote["created_at"]),
            )
            connection.commit()
        return canonical_quote

    @application.get("/quotes/{quote_id}")
    def get_quote(quote_id: str, request: Request):
        with connect() as connection:
            canonical_quote = load_quote(connection, quote_id)
        request.state.business_request_id = canonical_quote["request_id"]
        return canonical_quote

    @application.post("/quotes/verify")
    def verify_quote(body: VerifyInput, request: Request):
        with connect() as connection:
            canonical_quote = load_quote(connection, body.quote_id)
        request.state.business_request_id = canonical_quote["request_id"]
        if body.claimed_total_sar != Decimal(canonical_quote["total_sar"]):
            raise HTTPException(409, "Claimed total does not match the authoritative quote")
        return {"verified": True, "quote": canonical_quote}

    @application.post("/reviews")
    def review(body: ReviewInput, request: Request, x_reviewer_key: str | None = Header(default=None)):
        if not configured_key:
            raise HTTPException(503, "Set DEMO_REVIEWER_KEY locally before accepting human reviews")
        if not x_reviewer_key or not hmac.compare_digest(x_reviewer_key, configured_key):
            raise HTTPException(403, "Trusted reviewer credential required")
        with connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            canonical_quote = load_quote(connection, body.quote_id)
            request.state.business_request_id = canonical_quote["request_id"]
            if body.decision == "approved" and not canonical_quote["available"]:
                raise HTTPException(409, "Unavailable resources cannot be approved")
            # A later denial or approval replaces older review tokens for this quote.
            connection.execute("UPDATE reviews SET revoked = 1 WHERE quote_id = ?", (body.quote_id,))
            approval_token = secrets.token_urlsafe(32) if body.decision == "approved" else None
            review_id = "rv_" + uuid.uuid4().hex
            connection.execute(
                "INSERT INTO reviews (review_id, quote_id, decision, token_hash, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (review_id, body.quote_id, body.decision, fingerprint(approval_token) if approval_token else None,
                 time.time() + token_lifetime if approval_token else None, timestamp()),
            )
            connection.commit()
        return {"review_id": review_id, "quote_id": body.quote_id, "decision": body.decision,
                "approval_token": approval_token, "expires_in_seconds": token_lifetime if approval_token else None}

    @application.post("/work-orders")
    def create_order(body: OrderInput, request: Request):
        payload_fingerprint = fingerprint({"quote_id": body.quote_id, "approval_token_hash": fingerprint(body.approval_token)})
        with connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT payload_fingerprint, payload FROM work_orders WHERE idempotency_key = ?", (body.idempotency_key,)).fetchone()
            if existing:
                if existing["payload_fingerprint"] != payload_fingerprint:
                    raise HTTPException(409, "Idempotency key was already used with different fields")
                result = json.loads(existing["payload"])
                request.state.business_request_id = result["request_id"]
                connection.commit()
                return result
            canonical_quote = load_quote(connection, body.quote_id)
            request.state.business_request_id = canonical_quote["request_id"]
            review_row = connection.execute(
                "SELECT * FROM reviews WHERE token_hash = ? AND quote_id = ? AND decision = 'approved' AND revoked = 0 AND consumed = 0 AND expires_at > ?",
                (fingerprint(body.approval_token), body.quote_id, time.time()),
            ).fetchone()
            if review_row is None:
                raise HTTPException(403, "Valid unconsumed approval token for this exact quote required")
            try:
                validate_date(canonical_quote["fields"]["production_date"])
            except ValueError:
                raise HTTPException(409, "Quote date is no longer valid; create a new quote") from None
            if not canonical_quote["available"]:
                raise HTTPException(409, "Unavailable resources cannot create a work order")
            if connection.execute("SELECT 1 FROM work_orders WHERE request_id = ?", (canonical_quote["request_id"],)).fetchone():
                raise HTTPException(409, "A work order already exists for this request_id")
            result = {"order_id": "wo_" + uuid.uuid4().hex, "quote_id": body.quote_id,
                      "request_id": canonical_quote["request_id"], "status": "created", "synthetic": True,
                      "quote": canonical_quote, "created_at": timestamp()}
            connection.execute("UPDATE reviews SET consumed = 1 WHERE review_id = ?", (review_row["review_id"],))
            connection.execute(
                "INSERT INTO work_orders VALUES (?, ?, ?, ?, ?, ?, ?)",
                (result["order_id"], body.quote_id, result["request_id"], body.idempotency_key,
                 payload_fingerprint, canonical_json(result), result["created_at"]),
            )
            connection.commit()
        return result

    @application.get("/work-orders/{order_id}")
    def get_order(order_id: str, request: Request):
        with connect() as connection:
            row = connection.execute("SELECT payload FROM work_orders WHERE order_id = ?", (order_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Work order not found")
        result = json.loads(row["payload"])
        request.state.business_request_id = result["request_id"]
        return result

    @application.get("/metrics")
    def metrics():
        with connect() as connection:
            summary = connection.execute(
                "SELECT COUNT(*) request_count, COALESCE(SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END), 0) failed_request_count, COALESCE(AVG(duration_ms), 0) mean_duration_ms FROM request_events WHERE path NOT IN ('/health', '/metrics')"
            ).fetchone()
            endpoints = connection.execute(
                "SELECT path, COUNT(*) request_count, ROUND(CAST(AVG(duration_ms) AS NUMERIC), 3) mean_duration_ms FROM request_events WHERE path NOT IN ('/health', '/metrics') GROUP BY path ORDER BY path"
            ).fetchall()
            quote_count = connection.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
            review_counts = connection.execute("SELECT decision, COUNT(*) count FROM reviews GROUP BY decision").fetchall()
            order_count = connection.execute("SELECT COUNT(*) FROM work_orders").fetchone()[0]
        return {"synthetic": True, "currency": "SAR", **dict(summary), "endpoints": [dict(row) for row in endpoints],
                "quote_count": quote_count, "review_counts": {row["decision"]: row["count"] for row in review_counts},
                "work_order_count": order_count,
                "note": "Measured local API requests only. These are not real-world business results."}

    return application
