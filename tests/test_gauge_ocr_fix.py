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
    # This machine has OLLAMA_NEWSLETTER_ONLY=1 set as a real Windows env var —
    # isolate this test from that ambient state so it exercises the spawn-failure
    # path it's actually named for, not the (also-real) newsletter-only guard.
    monkeypatch.delenv('OLLAMA_NEWSLETTER_ONLY', raising=False)
    monkeypatch.setattr(om, '_is_running', lambda: False)
    monkeypatch.setattr(om, '_IS_LOCAL', True)
    monkeypatch.setattr(om.subprocess, 'Popen', _raise_fnf)
    with pytest.raises(RuntimeError):
        om.ensure_running()


def test_ensure_running_remote_host_never_spawns(monkeypatch):
    import api.ollama_manager as om
    monkeypatch.delenv('OLLAMA_NEWSLETTER_ONLY', raising=False)
    monkeypatch.setattr(om, '_is_running', lambda: False)
    monkeypatch.setattr(om, '_IS_LOCAL', False)
    called = {'popen': False}
    monkeypatch.setattr(om.subprocess, 'Popen', lambda *a, **k: called.update(popen=True))
    with pytest.raises(RuntimeError, match='not reachable'):
        om.ensure_running()
    assert called['popen'] is False


def test_ensure_running_refuses_when_newsletter_only_guard_set(monkeypatch):
    # MED-25 hardening: the guard is now centralized here so every caller
    # (gauge OCR, EUR photo OCR, the AI assistant) inherits it, instead of each
    # endpoint needing to re-implement the check (which is how it went missing).
    import api.ollama_manager as om
    monkeypatch.setenv('OLLAMA_NEWSLETTER_ONLY', '1')
    called = {'popen': False}
    monkeypatch.setattr(om.subprocess, 'Popen', lambda *a, **k: called.update(popen=True))
    with pytest.raises(RuntimeError, match='newsletter'):
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
    monkeypatch.setattr(g, 'ALLOW_CLOUD_OCR', True)   # explicit egress opt-in (default off)
    monkeypatch.setattr(g.httpx, 'post', lambda *a, **k: _Resp(
        {'candidates': [{'content': {'parts': [{'text': '{"is_gauge": true}'}]}}]}))
    assert g.call('prompt', '/9j/x', {'type': 'object'}) == '{"is_gauge": true}'


def test_gemini_raises_on_safety_block(monkeypatch):
    import api.gemini_ocr as g
    monkeypatch.setattr(g, 'GEMINI_API_KEY', 'k')
    monkeypatch.setattr(g, 'ALLOW_CLOUD_OCR', True)   # explicit egress opt-in (default off)
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
    main.app.dependency_overrides[get_current_user] = lambda: {'username': 't', 'lab_id': 'petlabs-pretoria', 'role': 'admin'}

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


