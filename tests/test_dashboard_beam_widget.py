"""
TDD tests for the /api/dashboard endpoint's beam-trend and gauge-history fields.

Mirrors tests/test_scan_endpoint.py and tests/test_gauges_limits.py for the
FastAPI TestClient + DATABASE_PATH-temp-file + dependency-override pattern.
"""
import json
import os
import tempfile
import uuid

# Point the cloud DB at a fresh temp file so the app's lifespan init never
# touches the real production DB (see CRITICAL SAFETY RULE in other test files).
_DB_PATH = os.path.join(tempfile.gettempdir(), f'petlab_dashboard_test_{uuid.uuid4().hex}.db')
os.environ.setdefault('DATABASE_PATH', _DB_PATH)

from fastapi.testclient import TestClient  # noqa: E402

from api import config as _config  # noqa: E402
_config.get_config.cache_clear()

import api.main as main  # noqa: E402
from api.auth import get_current_user  # noqa: E402
from api.routes import dashboard  # noqa: E402
from api.db_cloud import get_conn, init_cloud_tables  # noqa: E402
from db import init_db  # noqa: E402

_LAB_ID = 'petlabs-pretoria'

# Bypass JWT for these tests — same idiom as test_gauges_limits.py.
main.app.dependency_overrides[get_current_user] = lambda: {'username': 't', 'lab_id': _LAB_ID}


def _seed_synced_dashboard(db_path, lab_id):
    conn = get_conn(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO synced_dashboard (lab_id, payload, synced_at) VALUES (?,?,?)",
            [lab_id, json.dumps({'generated_at': '2026-07-01T00:00:00Z', 'components': []}),
             '2026-07-01T00:00:00Z'],
        )
        conn.commit()
    finally:
        conn.close()


def test_dashboard_includes_beam_trend_and_gauge_history_when_data_exists(tmp_path, monkeypatch):
    db_path = str(tmp_path / "with_data.db")
    init_db(db_path)          # on-prem schema: creates beam_daily + gauge_readings
    init_cloud_tables(db_path)  # cloud schema: creates synced_dashboard etc.

    conn = get_conn(db_path)
    try:
        conn.execute(
            "INSERT INTO gauge_readings (lab_id, gauge_name, timestamp, value, unit) "
            "VALUES (?,?,?,?,?)",
            [_LAB_ID, 'Vacuum Gauge 1', '2026-06-30T10:00:00Z', 7.4e-7, 'mbar'],
        )
        conn.execute(
            "INSERT INTO beam_daily (date, param, mean, std, min, max, p10, p90, data_quality) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ['2026-06-30', 'Arc-I', 48.0, 1.0, 40.0, 55.0, 42.0, 53.0, 'ok'],
        )
        conn.commit()
    finally:
        conn.close()

    _seed_synced_dashboard(db_path, _LAB_ID)

    monkeypatch.setattr(dashboard, 'get_config', lambda: {'db_path': db_path, 'lab_id': _LAB_ID})

    with TestClient(main.app) as client:
        r = client.get('/api/dashboard')

    assert r.status_code == 200
    data = r.json()
    assert data['beam_trend'], "beam_trend should be non-empty when beam_daily has rows"
    assert data['beam_trend'][0]['param'] == 'Arc-I'
    assert data['beam_trend'][0]['mean'] == 48.0
    assert data['gauge_history'], "gauge_history should be non-empty when gauge_readings has rows"
    assert data['gauge_history'][0]['gauge_name'] == 'Vacuum Gauge 1'


def test_dashboard_returns_empty_trend_fields_without_erroring_when_no_data(tmp_path, monkeypatch):
    db_path = str(tmp_path / "no_data.db")
    init_cloud_tables(db_path)  # only cloud tables -- no beam_daily table at all

    _seed_synced_dashboard(db_path, _LAB_ID)

    monkeypatch.setattr(dashboard, 'get_config', lambda: {'db_path': db_path, 'lab_id': _LAB_ID})

    with TestClient(main.app) as client:
        r = client.get('/api/dashboard')

    assert r.status_code == 200
    data = r.json()
    assert data['beam_trend'] == []
    assert data['gauge_history'] == []
