"""Fail-closed private beta behind a trusted, TLS authentication gateway."""
import hashlib
import hmac
import json
import re
import time
from urllib.parse import urlencode, urlsplit

from fastapi.responses import JSONResponse
from .parse_repository import bound_reports


def owned(metadata, actor):
    scopes = [metadata, metadata.get('task') or {}]
    owners = [s[k] for s in scopes for k in ('owner', 'user_id') if s.get(k)]
    return bool(owners) and all(x == actor for x in owners)


def install_public_gate(app, settings):
    if not settings.public_mode:
        return
    origin = urlsplit(settings.public_origin)
    if (origin.scheme != 'https' or not origin.hostname or origin.path or origin.query or origin.fragment
            or origin.username or origin.password or len(settings.proxy_secret) < 32):
        raise ValueError('Public mode requires an HTTPS origin and a proxy secret of at least 32 characters')
    if settings.harness_enabled or settings.extraction_model:
        raise ValueError('Private beta has a zero model budget: disable harness and extraction model')
    if settings.database_path == ':memory:' or settings.max_request_bytes < 1024 or settings.requests_per_minute < 1:
        raise ValueError('Public mode requires a persistent database and positive request limits')
    with app.state.tasks.transaction() as db:
        db.execute('CREATE TABLE IF NOT EXISTS public_requests (bucket TEXT, at REAL)')
        db.execute('CREATE INDEX IF NOT EXISTS public_requests_bucket ON public_requests(bucket,at)')

    def error(status, code):
        return JSONResponse(status_code=status, content=dict(ok=False, data=None,
                            error=dict(code=code, message=code, retryable=status == 429), task_id=None, revision=None))

    def owner_for(task_id, actor):
        task = app.state.tasks.get(task_id)
        return task is not None and owned(task.results, actor)

    @app.middleware('http')
    async def gate(request, call_next):
        async def respond():
            if request.url.path == '/health':
                return await call_next(request)
            # Gateways remove client-supplied identity headers and set both values after authentication.
            if not hmac.compare_digest(request.headers.get('x-esg-proxy-secret', '').encode(), settings.proxy_secret.encode()):
                return error(401, 'AUTHENTICATION_REQUIRED')
            actor = request.headers.get('x-esg-user', '')
            if not re.fullmatch(r'[A-Za-z0-9@_.+-]{1,128}', actor):
                return error(401, 'AUTHENTICATION_REQUIRED')
            if request.headers.get('host', '') != origin.netloc:
                return error(400, 'INVALID_HOST')
            now = time.time()
            bucket = hashlib.sha256(actor.encode()).hexdigest()
            with app.state.tasks.transaction() as db:
                db.execute('DELETE FROM public_requests WHERE at < ?', (now - 60,))
                count = db.execute('SELECT COUNT(*) FROM public_requests WHERE bucket=?', (bucket,)).fetchone()[0]
                if count >= settings.requests_per_minute:
                    return error(429, 'RATE_LIMITED')
                db.execute('INSERT INTO public_requests VALUES (?,?)', (bucket, now))
            path = request.url.path
            # First beta: no model-facing endpoint is reachable, even with a genuine agent token.
            if path.startswith('/agent') or path.startswith('/api/tasks/') or path.endswith('/agent-runs'):
                return error(403, 'MODEL_BUDGET_ZERO')
            if request.method not in ('GET', 'HEAD', 'OPTIONS'):
                if request.headers.get('origin') != settings.public_origin:
                    return error(403, 'ORIGIN_REQUIRED')
            try:
                length = int(request.headers.get('content-length', '0'))
                if length < 0 or length > settings.max_request_bytes:
                    return error(413, 'REQUEST_TOO_LARGE')
            except ValueError:
                return error(400, 'INVALID_LENGTH')
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > settings.max_request_bytes:
                    return error(413, 'REQUEST_TOO_LARGE')
                body.extend(chunk)
            request._body = bytes(body)
            # Existing user routes consume X-User; clients never control that identity in public mode.
            request.scope['headers'] = [(k, v) for k, v in request.scope['headers']
                                        if k not in (b'x-user', b'cookie', b'x-esg-proxy-secret', b'x-esg-user')]
            request.scope['headers'].append((b'x-user', actor.encode()))
            request.state.authenticated_user = actor
            match = re.match(r'^/tasks/([^/]+)', path)
            if match and not owner_for(match[1], actor):
                return error(404, 'NOT_FOUND')
            if path == '/':
                ids = request.query_params.getlist('task_id')
                if len(ids) > 1:
                    return error(400, 'DUPLICATE_TASK')
                if ids and not owner_for(ids[0], actor):
                    return error(404, 'NOT_FOUND')
                if not ids:
                    with app.state.tasks.transaction() as db:
                        candidates = list(db.execute('SELECT task_id,results FROM tasks'))
                    task_id = next((tid for tid, raw in candidates if owned(json.loads(raw), actor)), None)
                    if task_id is None:
                        return error(404, 'NO_ASSIGNED_TASK')
                    request.scope['query_string'] = urlencode({'task_id': task_id}).encode()
            match = re.fullmatch(r'/api/reports/([^/]+)/pdf', path)
            if match:
                with app.state.tasks.transaction() as db:
                    metadata = [json.loads(r[0]) for r in db.execute('SELECT results FROM tasks')]
                if not any(owned(m, actor) and match[1] in bound_reports(m) for m in metadata):
                    return error(404, 'NOT_FOUND')
            match = re.fullmatch(r'/api/artifacts/([^/]+)', path)
            if match:
                meta = app.state.artifacts.get_meta(match[1])
                if meta is None or not owner_for(meta['task_id'], actor):
                    return error(404, 'NOT_FOUND')
            return await call_next(request)
        response = await respond()
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                                 'Referrer-Policy': 'same-origin', 'X-Frame-Options': 'DENY',
                                 'Strict-Transport-Security': 'max-age=31536000',
                                 'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"})
        if request.url.path.startswith('/api/reports/') and response.headers.get('content-type', '').startswith('application/pdf'):
            response.headers['X-Frame-Options'] = 'SAMEORIGIN'
            response.headers['Content-Security-Policy'] = response.headers['Content-Security-Policy'].replace("frame-ancestors 'none'", "frame-ancestors 'self'")
        return response
