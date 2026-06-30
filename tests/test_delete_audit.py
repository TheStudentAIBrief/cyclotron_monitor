"""DEL-1 hardening: deleting a gauge reading must leave an NNR audit trail.

The deletion records the actor and the full prior content in audit_log before the
row is removed, so a regulated record cannot disappear without a trace.
"""
import os
import tempfile

os.environ.setdefault('DATABASE_PATH', os.path.join(tempfile.gettempdir(), 'petlab_delete_audit_test.db'))

from fastapi.testclient import TestClient  # noqa: E402

from api import config as _config  # noqa: E402
_config.get_config.cache_clear()

import api.main as main  # noqa: E402
from api.auth import get_current_user  # noqa: E402
from api.db_cloud import get_conn, init_cloud_tables  # noqa: E402

main.app.dependency_overrides[get_current_user] = lambda: {'username': 'tester', 'lab_id': 'petlabs-pretoria'}


def test_delete_writes_audit_entry_with_prior_content():
    # Set the actor at run-time (not import) so a sibling test module's override can't clobber it.
    main.app.dependency_overrides[get_current_user] = lambda: {'username': 'tester', 'lab_id': 'petlabs-pretoria'}
    db = os.environ['DATABASE_PATH']
    init_cloud_tables(db)
    conn = get_conn(db)
    conn.execute("DELETE FROM gauge_readings WHERE id=4242")   # clean slate across reruns
    conn.execute(
        "INSERT INTO gauge_readings (id, lab_id, gauge_name, timestamp, value, unit) "
        "VALUES (?,?,?,?,?,?)",
        [4242, 'petlabs-pretoria', '0096', '2026-01-01T00:00:00Z', 12.3, 'Pa'],
    )
    conn.commit()
    conn.close()

    with TestClient(main.app) as client:
        r = client.delete('/api/gauges/4242')
    assert r.status_code == 200

    conn = get_conn(db)
    gone = conn.execute("SELECT COUNT(*) AS n FROM gauge_readings WHERE id=4242").fetchone()['n']
    audit = conn.execute(
        "SELECT actor, detail FROM audit_log WHERE action='delete_gauge_reading' ORDER BY id DESC"
    ).fetchall()
    conn.close()

    assert gone == 0                              # the reading is removed
    assert len(audit) >= 1                        # ...but the deletion is recorded
    assert audit[0]['actor'] == 'tester'          # who did it
    assert '0096' in audit[0]['detail']           # prior content preserved


def test_delete_missing_reading_is_404():
    with TestClient(main.app) as client:
        r = client.delete('/api/gauges/999999')
    assert r.status_code == 404
