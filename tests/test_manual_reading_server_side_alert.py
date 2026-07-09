"""POST /api/gauges must not trust a client-supplied is_alert once real thresholds
are on file for that gauge (HIGH-08 hardening) — a client can no longer suppress
an alert by submitting is_alert=false for a value that is actually out of range."""
import os
import tempfile

os.environ.setdefault('DATABASE_PATH', os.path.join(tempfile.gettempdir(), 'petlab_manual_alert_test.db'))

from fastapi.testclient import TestClient  # noqa: E402

from api import config as _config  # noqa: E402
_config.get_config.cache_clear()

import api.main as main  # noqa: E402
from api.auth import get_current_user  # noqa: E402
from api.db_cloud import get_conn, init_cloud_tables  # noqa: E402

_LAB_ID = 'petlabs-pretoria'


def _client(db_path):
    init_cloud_tables(db_path)
    main.app.dependency_overrides[get_current_user] = lambda: {'username': 't', 'lab_id': _LAB_ID}
    return TestClient(main.app)


def test_client_cannot_suppress_alert_once_thresholds_are_known(tmp_path, monkeypatch):
    db_path = str(tmp_path / 'alert.db')
    monkeypatch.setenv('DATABASE_PATH', db_path)
    _config.get_config.cache_clear()
    prev_override = main.app.dependency_overrides.get(get_current_user)
    try:
        c = _client(db_path)

        conn = get_conn(db_path)
        conn.execute(
            "INSERT INTO gauge_readings (lab_id, gauge_name, timestamp, value, unit, "
            "alert_lo, alert_hi, action_lo, action_hi) VALUES (?,?,?,?,?,?,?,?,?)",
            [_LAB_ID, '0104', '2026-01-01T00:00:00Z', 60.0, 'Pa', None, 80.0, None, 100.0],
        )
        conn.commit()
        conn.close()

        # 90 Pa is above alert_hi=80 but below action_hi=100 — ALERT, not ACTION.
        # Client claims is_alert=false; server must override based on the thresholds
        # already on file for gauge '0104'.
        r = c.post('/api/gauges', json={
            'gauge_name': '0104', 'value': 90.0, 'unit': 'Pa',
            'is_alert': False, 'alert_reason': 'looks fine',
        })
        assert r.status_code == 200
        body = r.json()
        assert body['is_alert'] is True
        assert body['alert_reason'] == 'ALERT'

        conn = get_conn(db_path)
        row = conn.execute(
            "SELECT is_alert, alert_reason FROM gauge_readings WHERE gauge_name='0104' AND value=90.0"
        ).fetchone()
        conn.close()
        assert row['is_alert'] == 1
        assert row['alert_reason'] == 'ALERT'
    finally:
        # get_config() is @lru_cache(maxsize=1) — without this, the cached dict
        # (pointing at this test's tmp_path db) would leak into every test that
        # runs afterward, in this file or any other, since monkeypatch reverting
        # the env var doesn't itself invalidate an already-cached return value.
        _config.get_config.cache_clear()
        # _client() sets a get_current_user bypass that must not leak into other
        # test files (e.g. test_scan_endpoint.py's negative-auth tests, which
        # expect real auth to be active and would silently pass with a bypass).
        main.app.dependency_overrides.pop(get_current_user, None)
        if prev_override is not None:
            main.app.dependency_overrides[get_current_user] = prev_override


def test_manual_reading_trusts_client_when_no_thresholds_on_file(tmp_path, monkeypatch):
    db_path = str(tmp_path / 'no_thresholds.db')
    monkeypatch.setenv('DATABASE_PATH', db_path)
    _config.get_config.cache_clear()
    prev_override = main.app.dependency_overrides.get(get_current_user)
    try:
        c = _client(db_path)

        # Brand-new gauge, no prior threshold history — server has nothing to check
        # against, so it falls back to trusting the client (pre-existing behaviour).
        r = c.post('/api/gauges', json={
            'gauge_name': 'brand-new-gauge', 'value': 42.0, 'unit': 'Pa',
            'is_alert': True, 'alert_reason': 'operator flagged it',
        })
        assert r.status_code == 200
        body = r.json()
        assert body['is_alert'] is True
        assert body['alert_reason'] == 'operator flagged it'
    finally:
        _config.get_config.cache_clear()
        main.app.dependency_overrides.pop(get_current_user, None)
        if prev_override is not None:
            main.app.dependency_overrides[get_current_user] = prev_override
