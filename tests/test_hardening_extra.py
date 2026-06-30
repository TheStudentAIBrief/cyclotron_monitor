"""Extra hardening from the external report: docs disabled, finite-value validation, cloud WAL."""
import os
import tempfile

os.environ.setdefault('DATABASE_PATH', os.path.join(tempfile.gettempdir(), 'petlab_extra_test.db'))

from fastapi.testclient import TestClient  # noqa: E402

from api import config as _config  # noqa: E402
_config.get_config.cache_clear()

import api.main as main  # noqa: E402
from api.auth import get_current_user  # noqa: E402
from api.db_cloud import get_conn, init_cloud_tables  # noqa: E402

main.app.dependency_overrides[get_current_user] = lambda: {'username': 't', 'lab_id': 'petlabs-pretoria'}


def test_openapi_and_docs_are_disabled():
    with TestClient(main.app) as c:
        assert c.get('/openapi.json').status_code == 404
        assert c.get('/docs').status_code == 404
        assert c.get('/redoc').status_code == 404


def test_manual_reading_rejects_non_finite_value():
    # 1e309 overflows to +inf when parsed — must be rejected, not stored.
    with TestClient(main.app) as c:
        r = c.post('/api/gauges', content='{"gauge_name":"0096","value":1e309}',
                   headers={'content-type': 'application/json'})
    assert r.status_code == 422


def test_cloud_db_uses_wal_journal():
    db = os.environ['DATABASE_PATH']
    init_cloud_tables(db)
    conn = get_conn(db)
    mode = conn.execute('PRAGMA journal_mode').fetchone()[0]
    conn.close()
    assert mode.lower() == 'wal'
