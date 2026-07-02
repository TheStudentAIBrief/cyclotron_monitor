"""
PET Lab Cloud API — FastAPI backend for the mobile app.

Dev:        uvicorn api.main:app --reload --port 8000
Production: uvicorn api.main:app --host 0.0.0.0 --port 8000
"""
import base64
import hashlib
import os
import re
import threading
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import OAuth2PasswordRequestForm

from api.auth import (
    authenticate, clear_refresh_cookie, create_tokens, ensure_bootstrap_credentials,
    get_current_user, get_refresh_payload, revoke_token, set_refresh_cookie,
)
from api.config import get_config
from api.db_cloud import init_cloud_tables
from api.routes import admin_import, ask, dashboard, gauges, petrace, push, records, scan, sync


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_config()
    init_cloud_tables(cfg['db_path'])
    # No-op if data/.credentials.json already exists or BOOTSTRAP_USERNAME/
    # BOOTSTRAP_PASSWORD aren't set — only matters on a fresh disk (new deploy)
    # where setup_credentials.py's interactive prompt has no terminal to run in.
    ensure_bootstrap_credentials(cfg['db_path'])
    yield


# docs/openapi disabled — the API is internal; an unauthenticated schema would let
# anyone on the network enumerate every route and model.
app = FastAPI(title='PET Lab API', version='1.0.0', lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)

# Restrict CORS. Native mobile clients send no Origin header, so they are unaffected;
# browser clients must be allow-listed via CORS_ALLOW_ORIGINS (comma-separated). Defaults
# to local dev origins only — never the '*' wildcard.
_cors = os.environ.get('CORS_ALLOW_ORIGINS', '').strip()
_allowed_origins = (
    [o.strip() for o in _cors.split(',') if o.strip()]
    if _cors else ['http://localhost:8081', 'http://localhost:19006']
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=['GET', 'POST'],
    allow_headers=['Authorization', 'Content-Type'],
    # Needed for the web refresh-token cookie (set_refresh_cookie): the browser
    # only attaches/accepts cookies on a cross-origin fetch when the server
    # opts in here. Safe with an explicit allow-list (never '*', enforced
    # above) -- allow_credentials with a wildcard origin is what's actually
    # dangerous, and this app never does that.
    allow_credentials=True,
)

# Reject oversized request bodies early (before parsing) to bound the memory/disk DoS
# surface on the photo, EUR-form, and CSV-import endpoints. Override via MAX_REQUEST_BYTES.
_MAX_BODY_BYTES = int(os.environ.get('MAX_REQUEST_BYTES', str(25 * 1024 * 1024)))


@app.middleware('http')
async def _limit_request_body(request: Request, call_next):
    cl = request.headers.get('content-length')
    if cl is not None:
        try:
            too_big = int(cl) > _MAX_BODY_BYTES
        except ValueError:
            return JSONResponse({'detail': 'Invalid Content-Length'}, status_code=400)
        if too_big:
            return JSONResponse({'detail': 'Request body too large'}, status_code=413)
    return await call_next(request)


@app.middleware('http')
async def _replace_server_header(request: Request, call_next):
    # Default uvicorn "Server" header discloses the framework, aiding CVE targeting.
    response = await call_next(request)
    response.headers['Server'] = 'petbms'
    return response


@app.middleware('http')
async def _security_headers(request: Request, call_next):
    # This app serves the login page and every facility dashboard directly (the
    # installable PWA, mounted below), so clickjacking/MIME-sniffing/downgrade
    # protections apply to it, not just the JSON API. `_CSP_HEADER` is computed
    # once at import time, after `_WEB_BUILD_DIR` is defined further down this
    # file -- Python resolves the module-global lookup at call time, so this is
    # safe even though it's assigned later in the file (never before the first
    # real request, since the module always finishes loading first).
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    response.headers['Content-Security-Policy'] = _CSP_HEADER
    return response


@app.get('/health')
def health():
    return {'status': 'ok'}


# In-process per-IP sliding-window limiter, same shape as serve.py's _rate_ok,
# scoped to /auth/login only — bounds brute-force/credential-stuffing attempts.
# Not shared across multiple server processes; fine for this app's single-instance
# deployment (Render free tier, local dev).
_LOGIN_MAX_ATTEMPTS_PER_MINUTE = int(os.environ.get('LOGIN_RATE_LIMIT_PER_MIN', '10'))
_login_rate_lock = threading.Lock()
_login_rate_counts: dict = defaultdict(lambda: (0, 0.0))


def _login_rate_ok(ip: str) -> bool:
    with _login_rate_lock:
        count, window_start = _login_rate_counts[ip]
        now = time.monotonic()
        if now - window_start > 60.0:
            _login_rate_counts[ip] = (1, now)
            return True
        if count >= _LOGIN_MAX_ATTEMPTS_PER_MINUTE:
            return False
        _login_rate_counts[ip] = (count + 1, window_start)
        return True


@app.post('/auth/login')
def login(request: Request, response: Response, form: OAuth2PasswordRequestForm = Depends()):
    client_ip = request.client.host if request.client else 'unknown'
    if not _login_rate_ok(client_ip):
        raise HTTPException(status_code=429, detail='Too many login attempts. Try again later.')
    if not authenticate(form.username, form.password):
        raise HTTPException(status_code=401, detail='Invalid credentials')
    cfg = get_config()
    tokens = create_tokens(form.username, cfg.get('lab_id', 'default'))
    # Mirrors the refresh token into an httpOnly cookie for web (see api/auth.py's
    # set_refresh_cookie docstring) -- native clients ignore Set-Cookie and keep
    # using the JSON body's refresh_token via SecureStore, unaffected.
    set_refresh_cookie(response, tokens['refresh_token'])
    return tokens


