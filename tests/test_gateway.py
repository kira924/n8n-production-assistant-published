import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from api.app import Settings, create_app


@pytest.fixture
def demo(tmp_path):
    captured = []
    async def dispatch(payload):
        captured.append(payload)
    clock_value = [1_800_000_000.0]
    config = Settings(tmp_path, 'http://testserver', 'w' * 40, 'r' * 40, 's' * 40)
    app = create_app(config, dispatcher=dispatch, clock=lambda: clock_value[0])
    return app, TestClient(app), captured, config, clock_value


def sample(**changes):
    day = date.today() + timedelta(days=2)
    if day.day == 15:
        day += timedelta(days=1)
    values = dict(brief='Synthetic interview production with two cameras.', production_date=day.isoformat(),
                  duration_hours=2, camera_count=2, include_audio=True, editing_hours=2, budget_sar=3000)
    return {**values, **changes}


def session(client):
    result = client.get('/api/session')
    assert result.status_code == 200
    return {'Origin': 'http://testserver', 'X-CSRF-Token': result.json()['csrf_token']}


def submit(client, headers, **changes):
    response = client.post('/api/jobs', json=sample(**changes), headers=headers)
    assert response.status_code == 202, response.text
    return response.json()['id']


def quote_and_report(client, config, payload, **changes):
    tool_headers = {'X-Workflow-Key': config.workflow_key}
    fields = {k: v for k, v in payload.items() if k != 'budget_sar'}
    quote = client.post('/internal/erp/quotes', json=fields, headers=tool_headers).json()
    body = dict(status='awaiting_review', quote_id=quote['quote_id'], claimed_total_sar=quote['total_sar'],
                brief_summary='Prepare the studio, capture clean sound, and edit the recording.',
                agent_tool_sequence=['catalog', 'availability', 'quote'])
    response = client.post('/internal/jobs/' + payload['request_id'] + '/result', json={**body, **changes}, headers=tool_headers)
    assert response.status_code == 200, response.text
    return quote


def test_session_cookie_is_http_only_and_owned_jobs_are_isolated(demo):
    app, client, captured, config, _ = demo
    initial = client.get('/api/session')
    assert 'HttpOnly' in initial.headers['set-cookie']
    assert 'SameSite=strict' in initial.headers['set-cookie']
    headers = session(client)
    job = submit(client, headers)
    other = TestClient(app)
    session(other)
    assert other.get('/api/jobs/' + job).status_code == 404
    assert other.post('/api/jobs/' + job + '/decision', json={'decision': 'approved'}, headers=session(other)).status_code == 404
    assert client.get('/api/jobs/' + job).json()['status'] == 'planning'


def test_csrf_and_origin_are_required(demo):
    _, client, _, _, _ = demo
    headers = session(client)
    assert client.post('/api/jobs', json=sample()).status_code == 403
    assert client.post('/api/jobs', json=sample(), headers={**headers, 'Origin': 'https://foreign.example'}).status_code == 403
    assert client.post('/api/jobs', json=sample(), headers={**headers, 'X-CSRF-Token': 'wrong'}).status_code == 403


def test_workflow_routes_require_key_and_cannot_approve(demo):
    _, client, _, config, _ = demo
    assert client.get('/internal/erp/catalog').status_code == 403
    tool_headers = {'X-Workflow-Key': config.workflow_key}
    assert client.get('/internal/erp/catalog', headers=tool_headers).status_code == 200
    assert client.post('/internal/erp/reviews', headers=tool_headers, json={}).status_code == 404
    assert client.post('/internal/erp/work-orders', headers=tool_headers, json={}).status_code == 404
    assert client.get('/internal/erp/metrics', headers=tool_headers).status_code == 404


def test_happy_path_requires_decision_and_duplicate_approval_is_idempotent(demo):
    _, client, captured, config, _ = demo
    headers = session(client)
    job = submit(client, headers)
    assert client.post('/api/jobs/' + job + '/decision', json={'decision': 'approved'}, headers=headers).status_code == 409
    quote = quote_and_report(client, config, captured[0])
    assert quote['total_sar'] == 2240
    assert client.get('/api/jobs/' + job).json()['status'] == 'awaiting_review'
    first = client.post('/api/jobs/' + job + '/decision', json={'decision': 'approved'}, headers=headers)
    repeated = client.post('/api/jobs/' + job + '/decision', json={'decision': 'approved'}, headers=headers)
    assert first.status_code == repeated.status_code == 200
    assert first.json()['status'] == 'created'
    assert first.json()['order']['order_id'] == repeated.json()['order']['order_id']
    assert 'approval_token' not in first.text
    assert client.post('/api/jobs/' + job + '/decision', json={'decision': 'denied'}, headers=headers).status_code == 409


def test_decline_does_not_create_an_order(demo):
    _, client, captured, config, _ = demo
    headers = session(client)
    job = submit(client, headers)
    quote_and_report(client, config, captured[0])
    result = client.post('/api/jobs/' + job + '/decision', json={'decision': 'denied'}, headers=headers)
    assert result.json()['status'] == 'denied' and 'order' not in result.json()


@pytest.mark.parametrize('changes', [{'budget_sar': 1000}, {'camera_count': 3}])
def test_budget_and_unavailable_quotes_cannot_be_approved(demo, changes):
    _, client, captured, config, _ = demo
    headers = session(client)
    job = submit(client, headers, **changes)
    quote_and_report(client, config, captured[0])
    assert client.get('/api/jobs/' + job).json()['status'] == 'rejected'
    assert client.post('/api/jobs/' + job + '/decision', json={'decision': 'approved'}, headers=headers).status_code == 409


