"""Request-size / batch limits (security hardening N1/M3).

Verifies the global body-size middleware rejects oversized requests, and the EUR
photo endpoint rejects an oversized batch *before* doing any expensive OCR work.
"""
import os
import tempfile

# Point the cloud DB at a temp file so the app's lifespan init doesn't touch the
# operator's real (Windows-path) DB.
os.environ.setdefault('DATABASE_PATH', os.path.join(tempfile.gettempdir(), 'petlab_limits_test.db'))

from fastapi.testclient import TestClient  # noqa: E402

from api import config as _config  # noqa: E402
_config.get_config.cache_clear()

import api.main as main  # noqa: E402
from api.auth import get_current_user  # noqa: E402
from api.routes.gauges import _MAX_EUR_PHOTOS  # noqa: E402

# Bypass JWT for these limit tests — the checks under test run before any auth-protected work.
main.app.dependency_overrides[get_current_user] = lambda: {'username': 't', 'lab_id': 'petlabs-pretoria'}


def test_oversized_request_body_is_rejected(monkeypatch):
    monkeypatch.setattr(main, '_MAX_BODY_BYTES', 16)
    with TestClient(main.app) as client:
        r = client.post(
            '/api/gauges',
            content=b'{"padding":"' + b'a' * 200 + b'"}',
            headers={'content-type': 'application/json'},
        )
    assert r.status_code == 413


def test_eur_photos_rejects_oversized_batch():
    with TestClient(main.app) as client:
        payload = {'photos_b64': ['eHg='] * (_MAX_EUR_PHOTOS + 1), 'filenames': []}
        r = client.post('/api/gauges/eur-photos', json=payload)
    assert r.status_code == 413


def test_cors_is_not_wildcard():
    with TestClient(main.app) as client:
        r = client.get('/health', headers={'Origin': 'http://evil.example'})
    acao = r.headers.get('access-control-allow-origin')
    assert acao != '*'
    assert acao != 'http://evil.example'
