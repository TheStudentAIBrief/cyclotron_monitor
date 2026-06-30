"""
PET Lab Cloud API — FastAPI backend for the mobile app.

Dev:        uvicorn api.main:app --reload --port 8000
Production: uvicorn api.main:app --host 0.0.0.0 --port 8000
"""
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm

from api.auth import authenticate, create_tokens, get_current_user, get_refresh_payload
from api.config import get_config
from api.db_cloud import init_cloud_tables
from api.routes import ask, dashboard, gauges, petrace, push, records, sync


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_config()
    init_cloud_tables(cfg['db_path'])
    yield


app = FastAPI(title='PET Lab API', version='1.0.0', lifespan=lifespan)

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
