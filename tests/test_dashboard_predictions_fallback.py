"""
TDD tests for the /api/dashboard predictions-table fallback.

Context: the Siemens (main) dashboard cards are served from the synced_dashboard
table, which is only ever populated by an on-prem bridge POSTing a full
dashboard.json to /sync/dashboard. A cloud deploy fed only by the manual
push_data_to_cloud.py workflow uploads the `predictions` TABLE via
/api/admin/import/predictions, which the dashboard never rendered — so the
component cards came back empty while beam_trend/gauge_history still showed.

These tests pin the fallback that reconstructs component cards from the latest
predictions run for the lab when no synced_dashboard row exists.

Mirrors tests/test_dashboard_beam_widget.py for the TestClient + temp-DB +
dependency-override pattern.
"""
import json
import os
import tempfile
import uuid

_DB_PATH = os.path.join(tempfile.gettempdir(), f'petlab_predfallback_test_{uuid.uuid4().hex}.db')
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

main.app.dependency_overrides[get_current_user] = lambda: {'username': 't', 'lab_id': _LAB_ID}


def _insert_prediction(conn, run_at, component, risk, days, level, signal, reasons):
    conn.execute(
        "INSERT OR REPLACE INTO predictions "
        "(run_at, component, risk_score, days_estimate, alert_level, "
        " primary_signal, top_features, lab_id) VALUES (?,?,?,?,?,?,?,?)",
        [run_at, component, risk, days, level, signal, json.dumps(reasons), _LAB_ID],
    )


def test_components_reconstructed_from_predictions_when_never_synced(tmp_path, monkeypatch):
    """No synced_dashboard row, but the predictions table has rows (pushed via
    push_data_to_cloud.py) -> cards are reconstructed from the latest run."""
    db_path = str(tmp_path / "pred_fallback.db")
    init_db(db_path)
    init_cloud_tables(db_path)

    conn = get_conn(db_path)
    try:
        # An older run that must be ignored in favour of the latest run_at.
        _insert_prediction(conn, '2026-05-14', 'ION SOURCE', 0.9, 2.0, 'RED', 'MODEL', ['stale'])
        # Latest run.
        _insert_prediction(conn, '2026-06-23', 'ION SOURCE', 0.28, 26.0, 'GREEN', 'MODEL',
                           ['days since last maintenance: 32', 'Signal: AI_IS_CUR_7d_mean'])
        _insert_prediction(conn, '2026-06-23', 'BL1 Target 1', 1.0, 0.0, 'RED', 'COUNTER',
                           ['days since last maintenance: 74'])
        conn.execute(
            "INSERT INTO maintenance_events (timestamp, component_key, component_label, source_file, lab_id) "
            "VALUES (?,?,?,?,?)",
            ['2026-05-22T08:00:00Z', 'ion_source', 'ION SOURCE', 'x.log', _LAB_ID],
        )
        conn.commit()
    finally:
        conn.close()

    # Deliberately NO synced_dashboard row and no local dashboard.json.
    monkeypatch.setattr(dashboard, 'get_config', lambda: {'db_path': db_path, 'lab_id': _LAB_ID})

    with TestClient(main.app) as client:
        r = client.get('/api/dashboard')

    assert r.status_code == 200
    data = r.json()
    comps = {c['name']: c for c in data['components']}
    # Only the latest run's two components, not the stale 2026-05-14 row.
    assert set(comps) == {'ION SOURCE', 'BL1 Target 1'}
    ion = comps['ION SOURCE']
    assert ion['alert_level'] == 'GREEN'
    assert ion['days_estimate'] == 26.0
    assert ion['risk_score'] == 0.28
    assert ion['primary_signal'] == 'MODEL'
    assert 'Signal: AI_IS_CUR_7d_mean' in ion['top_reasons']
    assert ion['last_maintenance'] == '2026-05-22T08:00:00Z'
    # pct_life_used derived from AVG_CYCLES['ION SOURCE']=58 and 26 days remaining.
    assert ion['pct_life_used'] == int(100 * (58 - 26) / 58)
    # Fields the predictions table can't supply are null, not missing.
    assert ion['counter_days'] is None
    assert data['generated_at']


def test_no_components_when_no_predictions_and_never_synced(tmp_path, monkeypatch):
    """Preserve prior behaviour: genuinely empty (no predictions rows) -> []."""
    db_path = str(tmp_path / "empty.db")
    init_db(db_path)
    init_cloud_tables(db_path)

    monkeypatch.setattr(dashboard, 'get_config', lambda: {'db_path': db_path, 'lab_id': _LAB_ID})

    with TestClient(main.app) as client:
        r = client.get('/api/dashboard')

    assert r.status_code == 200
    assert r.json()['components'] == []


def test_synced_dashboard_still_wins_over_predictions(tmp_path, monkeypatch):
    """A real bridge push must take precedence over the predictions fallback."""
    db_path = str(tmp_path / "synced_wins.db")
    init_db(db_path)
    init_cloud_tables(db_path)

    conn = get_conn(db_path)
    try:
        _insert_prediction(conn, '2026-06-23', 'ION SOURCE', 0.28, 26.0, 'GREEN', 'MODEL', ['x'])
        conn.execute(
            "INSERT OR REPLACE INTO synced_dashboard (lab_id, payload, synced_at) VALUES (?,?,?)",
            [_LAB_ID, json.dumps({'generated_at': '2026-07-01T00:00:00Z',
                                  'components': [{'name': 'FROM_BRIDGE', 'alert_level': 'ORANGE'}]}),
             '2026-07-01T00:00:00Z'],
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(dashboard, 'get_config', lambda: {'db_path': db_path, 'lab_id': _LAB_ID})

    with TestClient(main.app) as client:
        r = client.get('/api/dashboard')

    assert r.status_code == 200
    names = [c['name'] for c in r.json()['components']]
    assert names == ['FROM_BRIDGE']
