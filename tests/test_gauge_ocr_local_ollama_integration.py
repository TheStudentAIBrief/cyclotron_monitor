"""Real (non-mocked) integration test for the local Ollama vision fallback.

Root cause this locks in (see workflow context): a co-founder's PR correctly made
GAUGE_OLLAMA_MODEL opt-in (empty by default) for cloud/Render, where there is no
Ollama binary at all. But on an on-prem dev machine like this one, that env var
must actually be set (start_dev.ps1 now does this) or the Ollama fallback silently
never fires and every Gemini failure (quota 429s included) reports "not configured"
instead of trying the local model that would otherwise succeed.

This test proves the real HTTP round-trip to a local Ollama server + qwen2.5vl:7b
actually works end-to-end through the /api/gauges/reading endpoint, with Gemini
simulated as unavailable. It talks to a REAL Ollama server — no mocking of the
Ollama call itself — so it only runs on a machine where that's actually possible.
"""
import base64
import io
import os
import tempfile

import httpx
import pytest

# Safety rule: point the DB at a tempfile before anything imports api.main/api.config,
# so the app's lifespan init never touches the real production cyclotron.db.
os.environ.setdefault('DATABASE_PATH', os.path.join(tempfile.gettempdir(), 'petlab_ollama_integration_test.db'))

_OLLAMA_HOST = os.environ.get('OLLAMA_HOST', 'http://localhost:11434')
_REQUIRED_MODEL = 'qwen2.5vl:7b'


def _ollama_ready() -> bool:
    """True only if Ollama answers within ~2s AND has the vision model pulled."""
    try:
        r = httpx.get(f'{_OLLAMA_HOST}/api/tags', timeout=2.0)
        r.raise_for_status()
    except Exception:
        return False
    names = [m.get('name', '') for m in r.json().get('models', [])]
    return any(n == _REQUIRED_MODEL for n in names)


pytestmark = pytest.mark.skipif(
    not _ollama_ready(),
    reason=f'Ollama not reachable at {_OLLAMA_HOST} or {_REQUIRED_MODEL} not pulled '
           '(this integration test only runs on machines with a local on-prem Ollama).',
)


def _tiny_test_image_b64() -> str:
    """A few-KB real JPEG. Doesn't need to depict a gauge — this proves the HTTP
    round-trip to Ollama returns a parseable result shape, not OCR accuracy."""
    from PIL import Image, ImageDraw

    img = Image.new('RGB', (200, 200), 'white')
    draw = ImageDraw.Draw(img)
    draw.ellipse((20, 20, 180, 180), outline='black', width=4)
    draw.line((100, 100, 100, 30), fill='black', width=4)   # a "needle"
    buf = io.BytesIO()
    img.save(buf, format='JPEG')
    return base64.b64encode(buf.getvalue()).decode()


def test_local_ollama_vision_fallback_reads_real_endpoint(monkeypatch):
    from api import config as _config
    _config.get_config.cache_clear()
    import api.main as main
    from api.auth import get_current_user
    from api.routes import gauges
    from api import gemini_ocr
    from fastapi.testclient import TestClient

    # Simulate Gemini exhausted/misconfigured (equivalent effect to retries exhausting
    # a 429 quota) — do NOT call the real Gemini API in this test.
    monkeypatch.setattr(gemini_ocr, 'is_configured', lambda: False)
    # Simulate start_dev.ps1 having set GAUGE_OLLAMA_MODEL correctly.
    monkeypatch.setattr(gauges, '_OLLAMA_MODEL', _REQUIRED_MODEL)

    main.app.dependency_overrides[get_current_user] = lambda: {'username': 't', 'lab_id': 'petlabs-pretoria'}

    photo_b64 = _tiny_test_image_b64()

    import time
    start = time.monotonic()
    # Ollama cold-start + first CPU inference can be slow; the endpoint itself uses a
    # 600s timeout talking to Ollama (see _gemini_then_ollama's ollama_timeout=600),
    # so let this specific test take as long as that needs.
    with TestClient(main.app) as c:
        r = c.post('/api/gauges/reading', json={'photo_b64': photo_b64, 'gauge_name': 'TEST-0001'})
    elapsed = time.monotonic() - start

    assert r.status_code == 200
    body = r.json()
    assert 'ocr_ok' in body
    # Proves the real Ollama backend was actually reached and answered — not the
    # "not configured" short-circuit path.
    assert 'not configured' not in body['raw_ocr_text']

    print(f'\n[test_local_ollama_vision_fallback_reads_real_endpoint] real Ollama round-trip took {elapsed:.2f}s')


def _full_size_test_image_b64() -> str:
    """A phone-camera-sized image (3024x4032, matches a typical iPhone photo).

    Regression fixture for the num_ctx bug: Qwen2.5-VL's dynamic-resolution image
    tokenizer alone can spend 4000+ tokens on a full-size photo, which overflowed
    Ollama's per-request default context window (4096) with a 400 "exceeds the
    available context size" error — before num_ctx was set explicitly in
    api/routes/gauges.py's _run_ocr. The tiny 200x200 image used above doesn't
    reproduce this; only a realistically large image does.
    """
    from PIL import Image, ImageDraw

    img = Image.new('RGB', (3024, 4032), 'white')
    draw = ImageDraw.Draw(img)
    draw.ellipse((900, 1500, 2100, 2700), outline='black', width=12)
    draw.line((1500, 2100, 1500, 1600), fill='black', width=12)
    buf = __import__('io').BytesIO()
    img.save(buf, format='JPEG', quality=90)
    return base64.b64encode(buf.getvalue()).decode()


def test_local_ollama_vision_fallback_handles_full_size_photo_without_context_overflow(monkeypatch):
    """Regression test for the num_ctx fix in api/routes/gauges.py's _run_ocr.

    Before the fix, this request failed with a 400 "exceeds the available context
    size (4096 tokens)" error from Ollama's llama.cpp backend, reported as
    ocr_ok=False with 'OCR failed' in raw_ocr_text — never reaching the model.
    """
    from api import config as _config
    _config.get_config.cache_clear()
    import api.main as main
    from api.auth import get_current_user
    from api.routes import gauges
    from api import gemini_ocr
    from fastapi.testclient import TestClient

    monkeypatch.setattr(gemini_ocr, 'is_configured', lambda: False)
    monkeypatch.setattr(gauges, '_OLLAMA_MODEL', _REQUIRED_MODEL)

    main.app.dependency_overrides[get_current_user] = lambda: {'username': 't', 'lab_id': 'petlabs-pretoria'}

    photo_b64 = _full_size_test_image_b64()

    with TestClient(main.app) as c:
        r = c.post('/api/gauges/reading', json={'photo_b64': photo_b64, 'gauge_name': 'TEST-FULLSIZE'})

    assert r.status_code == 200
    body = r.json()
    # The specific failure mode this regression guards against: a context-size
    # rejection reported through raw_ocr_text (the endpoint always returns 200,
    # even on OCR failure — see process_photo_reading's "no phantom NULL row" path).
    assert 'exceeds the available context size' not in body['raw_ocr_text']
    assert 'exceed_context_size_error' not in body['raw_ocr_text']