def test_callback_cannot_bind_another_request_quote(demo):
    _, client, captured, config, _ = demo
    headers = session(client)
    job = submit(client, headers)
    wrong_payload = {**captured[0], 'request_id': 'job_' + 'f' * 32}
    fields = {k: v for k, v in wrong_payload.items() if k != 'budget_sar'}
    key = {'X-Workflow-Key': config.workflow_key}
    quote = client.post('/internal/erp/quotes', json=fields, headers=key).json()
    client.post('/internal/jobs/' + job + '/result', headers=key, json={'status': 'awaiting_review',
        'quote_id': quote['quote_id'], 'claimed_total_sar': 2240, 'agent_tool_sequence': ['catalog', 'availability', 'quote']})
    assert client.get('/api/jobs/' + job).json()['status'] == 'error'


@pytest.mark.parametrize('changes', [{'claimed_total_sar': 1}, {'agent_tool_sequence': ['quote']}, {'agent_tool_sequence': ['quote', 'catalog', 'availability']}])
def test_malformed_planning_result_fails_closed(demo, changes):
    _, client, captured, config, _ = demo
    headers = session(client)
    job = submit(client, headers)
    quote_and_report(client, config, captured[0], **changes)
    assert client.get('/api/jobs/' + job).json()['status'] == 'error'


def test_browser_cannot_send_quote_token_or_foreign_request_id(demo):
    _, client, _, _, _ = demo
    headers = session(client)
    assert client.post('/api/jobs', headers=headers, json=sample(request_id='chosen')).status_code == 422
    assert client.post('/api/jobs', headers=headers, json=sample(approval_token='fake-token')).status_code == 422
    assert client.post('/api/jobs', headers=headers, json=sample(budget_sar='3000')).status_code == 422
    assert client.post('/api/jobs', headers=headers, json=sample(production_date='2026-99-99')).status_code == 422


def test_expiry_and_planning_timeout(demo):
    _, client, _, _, now = demo
    headers = session(client)
    job = submit(client, headers)
    now[0] += 181
    assert client.get('/api/jobs/' + job).json()['status'] == 'error'
    now[0] += 1620
    assert client.get('/api/jobs/' + job).status_code == 410


def test_single_planning_slot_and_session_limit(demo):
    _, client, _, config, _ = demo
    headers = session(client)
    job = submit(client, headers)
    assert client.post('/api/jobs', headers=headers, json=sample()).status_code == 429
    assert client.get('/api/jobs/' + job).json()['status'] == 'planning'
    for index in range(config.session_limit):
        if index:
            job = submit(client, headers)
        result = client.post('/internal/jobs/' + job + '/result', headers={'X-Workflow-Key': config.workflow_key}, json={'status': 'error'})
        assert result.status_code == 200
    assert client.post('/api/jobs', headers=headers, json=sample()).status_code == 429


def test_callback_retry_cannot_undo_final_decision(demo):
    _, client, captured, config, _ = demo
    headers = session(client)
    job = submit(client, headers)
    quote_and_report(client, config, captured[0])
    client.post('/api/jobs/' + job + '/decision', json={'decision': 'denied'}, headers=headers)
    quote_and_report(client, config, captured[0])
    assert client.get('/api/jobs/' + job).json()['status'] == 'denied'


def test_unavailable_dispatch_reports_error_without_sensitive_details(tmp_path):
    async def broken(payload):
        raise RuntimeError('provider-key-or-internal-url')
    config = Settings(tmp_path, 'http://testserver', 'w' * 40, 'r' * 40, 's' * 40)
    client = TestClient(create_app(config, dispatcher=broken))
    headers = session(client)
    job = submit(client, headers)
    result = client.get('/api/jobs/' + job)
    assert result.json()['status'] == 'error'
    assert 'provider-key' not in result.text and 'internal-url' not in result.text


def test_large_request_is_rejected(demo):
    _, client, _, _, _ = demo
    headers = session(client)
    assert client.post('/api/jobs', headers=headers, json=sample(brief='x' * 21000)).status_code == 413


def test_internal_callback_requires_service_key(demo):
    _, client, _, _, _ = demo
    headers = session(client)
    job = submit(client, headers)
    assert client.post('/internal/jobs/' + job + '/result', json={'status': 'error'}).status_code == 403


def test_parallel_browser_decisions_create_only_one_order(demo):
    _, client, captured, config, _ = demo
    headers = session(client)
    job = submit(client, headers)
    quote_and_report(client, config, captured[0])
    def approve(_):
        return client.post('/api/jobs/' + job + '/decision', headers=headers, json={'decision': 'approved'})
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(approve, range(4)))
    assert all(response.status_code in (200, 409) for response in responses)
    assert any(response.status_code == 200 for response in responses)
    assert client.get('/api/jobs/' + job).json()['status'] == 'created'
    with sqlite3.connect(config.data_dir / 'erp.sqlite3') as conn:
        assert conn.execute('SELECT COUNT(*) FROM work_orders').fetchone()[0] == 1


def test_https_configuration_uses_secure_session_cookie(tmp_path):
    config = Settings(tmp_path, 'https://demo.example', 'w' * 40, 'r' * 40, 's' * 40, secure_cookies=True)
    client = TestClient(create_app(config), base_url='https://demo.example')
    cookie = client.get('/api/session').headers['set-cookie']
    assert 'Secure' in cookie and 'HttpOnly' in cookie and 'SameSite=strict' in cookie


def test_late_callback_cannot_reopen_expired_planning(demo):
    _, client, _, config, now = demo
    headers = session(client)
    job = submit(client, headers)
    now[0] += 181
    result = client.post('/internal/jobs/' + job + '/result', headers={'X-Workflow-Key': config.workflow_key}, json={'status': 'error'})
    assert result.status_code == 410
    assert client.get('/api/jobs/' + job).json()['status'] == 'error'
