"""Regression tests for the gauge-photo OCR fix.

Root cause (workflow-diagnosed): on Render the Ollama fallback crashed with a
Windows-only Popen flag on Linux, the endpoint swallowed it and returned HTTP 200
with value=null, and the app showed the error as a fake reading. These lock in the
fix: OS-safe spawn, defensive Gemini parsing, and honest no-value handling.
"""
import os
import tempfile

import pytest

os.environ.setdefault('DATABASE_PATH', os.path.join(tempfile.gettempdir(), 'petlab_ocrfix_test.db'))


def _raise_fnf(*a, **k):
    raise FileNotFoundError('ollama')


# ── ollama_manager: OS-safe spawn + clean errors ────────────────────────────

def test_start_passes_no_creationflags_on_posix(monkeypatch):
    import api.ollama_manager as om
    if om._IS_WINDOWS:
        pytest.skip('POSIX-only')
    seen = {}

    def fake_popen(cmd, **kw):
        seen['kwargs'] = kw
        raise FileNotFoundError('ollama')

    monkeypatch.setattr(om.subprocess, 'Popen', fake_popen)
    with pytest.raises(RuntimeError, match='ollama binary'):
        om._start()
    assert 'creationflags' not in seen['kwargs']   # THE BUG: passing it raised ValueError on Linux


def test_ensure_running_raises_runtimeerror_not_valueerror(monkeypatch):
    import api.ollama_manager as om
    monkeypatch.setattr(om, '_is_running', lambda: False)
    monkeypatch.setattr(om, '_IS_LOCAL', True)
    monkeypatch.setattr(om.subprocess, 'Popen', _raise_fnf)
    with pytest.raises(RuntimeError):
        om.ensure_running()


def test_ensure_running_remote_host_never_spawns(monkeypatch):
    import api.ollama_manager as om
    monkeypatch.setattr(om, '_is_running', lambda: False)
    monkeypatch.setattr(om, '_IS_LOCAL', False)
    called = {'popen': False}
    monkeypatch.setattr(om.subprocess, 'Popen', lambda *a, **k: called.update(popen=True))
    with pytest.raises(RuntimeError, match='not reachable'):
        om.ensure_running()
    assert called['popen'] is False


# ── gemini_ocr: defensive response parsing ──────────────────────────────────

class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


def test_gemini_extracts_text_on_success(monkeypatch):
    import api.gemini_ocr as g
    monkeypatch.setattr(g, 'GEMINI_API_KEY', 'k')
    monkeypatch.setattr(g.httpx, 'post', lambda *a, **k: _Resp(
        {'candidates': [{'content': {'parts': [{'text': '{"is_gauge": true}'}]}}]}))
    assert g.call('prompt', '/9j/x', {'type': 'object'}) == '{"is_gauge": true}'


def test_gemini_raises_on_safety_block(monkeypatch):
    import api.gemini_ocr as g
    monkeypatch.setattr(g, 'GEMINI_API_KEY', 'k')
    monkeypatch.setattr(g.httpx, 'post', lambda *a, **k: _Resp(
        {'candidates': [], 'promptFeedback': {'blockReason': 'SAFETY'}}))
    with pytest.raises(RuntimeError):
        g.call('prompt', '/9j/x', {'type': 'object'})


# ── endpoint: honest failure, no phantom row ────────────────────────────────

def test_photo_reading_no_backend_is_honest_and_saves_no_row(monkeypatch):
    from api import config as _config
    _config.get_config.cache_clear()
    import api.main as main
    from api.auth import get_current_user
    from api.routes import gauges
    from api import gemini_ocr
    from api.db_cloud import get_conn, init_cloud_tables
    from fastapi.testclient import TestClient

    monkeypatch.setattr(gemini_ocr, 'GEMINI_API_KEY', '')   # no Gemini
    monkeypatch.setattr(gauges, '_OLLAMA_MODEL', '')        # no Ollama
    main.app.dependency_overrides[get_current_user] = lambda: {'username': 't', 'lab_id': 'petlabs-pretoria'}

    db = os.environ['DATABASE_PATH']
    init_cloud_tables(db)
    before = get_conn(db).execute("SELECT COUNT(*) AS n FROM gauge_readings").fetchone()['n']

    with TestClient(main.app) as c:
        r = c.post('/api/gauges/reading', json={'photo_b64': '/9j/abcd', 'gauge_name': '0096'})

    assert r.status_code == 200
    body = r.json()
    assert body['value'] is None
    assert body['ocr_ok'] is False        # honest failure signal for the UI
    assert body['id'] is None

    after = get_conn(db).execute("SELECT COUNT(*) AS n FROM gauge_readings").fetchone()['n']
    assert after == before                # no phantom NULL row saved
