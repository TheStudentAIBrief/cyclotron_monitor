"""Scrub API keys that were written into stored OCR text before redaction existed.

An early gauge reading holds an upstream error message with a Gemini API key in
it (`...?key=AIza...`). New errors are redacted before they are stored, but the
old row stays readable by anyone who can list gauge readings. There is no shell
access to the cloud database, so an admin-only route applies the same redaction
to what is already stored.
"""
import pytest
from fastapi.testclient import TestClient

import api.main as main
from api.db_cloud import get_conn
from tests.test_rbac import _ADMIN, _LAB, _hdr, _make_user, db_path  # noqa: F401

_KEY = 'AIzaSyD-FAKE-not-a-real-key-0123456789a'   # 39 characters, like a real one
assert len(_KEY) == 39
_LEAKY = f"HTTPStatusError: 429 for url 'https://generativelanguage.googleapis.com/v1/models/x:generateContent?key={_KEY}'"


def _seed(path):
    conn = get_conn(path)
    rows = [(397, _LEAKY), (398, 'high confidence — needle at 12'), (399, f'bare key in text {_KEY} here')]
    for rid, text in rows:
        conn.execute(
            "INSERT INTO gauge_readings (id, lab_id, gauge_name, timestamp, value, unit, raw_ocr_text) "
            "VALUES (?,?,?,?,?,?,?)", [rid, _LAB, '0096', '2026-01-01T00:00:00Z', 12.3, 'Pa', text])
    conn.commit()
    conn.close()


def _texts(path):
    conn = get_conn(path)
    out = {r['id']: r['raw_ocr_text'] for r in conn.execute("SELECT id, raw_ocr_text FROM gauge_readings")}
    conn.close()
    return out


def test_admin_scrubs_stored_keys_and_leaves_everything_else_alone(db_path):  # noqa: F811
    _seed(db_path)
    with TestClient(main.app) as c:
        r = c.post('/api/gauges/redact-secrets', headers=_hdr(_ADMIN))
    assert r.status_code == 200
    assert r.json()['redacted'] == 2
    texts = _texts(db_path)
    assert all(_KEY not in t for t in texts.values())
    assert texts[398] == 'high confidence — needle at 12'          # untouched
    assert 'generateContent' in texts[397] and 'REDACTED' in texts[397]   # the rest of the message is kept


def test_scrub_is_safe_to_run_twice(db_path):  # noqa: F811
    _seed(db_path)
    with TestClient(main.app) as c:
        c.post('/api/gauges/redact-secrets', headers=_hdr(_ADMIN))
        before = _texts(db_path)
        r = c.post('/api/gauges/redact-secrets', headers=_hdr(_ADMIN))
    assert r.json()['redacted'] == 0
    assert _texts(db_path) == before


@pytest.mark.parametrize('role', ['viewer', 'operator'])
def test_only_an_admin_can_run_the_scrub(db_path, role):  # noqa: F811
    _seed(db_path)
    with TestClient(main.app) as c:
        _make_user(c, 'someone', role)
        r = c.post('/api/gauges/redact-secrets', headers=_hdr('someone'))
    assert r.status_code == 403
    assert _KEY in _texts(db_path)[397]


def test_the_scrub_is_audited_without_copying_the_key_into_the_audit_log(db_path):  # noqa: F811
    _seed(db_path)
    with TestClient(main.app) as c:
        c.post('/api/gauges/redact-secrets', headers=_hdr(_ADMIN))
    conn = get_conn(db_path)
    row = conn.execute("SELECT actor, detail FROM audit_log WHERE action='redact_stored_secrets'").fetchone()
    conn.close()
    assert row['actor'] == _ADMIN
    assert '397' in row['detail'] and '399' in row['detail']
    assert _KEY not in row['detail']


def test_the_key_is_no_longer_served_to_app_users(db_path):  # noqa: F811
    _seed(db_path)
    with TestClient(main.app) as c:
        assert _KEY in c.get('/api/gauges', headers=_hdr(_ADMIN)).text      # the problem, before
        c.post('/api/gauges/redact-secrets', headers=_hdr(_ADMIN))
        assert _KEY not in c.get('/api/gauges', headers=_hdr(_ADMIN)).text


