"""
PET Lab Cloud API — FastAPI backend for the mobile app.

Dev:        uvicorn api.main:app --reload --port 8000
Production: uvicorn api.main:app --host 0.0.0.0 --port 8000
"""
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm

from api.auth import authenticate, create_tokens, get_current_user, get_refresh_payload
from api.config import get_config
from api.db_cloud import init_cloud_tables
from api.routes import dashboard, gauges, push, records


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_config()
    init_cloud_tables(cfg['db_path'])
    yield


app = FastAPI(title='PET Lab API', version='1.0.0', lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['GET', 'POST'],
    allow_headers=['Authorization', 'Content-Type'],
)


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
