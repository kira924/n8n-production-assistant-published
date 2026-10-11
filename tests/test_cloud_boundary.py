import asyncio
import json

import httpx
from fastapi.testclient import TestClient

from deploy.cloud_runtime import create_app


def gateway(handler):
    ready = asyncio.Event()
    ready.set()
    # No lifespan context: exercise the boundary without booting private services.
    return TestClient(create_app(httpx.MockTransport(handler), ready))


def test_only_visitor_routes_forward_and_client_headers_are_replaced():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'csrf_token': 'example'}, headers={'Set-Cookie': 'production_session=opaque; HttpOnly; Secure; SameSite=Strict'})

    client = gateway(handler)
    for path in ['/internal/erp/catalog', '/webhook/production-plan', '/rest/credentials', '/api/../internal/erp/catalog', '/api/internal/erp/catalog', '/api/jobs/other']:
        assert client.get(path).status_code == 404
    response = client.get('/api/session', headers={'X-Workflow-Key': 'forged', 'X-Real-IP': 'spoofed', 'X-Forwarded-For': '198.51.100.2'})
    assert response.status_code == 200
    assert len(calls) == 1
    assert 'x-workflow-key' not in calls[0].headers
    assert calls[0].headers['x-real-ip'] == '198.51.100.2'
    assert 'HttpOnly' in response.headers['set-cookie']
    assert response.headers['cache-control'] == 'no-store'


def test_submission_stays_in_flight_until_persisted_planning_result():
    calls = []
    job = 'job_' + 'a' * 32

    def handler(request):
        calls.append(request.url.path)
        if request.method == 'POST':
            return httpx.Response(202, json={'id': job, 'status': 'planning'})
        return httpx.Response(200, json={'id': job, 'status': 'awaiting_review'})

    response = gateway(handler).post('/api/jobs', json={'brief': 'synthetic'}, headers={'Cookie': 'production_session=opaque', 'Origin': 'https://example.org', 'X-CSRF-Token': 'csrf'})
    assert response.status_code == 202
    assert response.json()['id'] == job
    assert calls == ['/api/jobs', '/api/jobs/' + job]


def test_oversized_body_never_reaches_private_service():
    def handler(request):
        raise AssertionError('Private service must not be called')

    assert gateway(handler).post('/api/jobs', content=b'x' * 20_001).status_code == 413
