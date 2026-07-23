"""HIGH finding (external security review prep): facility gauge/EUR photos must
never be sent to Google's Gemini cloud unless the operator has explicitly opted in.

render.yaml wires GEMINI_API_KEY on the public deployment, and api/routes/gauges.py
is Gemini-FIRST whenever the key is set — so merely configuring the key made every
operator-uploaded gauge photo and EUR form photo (operational readings, operator
initials, dates) leave the NNR-regulated facility for a US third-party cloud.

Contract under test: cloud OCR requires an explicit, default-off ALLOW_CLOUD_OCR=1
*in addition to* GEMINI_API_KEY, so data egress is a deliberate, documented policy
decision rather than a side effect of a key being present.
"""
import os
import tempfile

os.environ.setdefault('DATABASE_PATH', os.path.join(tempfile.gettempdir(), 'petlab_cloud_ocr_optin_test.db'))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api import config as _config  # noqa: E402
_config.get_config.cache_clear()

import api.main as main  # noqa: E402
from api import gemini_ocr  # noqa: E402
from api.auth import get_current_user  # noqa: E402
from api.routes import gauges  # noqa: E402

_FAKE_KEY = 'AIzaSyFAKE-not-a-real-key'


def _as_user():
    prev = main.app.dependency_overrides.get(get_current_user)
    main.app.dependency_overrides[get_current_user] = (
        lambda: {'username': 'optin-tester', 'lab_id': 'petlabs-pretoria'}
    )
    return prev


def _restore_user(prev):
    main.app.dependency_overrides.pop(get_current_user, None)
    if prev is not None:
        main.app.dependency_overrides[get_current_user] = prev


def _capture_egress(monkeypatch):
    """Replace the module-level httpx.post the server code uses for outbound
    calls with a recorder that refuses to send anything. (TestClient's own
    in-process transport does not go through httpx.post, so this only sees the
    app's outbound traffic.)"""
    egress = []

    def _post(url, *args, **kwargs):
        egress.append(str(url))
        raise RuntimeError('network egress blocked by test')

    monkeypatch.setattr(gemini_ocr.httpx, 'post', _post)
    return egress


def test_gauge_photo_reading_does_not_egress_to_google_without_optin(monkeypatch):
    """The public-deployment configuration the moment GEMINI_API_KEY is set
    (and no local Ollama), with NO explicit cloud-OCR opt-in: the operator's
    photo must not leave for generativelanguage.googleapis.com."""
    monkeypatch.setattr(gemini_ocr, 'GEMINI_API_KEY', _FAKE_KEY)
    monkeypatch.setattr(gemini_ocr, 'ALLOW_CLOUD_OCR', False, raising=False)
    monkeypatch.setattr(gauges, '_OLLAMA_MODEL', '')
    egress = _capture_egress(monkeypatch)
    prev = _as_user()
    try:
        with TestClient(main.app) as c:
            r = c.post('/api/gauges/reading',
                       json={'photo_b64': '/9j/facility-gauge-photo', 'gauge_name': 'Vacuum'})
    finally:
        _restore_user(prev)
    assert r.status_code == 200
    google_calls = [u for u in egress if 'googleapis.com' in u]
    assert google_calls == [], (
        f'gauge photo was sent to Google without the ALLOW_CLOUD_OCR opt-in: {google_calls}'
    )


def test_eur_photos_do_not_egress_to_google_without_optin(monkeypatch):
    monkeypatch.setattr(gemini_ocr, 'GEMINI_API_KEY', _FAKE_KEY)
    monkeypatch.setattr(gemini_ocr, 'ALLOW_CLOUD_OCR', False, raising=False)
    monkeypatch.setattr(gauges, 'ensure_running', lambda: None)  # keep the Ollama fallback hermetic
    egress = _capture_egress(monkeypatch)
    prev = _as_user()
    try:
        with TestClient(main.app) as c:
            r = c.post('/api/gauges/eur-photos',
                       json={'photos_b64': ['ZXVyLWZvcm0='], 'filenames': ['eur.jpg']})
    finally:
        _restore_user(prev)
    assert r.status_code == 200
    google_calls = [u for u in egress if 'googleapis.com' in u]
    assert google_calls == [], (
        f'EUR form photo was sent to Google without the ALLOW_CLOUD_OCR opt-in: {google_calls}'
    )


def test_gemini_call_itself_refuses_without_optin(monkeypatch):
    """Defense in depth: gemini_ocr.call() must refuse before building/sending
    the payload, so no future caller can egress a photo without the opt-in."""
    monkeypatch.setattr(gemini_ocr, 'GEMINI_API_KEY', _FAKE_KEY)
    monkeypatch.setattr(gemini_ocr, 'ALLOW_CLOUD_OCR', False, raising=False)
    egress = _capture_egress(monkeypatch)
    with pytest.raises(RuntimeError, match='ALLOW_CLOUD_OCR'):
        gemini_ocr.call('prompt', '/9j/x', {'type': 'object'})
    assert egress == []


def test_is_configured_requires_both_key_and_optin(monkeypatch):
    monkeypatch.setattr(gemini_ocr, 'GEMINI_API_KEY', _FAKE_KEY)
    monkeypatch.setattr(gemini_ocr, 'ALLOW_CLOUD_OCR', False, raising=False)
    assert gemini_ocr.is_configured() is False
    monkeypatch.setattr(gemini_ocr, 'ALLOW_CLOUD_OCR', True, raising=False)
    assert gemini_ocr.is_configured() is True
    monkeypatch.setattr(gemini_ocr, 'GEMINI_API_KEY', '')
    assert gemini_ocr.is_configured() is False


def test_cloud_optin_flag_must_be_explicitly_truthy(monkeypatch):
    """Presence alone is not consent — only explicit truthy values enable egress."""
    for off in ('', '0', 'false', 'no', 'off'):
        monkeypatch.setenv('ALLOW_CLOUD_OCR', off)
        assert gemini_ocr._flag('ALLOW_CLOUD_OCR') is False, f'{off!r} must not enable cloud OCR'
    for on in ('1', 'true', 'yes', 'on', 'TRUE'):
        monkeypatch.setenv('ALLOW_CLOUD_OCR', on)
        assert gemini_ocr._flag('ALLOW_CLOUD_OCR') is True


def test_gemini_call_proceeds_with_explicit_optin(monkeypatch):
    """With the deliberate opt-in granted, the Gemini path works as before."""
    monkeypatch.setattr(gemini_ocr, 'GEMINI_API_KEY', _FAKE_KEY)
    monkeypatch.setattr(gemini_ocr, 'ALLOW_CLOUD_OCR', True, raising=False)

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {'candidates': [{'content': {'parts': [{'text': '{"ok": 1}'}]}}]}

    monkeypatch.setattr(gemini_ocr.httpx, 'post', lambda *a, **k: _Resp())
    assert gemini_ocr.call('p', '/9j/x', {'type': 'object'}) == '{"ok": 1}'