def test_photo_reading_with_a_real_value_still_saves_no_row(tmp_path, monkeypatch):
    """Finding A8 (12 October 2026 catalogue review): a photo reading used to
    be inserted immediately by /gauges/reading, always as NORMAL (is_alert
    hard-coded False in _parse_ocr_result, no threshold check at all) - so an
    out-of-range photo read showed NORMAL until the operator pressed Save,
    which then inserted a SECOND row, this one correctly checked against
    thresholds. Now /gauges/reading only ever returns the value; the one row
    that ends up on file is the confirmed one from POST /gauges, and it is the
    one already proven correct by test_manual_reading_server_side_alert.py."""
    db_path = str(tmp_path / 'a8.db')
    monkeypatch.setenv('DATABASE_PATH', db_path)
    from api import config as _config
    _config.get_config.cache_clear()
    import api.main as main
    from api.auth import get_current_user
    from api.routes import gauges
    from api.db_cloud import get_conn, init_cloud_tables
    from fastapi.testclient import TestClient

    init_cloud_tables(db_path)
    prev_override = main.app.dependency_overrides.get(get_current_user)
    main.app.dependency_overrides[get_current_user] = lambda: {'username': 't', 'lab_id': 'petlabs-pretoria', 'role': 'admin'}
    # A gauge with an alert_hi of 80 already on file - 95 is a real out-of-range
    # reading, not a borderline one, so there's no ambiguity about what the old
    # NORMAL-always insert got wrong.
    conn = get_conn(db_path)
    conn.execute(
        "INSERT INTO gauge_readings (lab_id, gauge_name, timestamp, value, unit, "
        "alert_lo, alert_hi, action_lo, action_hi) VALUES (?,?,?,?,?,?,?,?,?)",
        ['petlabs-pretoria', '0200', '2026-01-01T00:00:00Z', 50.0, 'Pa', None, 80.0, None, 100.0],
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(
        gauges, '_run_ocr',
        lambda photo_b64, gauge_name='': {
            'value': 95.0, 'unit': 'Pa', 'is_alert': False, 'alert_reason': '',
            'raw_ocr_text': '0.9 confidence', 'ocr_ok': True,
        },
    )
    try:
        before = get_conn(db_path).execute("SELECT COUNT(*) AS n FROM gauge_readings").fetchone()['n']
        with TestClient(main.app) as c:
            r = c.post('/api/gauges/reading', json={'photo_b64': '/9j/abcd', 'gauge_name': '0200'})
        assert r.status_code == 200
        body = r.json()
        assert body['value'] == 95.0
        assert body['id'] is None
        photo_ref = body['photo_path']
        assert photo_ref   # a real server-issued filename, needed for the linkage check below
        after = get_conn(db_path).execute("SELECT COUNT(*) AS n FROM gauge_readings").fetchone()['n']
        assert after == before   # no row inserted just because OCR found a value

        # Operator reviews and confirms it - this is the one and only insert,
        # and it must come back ALERT (95 > alert_hi=80), not the NORMAL the
        # old /gauges/reading insert would have locked in. The photo_ref from
        # the reading step must carry through onto the confirmed row (Claudia
        # review, 13 Oct: A8 dropped photo linkage entirely).
        with TestClient(main.app) as c:
            r = c.post('/api/gauges', json={
                'gauge_name': '0200', 'value': 95.0, 'unit': 'Pa',
                'is_alert': False, 'alert_reason': '',
                'photo_ref': photo_ref, 'raw_ocr_text': body['raw_ocr_text'],
            })
        assert r.status_code == 200
        confirmed = r.json()
        assert confirmed['is_alert'] is True
        assert confirmed['alert_reason'] == 'ALERT'
        assert confirmed['photo_path'] == photo_ref

        rows = get_conn(db_path).execute(
            "SELECT is_alert, photo_path, raw_ocr_text FROM gauge_readings "
            "WHERE gauge_name='0200' AND value=95.0"
        ).fetchall()
        assert len(rows) == 1          # exactly one row for this reading, not two
        assert rows[0]['is_alert'] == 1
        assert rows[0]['photo_path'] == photo_ref
        assert rows[0]['raw_ocr_text'] == body['raw_ocr_text']
    finally:
        _config.get_config.cache_clear()
        main.app.dependency_overrides.pop(get_current_user, None)
        if prev_override is not None:
            main.app.dependency_overrides[get_current_user] = prev_override


def test_photo_ref_rejects_path_traversal_and_unknown_files(tmp_path, monkeypatch):
    """A client-supplied photo_ref must never be trusted as a path (Claudia review,
    13 Oct): a traversal attempt or a filename that was never issued by this server
    gets 400, not stored verbatim onto the row."""
    db_path = str(tmp_path / 'traversal.db')
    monkeypatch.setenv('DATABASE_PATH', db_path)
    from api import config as _config
    _config.get_config.cache_clear()
    import api.main as main
    from api.auth import get_current_user
    from api.db_cloud import init_cloud_tables
    from fastapi.testclient import TestClient

    init_cloud_tables(db_path)
    main.app.dependency_overrides[get_current_user] = lambda: {'username': 't', 'lab_id': 'petlabs-pretoria', 'role': 'admin'}
    try:
        with TestClient(main.app) as c:
            for bad_ref in ('../../etc/passwd', '..\\..\\windows\\win.ini', '/etc/passwd', 'never_issued.jpg'):
                r = c.post('/api/gauges', json={
                    'gauge_name': '0300', 'value': 1.0, 'unit': 'Pa', 'photo_ref': bad_ref,
                })
                assert r.status_code == 400, bad_ref
    finally:
        _config.get_config.cache_clear()
        main.app.dependency_overrides.pop(get_current_user, None)


def test_import_gauge_photos_script_persists_via_confirm_call(tmp_path, monkeypatch):
    """Finding A8 follow-up (Claudia review, 13 Oct): scripts/import_gauge_photos.py
    posts to /gauges/reading, which no longer inserts anything (A8's fix). Without
    a confirm call the script silently loses every reading in a bulk import. This
    runs the actual script function end to end against the real API + DB and counts
    the rows that land."""
    import io
    db_path = str(tmp_path / 'import_script.db')
    monkeypatch.setenv('DATABASE_PATH', db_path)
    from api import config as _config
    _config.get_config.cache_clear()
    import api.main as main
    from api.auth import get_current_user
    from api.routes import gauges
    from api.db_cloud import get_conn, init_cloud_tables
    from fastapi.testclient import TestClient
    from PIL import Image
    import scripts.import_gauge_photos as import_script

    init_cloud_tables(db_path)
    main.app.dependency_overrides[get_current_user] = lambda: {'username': 't', 'lab_id': 'petlabs-pretoria', 'role': 'admin'}
    monkeypatch.setattr(
        gauges, '_run_ocr',
        lambda photo_b64, gauge_name='': {
            'value': 12.5, 'unit': 'Pa', 'is_alert': False, 'alert_reason': '',
            'raw_ocr_text': 'high confidence', 'ocr_ok': True,
        },
    )

    buf = io.BytesIO()
    Image.new('RGB', (4, 4), color='blue').save(buf, format='JPEG')
    image_map = {'g1.jpg': buf.getvalue(), 'g2.jpg': buf.getvalue()}

    try:
        with TestClient(main.app) as client:
            def fake_post(url, headers=None, json=None, data=None, timeout=None):
                if url.endswith('/api/gauges/reading'):
                    return client.post('/api/gauges/reading', json=json)
                if url.endswith('/api/gauges'):
                    return client.post('/api/gauges', json=json)
                raise AssertionError(f'unexpected URL in test: {url}')

            monkeypatch.setattr(import_script.requests, 'post', fake_post)
            results = import_script.process_images(image_map, token='tok', gauge_name='0400', api='http://testserver')

        assert all(r['ok'] for r in results), results
        rows = get_conn(db_path).execute(
            "SELECT photo_path FROM gauge_readings WHERE gauge_name='0400'"
        ).fetchall()
        assert len(rows) == len(image_map)          # every readable photo actually persisted
        assert all(row['photo_path'] for row in rows)   # and kept its photo link
    finally:
        _config.get_config.cache_clear()
        main.app.dependency_overrides.pop(get_current_user, None)
