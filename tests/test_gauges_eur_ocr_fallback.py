"""Reproduces the missing Ollama fallback in import_eur_photos() (EUR batch endpoint).

_run_ocr() (used by the mobile single-photo endpoint) falls back to the local Ollama
vision model whenever Gemini fails at runtime. import_eur_photos() does NOT: today, if
gemini_ocr.is_configured() is True but gemini_ocr.call() raises (e.g. Gemini quota
exhausted after its own internal retries), the exception propagates straight to the
per-photo except-block and that photo is recorded as a permanent error, even though
Ollama + qwen2.5vl:7b are installed and would happily serve it.

This test proves that today: it should currently FAIL (inserted == 0, an error recorded
for the photo) because no fallback exists yet.
"""
import base64
import json
import os
import tempfile

# Point the cloud DB at a temp file so the app's lifespan init doesn't touch the
# operator's real (Windows-path) DB.
os.environ.setdefault('DATABASE_PATH', os.path.join(tempfile.gettempdir(), 'petlab_eur_fallback_test.db'))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api import config as _config  # noqa: E402
_config.get_config.cache_clear()

import api.main as main  # noqa: E402
from api.auth import get_current_user  # noqa: E402
from api.routes import gauges  # noqa: E402
from api import gemini_ocr  # noqa: E402

main.app.dependency_overrides[get_current_user] = lambda: {'username': 't', 'lab_id': 'petlabs-pretoria'}

_FAKE_PHOTO_B64 = base64.b64encode(b'not a real jpeg, just test bytes').decode()

_FAKE_OLLAMA_EUR_JSON = json.dumps({
    'entries': [
        {
            'date': '2026-06-30',
            'operator': 'Test Operator',
            'gas_flow_sccm': 6.0,
        }
    ]
})


class _FakeOllamaResponse:
    """Mimics the httpx.Response the code needs: .raise_for_status() and .json()."""
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return {'response': _FAKE_OLLAMA_EUR_JSON}


def test_eur_photos_falls_back_to_ollama_when_gemini_fails(monkeypatch, tmp_path):
    # Gemini looks configured, but its call (after its own internal retries) fails.
    monkeypatch.setattr(gemini_ocr, 'is_configured', lambda: True)

    def _raise_gemini_error(*args, **kwargs):
        raise httpx.HTTPStatusError(
            '429 Too Many Requests',
            request=httpx.Request('POST', 'https://example.invalid'),
            response=httpx.Response(429, request=httpx.Request('POST', 'https://example.invalid')),
        )

    monkeypatch.setattr(gemini_ocr, 'call', _raise_gemini_error)

    # Ollama should be reachable and return a valid EUR JSON body.
    monkeypatch.setattr(gauges, 'ensure_running', lambda: None)
    monkeypatch.setattr(httpx, 'post', lambda *args, **kwargs: _FakeOllamaResponse())

    # Archive under a throwaway dir so this test never touches the real gauge_archive.
    cfg = _config.get_config()
    monkeypatch.setitem(cfg, 'db_path', os.path.join(str(tmp_path), 'cyclotron.db'))

    with TestClient(main.app) as client:
        payload = {'photos_b64': [_FAKE_PHOTO_B64], 'filenames': ['eur_form.jpg']}
        r = client.post('/api/gauges/eur-photos', json=payload)

    assert r.status_code == 200
    body = r.json()

    # Today (bug present): Gemini's failure propagates with no Ollama fallback, so
    # inserted == 0 and an error is recorded for the photo instead of a successful
    # Ollama-derived reading.
    assert body['inserted'] == 1, (
        f"Expected Ollama fallback to insert 1 reading, got {body['inserted']} "
        f"with errors={body['errors']}"
    )
    assert body['errors'] == []
