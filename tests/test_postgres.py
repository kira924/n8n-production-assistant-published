"""Run against an isolated schema on TEST_DATABASE_URL, never application tables."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
psycopg = pytest.importorskip('psycopg')
from psycopg import sql

from api.app import Settings, create_app
from tests.test_gateway import quote_and_report, sample, session, submit


@pytest.fixture
def durable(tmp_path):
    url = os.environ.get('TEST_DATABASE_URL')
    if not url:
        pytest.skip('TEST_DATABASE_URL is required for PostgreSQL integration tests')
    prefix = 'test_pd_' + uuid.uuid4().hex[:16]
    schemas = [prefix + '_' + suffix for suffix in ('gateway', 'erp')]
    with psycopg.connect(url, autocommit=True) as db:
        for name in schemas:
            db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(name)))
    captured = []

    async def dispatch(payload):
        captured.append(payload)

    config = Settings(tmp_path, 'http://testserver', 'w' * 40, 'r' * 40, 's' * 40,
                      database_url=url, database_prefix=prefix)
    try:
        yield config, dispatch, captured
    finally:
        with psycopg.connect(url, autocommit=True) as db:
            for name in schemas:
                assert name.startswith('test_pd_')
                db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(name)))


def test_orders_and_session_ownership_survive_application_restart(durable):
    config, dispatch, captured = durable
    app = create_app(config, dispatcher=dispatch)
    client = TestClient(app)
    headers = session(client)
    job = submit(client, headers)
    quote_and_report(client, config, captured[0])
    result = client.post('/api/jobs/' + job + '/decision', json={'decision': 'approved'}, headers=headers)
    assert result.status_code == 200, result.text
    order_id = result.json()['order']['order_id']
    restarted = create_app(config, dispatcher=dispatch)
    restored = TestClient(restarted)
    restored.cookies.update(client.cookies)
    assert restored.get('/api/jobs/' + job).json()['order']['order_id'] == order_id
    repeated = restored.post('/api/jobs/' + job + '/decision', json={'decision': 'approved'}, headers=headers)
    assert repeated.json()['order']['order_id'] == order_id
    stranger = TestClient(restarted)
    session(stranger)
    assert stranger.get('/api/jobs/' + job).status_code == 404
    metrics = TestClient(restarted.state.erp).get('/metrics')
    assert metrics.status_code == 200, metrics.text
    assert metrics.json()['work_order_count'] == 1


def test_concurrent_instances_share_one_planning_slot(durable):
    config, dispatch, _ = durable
    clients = [TestClient(create_app(config, dispatcher=dispatch)) for _ in range(4)]
    headers = [session(client) for client in clients]

    def submit_one(index):
        return clients[index].post('/api/jobs', json=sample(), headers=headers[index]).status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(submit_one, range(4)))
    assert results.count(202) == 1
    assert results.count(429) == 3
