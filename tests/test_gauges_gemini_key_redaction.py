"""Defense-in-depth regression test for the Gemini API key leak (self-pentest
finding, 2026-07-02): api/gemini_ocr.py now sends GEMINI_API_KEY via a header
instead of a `?key=...` URL param, so a real key should never reach an
httpx.HTTPStatusError's string form in the first place. This test guards the
second layer independently of that fix: even if a future httpx client (or a
regression in gemini_ocr.py) ever put a key back in the request URL,
api/routes/gauges.py's `_redact()` must still strip it before the error text
reaches the client response, the server log, or a persisted gauge_readings row.
"""
import os
import tempfile

os.environ.setdefault('DATABASE_PATH', os.path.join(tempfile.gettempdir(), 'petlab_gemini_redact_test.db'))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api import config as _config  # noqa: E402
_config.get_config.cache_clear()

import api.main as main  # noqa: E402
from api.auth import get_current_user  # noqa: E402
from api.routes import gauges  # noqa: E402
from api import gemini_ocr  # noqa: E402
from api.db_cloud import get_conn, init_cloud_tables  # noqa: E402

_FAKE_SECRET = 'AIzaSyFAKESECRETVALUE1234567890'


def _raise_with_key_in_url(*args, **kwargs):
    # Simulates a leaked-key-shaped URL reaching the exception, regardless of how it
    # got there. Goes through httpx.Response's *real* raise_for_status(), the same
    # call gemini_ocr.py makes -- that's what actually embeds the request URL into
    # the exception's string form; manually constructing HTTPStatusError with a
    # plain message would not reproduce the real leak path.
    request = httpx.Request(
        'POST',
        f'https://generativelanguage.googleapis.com/v1beta/models/x:generateContent?key={_FAKE_SECRET}',
    )
    response = httpx.Response(429, request=request)
    response.raise_for_status()


def test_gauge_reading_error_never_discloses_gemini_key(monkeypatch, tmp_path):
    monkeypatch.setattr(gemini_ocr, 'is_configured', lambda: True)
    monkeypatch.setattr(gemini_ocr, 'call', _raise_with_key_in_url)
    monkeypatch.setattr(gauges, '_OLLAMA_MODEL', '')  # no Ollama fallback configured

    cfg = _config.get_config()
    db_path = os.path.join(str(tmp_path), 'cyclotron.db')
    monkeypatch.setitem(cfg, 'db_path', db_path)
    init_cloud_tables(db_path)

    # Restore whatever override (if any) was already in place rather than
    # unconditionally deleting it — other test modules set one at import time,
    # and this test runs before some of them in collection/execution order, so
    # an unconditional pop() here strips state they still depend on afterward.
    prev_override = main.app.dependency_overrides.get(get_current_user)
    main.app.dependency_overrides[get_current_user] = lambda: {'username': 't', 'lab_id': 'petlabs-pretoria'}
    try:
        with TestClient(main.app) as client:
            r = client.post('/api/gauges/reading', json={'photo_b64': '/9j/abcd', 'gauge_name': 'G-1'})
    finally:
        main.app.dependency_overrides.pop(get_current_user, None)
        if prev_override is not None:
            main.app.dependency_overrides[get_current_user] = prev_override

    assert r.status_code == 200
    body = r.json()
    assert _FAKE_SECRET not in body['raw_ocr_text']
    assert 'REDACTED' in body['raw_ocr_text']

    # Also never persisted into the DB -- process_photo_reading only inserts when
    # ocr_ok is True/value is not None, but future code paths could change that;
    # assert directly against what's on disk rather than trusting the response alone.
    conn = get_conn(db_path)
    rows = conn.execute("SELECT raw_ocr_text FROM gauge_readings").fetchall()
    conn.close()
    for row in rows:
        assert _FAKE_SECRET not in (row['raw_ocr_text'] or '')
