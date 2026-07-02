"""
PET Lab Cloud API — FastAPI backend for the mobile app.

Dev:        uvicorn api.main:app --reload --port 8000
Production: uvicorn api.main:app --host 0.0.0.0 --port 8000
"""
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import OAuth2PasswordRequestForm

from api.auth import authenticate, create_tokens, get_current_user, get_refresh_payload
from api.config import get_config
from api.db_cloud import init_cloud_tables
from api.routes import ask, dashboard, gauges, petrace, push, records, scan, sync


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_config()
    init_cloud_tables(cfg['db_path'])
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


@app.get('/health')
def health():
    return {'status': 'ok'}


@app.post('/auth/login')
def login(form: OAuth2PasswordRequestForm = Depends()):
    if not authenticate(form.username, form.password):
        raise HTTPException(status_code=401, detail='Invalid credentials')
    cfg = get_config()
    return create_tokens(form.username, cfg.get('lab_id', 'default'))


@app.post('/auth/refresh')
def refresh_token(payload: dict = Depends(get_refresh_payload)):
    return create_tokens(payload['sub'], payload['lab_id'])


# All /api/* routes require a valid access token.
_auth = [Depends(get_current_user)]
app.include_router(dashboard.router, prefix='/api', dependencies=_auth)
app.include_router(gauges.router,    prefix='/api', dependencies=_auth)
app.include_router(records.router,   prefix='/api', dependencies=_auth)
app.include_router(push.router,      prefix='/api', dependencies=_auth)
app.include_router(ask.router,       prefix='/api', dependencies=_auth)
app.include_router(petrace.router,   prefix='/api', dependencies=_auth)

# Sync endpoint is protected by X-Sync-Key header (not JWT) — server-to-server only.
app.include_router(sync.router, prefix='')

# Scan endpoint is deliberately unauthenticated (no JWT) — QR-code scanners have no
# way to log in first.
app.include_router(scan.router, prefix='')

# Serve the Expo web export (the installable PWA) from the same Render deployment.
# Built by `npm run build:web` in mobile/ (output is gitignored, not present until
# built) — registered last, and only if present, so its catch-all "/{full_path}"
# doesn't shadow the API routes above (they're matched first, in registration order)
# and doesn't break environments that never ran the build.
_WEB_BUILD_DIR = Path(__file__).parent.parent / 'mobile' / 'dist'
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
