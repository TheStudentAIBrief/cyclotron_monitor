"""TDD: authenticated bulk-import endpoints for pushing local data to a cloud
deploy that has no shell/file access (e.g. Render free tier). Reuses the same
JWT auth as every other /api/* route - no new auth surface. The new endpoints
are POST-only, so they never collide with api/main.py's GET-only static-file
catch-all route.

Uses monkeypatch.setenv (auto-reverted after each test) rather than
os.environ.setdefault at module level - other test files
(test_scan_endpoint.py, test_gauges_limits.py) set DATABASE_PATH via
setdefault, which is a silent no-op if this file's import already set it
first, causing unrelated test files to share one DB file and step on each
other's fixtures. monkeypatch avoids leaking that global mutation forward.
"""
import tempfile
import uuid

import pytest
from fastapi.testclient import TestClient

import api.main as main
from api import config as _config
from api.auth import get_current_user
from api.db_cloud import get_conn, init_cloud_tables
from api.routes.admin_import import _MAX_ROWS_PER_REQUEST

main.app.dependency_overrides[get_current_user] = lambda: {'username': 't', 'lab_id': 'petlabs-pretoria'}


@pytest.fixture
def db_path(monkeypatch):
    path = tempfile.gettempdir() + f'/petlab_admin_import_{uuid.uuid4().hex}.db'
    monkeypatch.setenv('DATABASE_PATH', path)
    _config.get_config.cache_clear()
    init_cloud_tables(path)
    yield path
    _config.get_config.cache_clear()


def test_import_beam_daily_inserts_rows(db_path):
    row = {'date': '2026-01-01', 'param': 'Arc-I', 'mean': 1.0, 'std': 0.1,
           'min': 0.5, 'max': 1.5, 'p10': 0.6, 'p90': 1.4, 'data_quality': 'ok'}
    with TestClient(main.app) as client:
        r = client.post('/api/admin/import/beam-daily', json={'rows': [row]})
    assert r.status_code == 200
    assert r.json() == {'inserted': 1}
    conn = get_conn(db_path)
    count = conn.execute("SELECT COUNT(*) FROM beam_daily").fetchone()[0]
    conn.close()
    assert count == 1


def test_import_beam_daily_is_idempotent_upsert(db_path):
    row = {'date': '2026-01-01', 'param': 'Arc-I', 'mean': 1.0, 'std': 0.1,
           'min': 0.5, 'max': 1.5, 'p10': 0.6, 'p90': 1.4, 'data_quality': 'ok'}
    with TestClient(main.app) as client:
        client.post('/api/admin/import/beam-daily', json={'rows': [row]})
        row2 = {**row, 'mean': 2.0}
        client.post('/api/admin/import/beam-daily', json={'rows': [row2]})
    conn = get_conn(db_path)
    rows = conn.execute("SELECT COUNT(*) FROM beam_daily").fetchone()[0]
    mean = conn.execute("SELECT mean FROM beam_daily").fetchone()[0]
    conn.close()
    assert rows == 1
    assert mean == 2.0


def test_import_beam_daily_rejects_batch_over_limit(db_path):
    row = {'date': '2026-01-01', 'param': 'Arc-I', 'mean': 1.0, 'std': 0.1,
           'min': 0.5, 'max': 1.5, 'p10': 0.6, 'p90': 1.4, 'data_quality': 'ok'}
    with TestClient(main.app) as client:
        r = client.post('/api/admin/import/beam-daily',
                         json={'rows': [row] * (_MAX_ROWS_PER_REQUEST + 1)})
    assert r.status_code == 413


def test_import_petrace_batches_inserts_and_upserts(db_path):
    row = {'batch_no': 1, 'batch_date': '2026-01-01', 'tracer_num': 1,
           'tracer_name': 'FDG', 'site': 'geps', 'duration_s': 100.0, 'row_count': 10,
           'foil_no': 1, 'peak_target_uA': 5.0, 'avg_target_uA': 4.0, 'total_muAh': 1.0,
           'avg_arc_I': 40.0, 'avg_vacuum_P': 1e-6, 'peak_vacuum_P': 2e-6,
           'rf_efficiency': 0.9, 'ingested_at': '2026-01-01T00:00:00Z'}
    with TestClient(main.app) as client:
        r = client.post('/api/admin/import/petrace-batches', json={'rows': [row]})
        assert r.status_code == 200
        assert r.json() == {'inserted': 1}
        row2 = {**row, 'tracer_name': 'CHANGED'}
        client.post('/api/admin/import/petrace-batches', json={'rows': [row2]})
    conn = get_conn(db_path)
    count = conn.execute("SELECT COUNT(*) FROM petrace_batches").fetchone()[0]
    name = conn.execute("SELECT tracer_name FROM petrace_batches WHERE batch_no=1").fetchone()[0]
    conn.close()
    assert count == 1
    assert name == 'CHANGED'


def test_import_petrace_batches_accepts_null_numeric_fields(db_path):
    # Regression: the real local petrace_batches table has a row with
    # avg_vacuum_P=NULL - the Pydantic model originally required a float,
    # which 422'd the whole batch during the real migration run.
    row = {'batch_no': 2, 'batch_date': '2026-01-02', 'ingested_at': '2026-01-02T00:00:00Z',
           'avg_vacuum_P': None, 'peak_target_uA': None, 'duration_s': None, 'foil_no': None}
    with TestClient(main.app) as client:
        r = client.post('/api/admin/import/petrace-batches', json={'rows': [row]})
    assert r.status_code == 200
    assert r.json() == {'inserted': 1}


def test_import_gauge_readings_inserts_and_is_idempotent(db_path):
    row = {'lab_id': 'petlabs-pretoria', 'gauge_name': 'Vacuum-P', 'timestamp': '2026-01-01T00:00:00Z',
           'value': 1e-6, 'unit': 'mbar', 'is_alert': 0, 'alert_reason': '', 'photo_path': '',
           'raw_ocr_text': '', 'location': '', 'alert_lo': None, 'alert_hi': None,
           'action_lo': None, 'action_hi': None, 'confidence': '', 'verified_by': '', 'verified_at': ''}
    with TestClient(main.app) as client:
        r1 = client.post('/api/admin/import/gauge-readings', json={'rows': [row]})
        r2 = client.post('/api/admin/import/gauge-readings', json={'rows': [row]})  # re-run, same data
    assert r1.status_code == 200
    assert r1.json() == {'inserted': 1}
    assert r2.json() == {'inserted': 0}  # already present, skipped - not duplicated
    conn = get_conn(db_path)
    count = conn.execute("SELECT COUNT(*) FROM gauge_readings").fetchone()[0]
    conn.close()
    assert count == 1


def test_import_endpoints_require_auth(db_path):
    main.app.dependency_overrides.pop(get_current_user, None)
    try:
        with TestClient(main.app) as client:
            r = client.post('/api/admin/import/beam-daily', json={'rows': []})
        assert r.status_code == 401
    finally:
        main.app.dependency_overrides[get_current_user] = lambda: {'username': 't', 'lab_id': 'petlabs-pretoria'}