@app.post('/auth/refresh')
def refresh_token(response: Response, payload: dict = Depends(get_refresh_payload)):
    tokens = create_tokens(payload['sub'], payload['lab_id'])
    set_refresh_cookie(response, tokens['refresh_token'])
    return tokens


@app.post('/auth/logout')
def logout(response: Response, payload: dict = Depends(get_current_user)):
    # Revokes the access token used to call this endpoint. A stolen/leaked token
    # is no longer usable after this, instead of remaining valid until its
    # natural expiry (previously up to 30 days for a refresh token, no way to
    # invalidate it early at all).
    revoke_token(payload)
    clear_refresh_cookie(response)
    return {'status': 'ok'}


# dashboard/gauges/records/push/ask/petrace each already declare their own
# `user: dict = Depends(get_current_user)` per route (they need the returned
# user dict for lab_id/username) — adding it again here would just decode the
# same JWT twice per request. admin_import's routes take no such parameter, so
# it's the one router that still needs an explicit guard here, or its 6
# database-write endpoints would be reachable with no authentication at all.
app.include_router(dashboard.router, prefix='/api')
app.include_router(gauges.router,    prefix='/api')
app.include_router(records.router,   prefix='/api')
app.include_router(push.router,      prefix='/api')
app.include_router(ask.router,       prefix='/api')
app.include_router(petrace.router,   prefix='/api')
app.include_router(admin_import.router, prefix='/api', dependencies=[Depends(get_current_user)])

# Sync endpoint is protected by X-Sync-Key header (not JWT) — server-to-server only.
app.include_router(sync.router, prefix='')

# Scan endpoint is deliberately unauthenticated (no JWT) — QR-code scanners have no
# way to log in first.
app.include_router(scan.router, prefix='')

# Serve the Expo web export (the installable PWA) from the same Render deployment.
# mobile/dist is committed to the repo (built locally with `npx expo export
# --platform web`, not at deploy time — Render's Python runtime bundles an old
# Node, too old for Expo SDK 54's tooling) — registered last, and only if present,
# so its catch-all "/{full_path}" doesn't shadow the API routes above (they're
# matched first, in registration order) and doesn't break environments where
# mobile/dist hasn't been built/committed yet.
_WEB_BUILD_DIR = Path(__file__).parent.parent / 'mobile' / 'dist'


def _inline_script_csp_sources(html_path: Path) -> str:
    """CSP script-src hash sources for every inline (no `src=`) <script> in the
    built PWA's index.html -- e.g. Expo Router's tiny hydration-flag script.
    Computed from the actual file on disk (not hardcoded) so a future `npx expo
    export` rebuild that changes this content doesn't silently break the page
    by making the hash stop matching."""
    try:
        html_text = html_path.read_text(encoding='utf-8')
    except OSError:
        return ''
    sources = []
    for m in re.finditer(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', html_text, re.S):
        content = m.group(1)
        if content.strip():
            digest = hashlib.sha256(content.encode('utf-8')).digest()
            sources.append("'sha256-" + base64.b64encode(digest).decode('ascii') + "'")
    return ' '.join(sources)


_inline_script_sources = _inline_script_csp_sources(_WEB_BUILD_DIR / 'index.html')
_CSP_HEADER = (
    "default-src 'self'; "
    f"script-src 'self'{(' ' + _inline_script_sources) if _inline_script_sources else ''}; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'"
)

if _WEB_BUILD_DIR.is_dir():
    _WEB_BUILD_DIR_RESOLVED = _WEB_BUILD_DIR.resolve()

    @app.get('/{full_path:path}')
    async def serve_web(full_path: str):
        # docs/openapi are intentionally disabled above (docs_url=None etc.) — FastAPI
        # then has no route for these, so without this guard they'd fall through to
        # the catch-all below and get served the app shell with a 200 instead of a 404,
        # defeating the "don't let anyone enumerate routes/models" hardening.
        if full_path in ('openapi.json', 'docs', 'redoc'):
            raise HTTPException(status_code=404)

        # Expo's static web export ("output": "static") emits one <route>.html file
        # per screen (e.g. gauges.html, records.html) rather than a single index.html
        # SPA shell. StaticFiles(html=True) only serves index.html for directory URLs,
        # so a fresh page load / browser refresh at a client route like /gauges 404s.
        # Resolve extensionless route paths to their generated <route>.html here, and
        # fall back to index.html for anything else (client-side router then decides,
        # e.g. rendering the not-found screen).
        candidate = (_WEB_BUILD_DIR / full_path).resolve()
        if candidate != _WEB_BUILD_DIR_RESOLVED and _WEB_BUILD_DIR_RESOLVED not in candidate.parents:
            # Path traversal attempt (e.g. "../../etc/passwd") — refuse, don't serve app shell.
            raise HTTPException(status_code=404)

        if candidate.is_file():
            return FileResponse(candidate)

        html_candidate = _WEB_BUILD_DIR / f'{full_path}.html'
        if html_candidate.is_file():
            return FileResponse(html_candidate)

        index = _WEB_BUILD_DIR / 'index.html'
        if index.is_file():
            return FileResponse(index)
        raise HTTPException(status_code=404)
