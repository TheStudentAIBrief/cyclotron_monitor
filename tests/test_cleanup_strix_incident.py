"""Test for the one-time /admin/cleanup-strix-import-incident-20260709 route.

Verifies it deletes ONLY rows matching the exact incident signature, leaves
real-looking historical rows untouched, and refuses to run if the matched
count falls outside the expected 50k-300k sanity bound. Note: 'status' is a
computed field in the read API (gauges.py's _gauge_status), not a stored
column - value IS NULL is what actually produces status='UNKNOWN'.
"""
import tempfile
import uuid

import pytest

from api import config as _config
from api.auth import get_current_user
from api.db_cloud import get_conn, init_cloud_tables
import api.main as main
from fastapi.testclient import TestClient

main.app.dependency_overrides[get_current_user] = lambda: {'username': 't', 'lab_id': 'petlabs-pretoria'}


@pytest.fixture
def db_path(monkeypatch):
    path = tempfile.gettempdir() + f'/petlab_cleanup_incident_{uuid.uuid4().hex}.db'
    monkeypatch.setenv('DATABASE_PATH', path)
    _config.get_config.cache_clear()
    init_cloud_tables(path)
    yield path
    _config.get_config.cache_clear()


def _insert(conn, *, gauge_name, value, confidence, timestamp):
    conn.execute(
        "INSERT INTO gauge_readings (lab_id, gauge_name, timestamp, value, confidence) "
        "VALUES (?,?,?,?,?)",
        ['petlabs-pretoria', gauge_name, timestamp, value, confidence],
    )


def test_deletes_only_the_exact_incident_signature(db_path):
    conn = get_conn(db_path)
    # 3 real, legitimate-looking rows that must survive.
    _insert(conn, gauge_name='ISC_PRESSURE', value=101.3, confidence='verified',
            timestamp='2026-06-15T08:00:00+00:00')
    _insert(conn, gauge_name='BL1_VACUUM', value=4.4e-07, confidence='ocr',
            timestamp='2026-07-09T20:06:34+00:00')  # same ts, has a real value - must survive
    _insert(conn, gauge_name='REAL_GAUGE', value=None, confidence='import',
            timestamp='2026-07-09T20:06:34+00:00')  # real gauge_name, not blank - must survive
    # 100,003 polluted rows matching the exact incident signature.
    n_polluted = 100_003
    conn.executemany(
        "INSERT INTO gauge_readings (lab_id, gauge_name, timestamp, value, confidence) "
        "VALUES (?,?,?,?,?)",
        [('petlabs-pretoria', '', '2026-07-09T20:06:34+00:00', None, 'import')] * n_polluted,
    )
    conn.commit()
    conn.close()

    with TestClient(main.app) as client:
        r = client.post('/api/admin/cleanup-strix-import-incident-20260709')
    assert r.status_code == 200
    body = r.json()
    assert body['rows_deleted'] == n_polluted
    assert body['remaining_total_rows'] == 3

    conn = get_conn(db_path)
    remaining = conn.execute("SELECT gauge_name FROM gauge_readings ORDER BY id").fetchall()
    conn.close()
    assert [dict(row) for row in remaining] == [
        {'gauge_name': 'ISC_PRESSURE'},
        {'gauge_name': 'BL1_VACUUM'},
        {'gauge_name': 'REAL_GAUGE'},
    ]


def test_refuses_when_matched_count_outside_sanity_bound(db_path):
    conn = get_conn(db_path)
    # Only a handful of matching rows - nowhere near the expected 50k-300k.
    conn.executemany(
        "INSERT INTO gauge_readings (lab_id, gauge_name, timestamp, value, confidence) "
        "VALUES (?,?,?,?,?)",
        [('petlabs-pretoria', '', '2026-07-09T20:06:34+00:00', None, 'import')] * 5,
    )
    conn.commit()
    conn.close()

    with TestClient(main.app) as client:
        r = client.post('/api/admin/cleanup-strix-import-incident-20260709')
    assert r.status_code == 409

    conn = get_conn(db_path)
    count = conn.execute("SELECT COUNT(*) FROM gauge_readings").fetchone()[0]
    conn.close()
    assert count == 5  # nothing deleted


def test_inspect_reports_true_range_without_deleting(db_path):
    conn = get_conn(db_path)
    conn.executemany(
        "INSERT INTO gauge_readings (lab_id, gauge_name, timestamp, value, confidence) "
        "VALUES (?,?,?,?,?)",
        [('petlabs-pretoria', '', ts, None, 'import')
         for ts in ('2026-07-09T20:06:30+00:00', '2026-07-09T20:06:34+00:00', '2026-07-09T20:06:41+00:00')],
    )
    conn.commit()
    conn.close()

    with TestClient(main.app) as client:
        r = client.get('/api/admin/inspect-strix-import-incident-20260709')
    assert r.status_code == 200
    body = r.json()
    assert body['n'] == 3
    assert body['min_ts'] == '2026-07-09T20:06:30+00:00'
    assert body['max_ts'] == '2026-07-09T20:06:41+00:00'

    conn = get_conn(db_path)
    count = conn.execute("SELECT COUNT(*) FROM gauge_readings").fetchone()[0]
    conn.close()
    assert count == 3  # nothing deleted - read-only


def test_requires_auth(db_path):
    main.app.dependency_overrides.pop(get_current_user, None)
    try:
        with TestClient(main.app) as client:
            r = client.post('/api/admin/cleanup-strix-import-incident-20260709')
        assert r.status_code == 401
    finally:
        main.app.dependency_overrides[get_current_user] = lambda: {'username': 't', 'lab_id': 'petlabs-pretoria'}