# ── gaps found by independent review ──────────────────────────────────────────

def _put(path, rid, text, lab=_LAB):
    conn = get_conn(path)
    conn.execute(
        "INSERT INTO gauge_readings (id, lab_id, gauge_name, timestamp, value, unit, raw_ocr_text) "
        "VALUES (?,?,?,?,?,?,?)", [rid, lab, '0096', '2026-01-01T00:00:00Z', 12.3, 'Pa', text])
    conn.commit()
    conn.close()


@pytest.mark.parametrize('text,secret', [
    ("error for url 'https://example.com/v1?key=s3cr3t-not-google-shaped'", 's3cr3t-not-google-shaped'),
    ("error for url 'https://example.com/v1?KEY=UPPERCASEPARAM123'", 'UPPERCASEPARAM123'),
    ("error for url 'https://example.com/v1?x=1&api_key=apikeyparam456'", 'apikeyparam456'),
])
def test_keys_in_other_shapes_are_scrubbed_too(db_path, text, secret):  # noqa: F811
    _put(db_path, 500, text)
    with TestClient(main.app) as c:
        c.post('/api/gauges/redact-secrets', headers=_hdr(_ADMIN))
    assert secret not in _texts(db_path)[500]


def test_a_key_stored_under_another_lab_is_scrubbed_as_well(db_path):  # noqa: F811
    _put(db_path, 600, _LEAKY, lab='default')
    with TestClient(main.app) as c:
        r = c.post('/api/gauges/redact-secrets', headers=_hdr(_ADMIN))
    assert r.json()['redacted'] == 1
    assert _KEY not in _texts(db_path)[600]


def test_a_reading_whose_stored_text_is_not_text_does_not_stop_the_scrub(db_path):  # noqa: F811
    _seed(db_path)
    conn = get_conn(db_path)
    conn.execute("INSERT INTO gauge_readings (id, lab_id, gauge_name, timestamp, value, unit, raw_ocr_text) "
                 "VALUES (700, ?, '0096', '2026-01-01T00:00:00Z', 1.0, 'Pa', x'414961')", [_LAB])
    conn.commit()
    conn.close()
    with TestClient(main.app) as c:
        r = c.post('/api/gauges/redact-secrets', headers=_hdr(_ADMIN))
    assert r.status_code == 200 and r.json()['redacted'] == 2


def test_deleting_a_leaky_reading_does_not_copy_the_key_into_the_audit_log(db_path):  # noqa: F811
    """The delete audit entry stores the removed row; a key copied there could never
    be removed again without breaking the audit chain."""
    _seed(db_path)
    with TestClient(main.app) as c:
        assert c.delete('/api/gauges/397', headers=_hdr(_ADMIN)).status_code == 200
    conn = get_conn(db_path)
    detail = conn.execute("SELECT detail FROM audit_log WHERE action='delete_gauge_reading'").fetchone()[0]
    conn.close()
    assert _KEY not in detail and 'generateContent' in detail


def test_the_scrub_reports_keys_already_copied_into_the_audit_log(db_path):  # noqa: F811
    """Those cannot be removed (the log is hash-chained) -- the admin must at least be told."""
    _seed(db_path)
    from api import audit
    conn = get_conn(db_path)
    audit.write(conn, 'delete_gauge_reading', 'someone', _LAB, '{"raw_ocr_text": "' + _LEAKY + '"}')
    conn.commit()
    conn.close()
    with TestClient(main.app) as c:
        r = c.post('/api/gauges/redact-secrets', headers=_hdr(_ADMIN))
    assert r.json()['audit_entries_with_keys'] == 1


def test_no_copy_of_the_key_is_left_in_the_database_file(db_path):  # noqa: F811
    """A later backup is a copy of the file, old pages included."""
    import glob
    for rid in range(1000, 1060):
        _put(db_path, rid, _LEAKY)
    with TestClient(main.app) as c:
        c.post('/api/gauges/redact-secrets', headers=_hdr(_ADMIN))
    leftovers = [f for f in glob.glob(db_path + '*') if _KEY.encode() in open(f, 'rb').read()]
    assert leftovers == []
