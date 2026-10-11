"""HTTP-only n8n demo container with durable state and a private service boundary.

The public request remains in flight while n8n plans, so scale-to-zero platforms
do not treat the automation as untracked background work. No editor is exposed.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
API = 'http://127.0.0.1:8000'
N8N = 'http://127.0.0.1:5678'
READY = asyncio.Event()
PROCESSES = []
FAILED = False
CSP = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"


def configure():
    import psycopg
    from psycopg import sql
    required = ('DATABASE_URL', 'GROQ_API_KEY', 'WORKFLOW_SERVICE_TOKEN',
                'REVIEWER_SECRET', 'SESSION_SECRET', 'N8N_ENCRYPTION_KEY', 'PUBLIC_ORIGIN')
    if not all(os.environ.get(name) for name in required):
        raise RuntimeError('Missing cloud configuration')
    prefix = os.environ.get('DATABASE_PREFIX', 'production_desk')
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,40}', prefix):
        raise RuntimeError('Invalid database prefix')
    direct_url = os.environ.get('DATABASE_URL_UNPOOLED', os.environ['DATABASE_URL'])
    url = urlsplit(direct_url)
    ssl_enabled = 'sslmode=require' in direct_url or 'sslmode=verify' in direct_url
    env = {**os.environ,
        'DB_TYPE': 'postgresdb', 'DB_POSTGRESDB_HOST': url.hostname or '',
        'DB_POSTGRESDB_PORT': str(url.port or 5432),
        'DB_POSTGRESDB_DATABASE': unquote(url.path.lstrip('/')),
        'DB_POSTGRESDB_USER': unquote(url.username or ''),
        'DB_POSTGRESDB_PASSWORD': unquote(url.password or ''),
        'DB_POSTGRESDB_SCHEMA': prefix + '_n8n', 'DB_POSTGRESDB_POOL_SIZE': '2',
        'DB_POSTGRESDB_SSL_ENABLED': str(ssl_enabled).lower(),
        'DB_POSTGRESDB_SSL_REJECT_UNAUTHORIZED': 'true',
        'N8N_LISTEN_ADDRESS': '127.0.0.1', 'N8N_HOST': 'localhost', 'N8N_PORT': '5678',
        'N8N_PROTOCOL': 'http', 'N8N_EDITOR_BASE_URL': N8N, 'WEBHOOK_URL': N8N + '/',
        'N8N_SECURE_COOKIE': 'false', 'N8N_DIAGNOSTICS_ENABLED': 'false',
        'N8N_PERSONALIZATION_ENABLED': 'false', 'N8N_VERSION_NOTIFICATIONS_ENABLED': 'false',
        'N8N_BLOCK_ENV_ACCESS_IN_NODE': 'true', 'N8N_RUNNERS_ENABLED': 'true',
        'EXECUTIONS_DATA_SAVE_ON_SUCCESS': 'none', 'EXECUTIONS_DATA_SAVE_ON_ERROR': 'none',
        'EXECUTIONS_DATA_SAVE_MANUAL_EXECUTIONS': 'false', 'EXECUTIONS_DATA_PRUNE': 'true',
        'N8N_LOG_LEVEL': 'error', 'GENERIC_TIMEZONE': 'Africa/Cairo', 'TZ': 'Africa/Cairo',
    }
    Path(env.get('N8N_USER_FOLDER', '/tmp/desk-home')).mkdir(parents=True, exist_ok=True)
    source = (ROOT / 'workflows/production-plan.json').read_text(encoding='utf-8')
    source = source.replace('http://api:8000', API)
    fingerprint = hashlib.sha256((source + '\0' + env['GROQ_API_KEY'] + '\0' +
                                  env['WORKFLOW_SERVICE_TOKEN']).encode()).hexdigest()
    # A session-level lock uses the direct endpoint, never a transaction pooler.
    with psycopg.connect(direct_url, autocommit=True, connect_timeout=30) as db:
        lock = int.from_bytes(hashlib.sha256((prefix + '_bootstrap').encode()).digest()[:4], 'big', signed=True)
        db.execute('SELECT pg_advisory_lock(%s)', (lock,))
        try:
            for suffix in ('gateway', 'erp', 'n8n', 'runtime'):
                db.execute(sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(prefix + '_' + suffix)))
            marker = sql.Identifier(prefix + '_runtime', 'configuration')
            db.execute(sql.SQL('CREATE TABLE IF NOT EXISTS {} (id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL)').format(marker))
            row = db.execute(sql.SQL('SELECT fingerprint FROM {} WHERE id=1').format(marker)).fetchone()
            if not row or row[0] != fingerprint:
                with tempfile.TemporaryDirectory(prefix='production-desk-') as folder:
                    credentials = Path(folder) / 'credentials.json'
                    credentials.write_text(json.dumps([
                        dict(id='productionDeskGroq', name='Production Desk Groq', type='groqApi', data=dict(apiKey=env['GROQ_API_KEY'])),
                        dict(id='productionDeskWorkflow', name='Production Desk Workflow', type='httpHeaderAuth',
                             data=dict(name='X-Workflow-Key', value=env['WORKFLOW_SERVICE_TOKEN'])),
                    ]), encoding='utf-8')
                    credentials.chmod(0o600)
                    workflow = Path(folder) / 'workflow.json'
                    workflow.write_text(source, encoding='utf-8')
                    for args in (
                        ['import:credentials', '--input=' + str(credentials)],
                        ['import:workflow', '--input=' + str(workflow)],
                        ['publish:workflow', '--id=productionDeskPublicPlan'],
                    ):
                        result = subprocess.run(['n8n', *args], env=env, cwd=ROOT,
                                                capture_output=True, timeout=240)
                        if result.returncode:
                            # CLI output may contain configuration. Never log it.
                            raise RuntimeError('n8n bootstrap failed at ' + args[0])
                db.execute(sql.SQL('INSERT INTO {} VALUES (1,%s) ON CONFLICT(id) DO UPDATE SET fingerprint=EXCLUDED.fingerprint').format(marker), (fingerprint,))
        finally:
            db.execute('SELECT pg_advisory_unlock(%s)', (lock,))
    # Keep Groq out of the Python service environment; only n8n needs it.
    api_env = {key: value for key, value in env.items() if key not in ('GROQ_API_KEY', 'DB_POSTGRESDB_PASSWORD')}
    api_env.update(DEMO_DATA_DIR='/tmp/desk-data', N8N_WEBHOOK_URL=N8N + '/webhook/production-plan')
    for args, child_env in (
        ([sys.executable, '-m', 'uvicorn', 'api.app:create_app', '--factory', '--host', '127.0.0.1', '--port', '8000', '--no-access-log'], api_env),
        (['n8n', 'start'], env),
    ):
        PROCESSES.append(subprocess.Popen(args, env=child_env, cwd=ROOT, start_new_session=True))


async def boot():
    global FAILED
    try:
        await asyncio.to_thread(configure)
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            for _ in range(120):
                if any(process.poll() is not None for process in PROCESSES):
                    raise RuntimeError('A private service stopped')
                try:
                    responses = await asyncio.gather(client.get(API + '/health'), client.get(N8N + '/healthz/readiness'))
                    if all(response.status_code == 200 for response in responses):
                        READY.set()
                        print('Production Desk services ready', flush=True)
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(1)
        raise RuntimeError('Services did not become ready')
    except Exception as error:
        FAILED = True
        # Intentionally omit exception messages, which may contain connection URLs.
        print('Production Desk startup failed (' + type(error).__name__ + ')', flush=True)


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(boot())
    yield
    task.cancel()
    for process in PROCESSES:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)


def create_app(transport=None, ready=READY):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.mount('/assets', StaticFiles(directory=ROOT / 'web/assets'), name='assets')

    @app.middleware('http')
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers.update({'Content-Security-Policy': CSP, 'X-Content-Type-Options': 'nosniff',
                                 'Referrer-Policy': 'same-origin', 'Cache-Control': 'no-store'})
        return response

    @app.get('/')
    async def index():
        return FileResponse(ROOT / 'web/index.html')

    @app.get('/health')
    async def health():
        return JSONResponse({'status': 'ok' if ready.is_set() else 'starting', 'synthetic': True},
                            status_code=200 if ready.is_set() else 503)

    @app.api_route('/api/{path:path}', methods=['GET', 'POST'])
    async def proxy(path: str, request: Request):
        # Only known visitor routes are forwarded; no URL normalization can open /internal.
        if not re.fullmatch(r'session|jobs|jobs/job_[a-f0-9]{32}(?:/decision)?', path):
            return JSONResponse({'detail': 'Not found'}, status_code=404)
        size = request.headers.get('content-length', '')
        if size and (not size.isdecimal() or int(size) > 20_000):
            return JSONResponse({'detail': 'الطلب أكبر من الحجم المسموح.'}, status_code=413)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 20_000:
                return JSONResponse({'detail': 'الطلب أكبر من الحجم المسموح.'}, status_code=413)
        try:
            await asyncio.wait_for(ready.wait(), timeout=90)
            forwarded = {name: request.headers[name] for name in ('cookie', 'origin', 'x-csrf-token', 'content-type') if name in request.headers}
            # Vercel overwrites this header with the direct client's public address.
            forwarded['x-real-ip'] = request.headers.get('x-forwarded-for', 'local').split(',')[0].strip()
            async with httpx.AsyncClient(base_url=API, transport=transport, timeout=30, trust_env=False) as client:
                response = await client.request(request.method, '/api/' + path, headers=forwarded, content=bytes(body))
                if path == 'jobs' and request.method == 'POST' and response.status_code == 202:
                    job = response.json()['id']
                    # Keep an actual platform request open until the persisted result exists.
                    for _ in range(185):
                        result = await client.get('/api/jobs/' + job, headers=forwarded)
                        if result.status_code != 200 or result.json()['status'] != 'planning':
                            break
                        await asyncio.sleep(1)
                output = Response(response.content, status_code=response.status_code,
                                  media_type=response.headers.get('content-type', 'application/json'))
                for cookie in response.headers.get_list('set-cookie'):
                    output.headers.append('set-cookie', cookie)
                return output
        except (httpx.HTTPError, asyncio.TimeoutError):
            return JSONResponse({'detail': 'الخدمة قيد التشغيل. انتظر قليلًا ثم حاول مجددًا.'}, status_code=503)
    return app


if __name__ == '__main__':
    uvicorn.run(create_app(), host='0.0.0.0', port=int(os.environ.get('PORT', '8080')), access_log=False)
