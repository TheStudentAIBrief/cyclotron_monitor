"""VULN-0003 (2026-07-09 pentest): /api/gauges/import-csv had no row-count
limit - only a byte-size cap (_MAX_CSV_BYTES), which a file of short rows can
blow past by row count long before hitting the byte limit. This caused a real
production incident (150,001 garbage rows inserted, cleaned up separately).
"""
import io
import os
import tempfile

os.environ.setdefault('DATABASE_PATH', os.path.join(tempfile.gettempdir(), 'petlab_csv_row_limit_test.db'))

from fastapi.testclient import TestClient  # noqa: E402

from api import config as _config  # noqa: E402
_config.get_config.cache_clear()

import api.main as main  # noqa: E402
from api.auth import get_current_user  # noqa: E402
from api.db_cloud import get_conn, init_cloud_tables  # noqa: E402
from api.routes import gauges  # noqa: E402

main.app.dependency_overrides[get_current_user] = lambda: {'username': 't', 'lab_id': 'petlabs-pretoria'}


def _csv_with_n_rows(n):
    lines = ['gauge,location,date,value_Pa,alert_lo,alert_hi,action_lo,action_hi,confidence,created_by,verified_by,verified_at']
    lines += [f'G{i},LOC,2026-01-01,1.0,,,,,,import,,' for i in range(n)]
    return '\n'.join(lines).encode()


def test_csv_over_row_limit_is_rejected(tmp_path, monkeypatch):
    db_path = str(tmp_path / 'csv_limit.db')
    monkeypatch.setenv('DATABASE_PATH', db_path)
    _config.get_config.cache_clear()
    init_cloud_tables(db_path)

    csv_bytes = _csv_with_n_rows(gauges._MAX_CSV_ROWS + 500)
    with TestClient(main.app) as client:
        r = client.post(
            '/api/gauges/import-csv',
            files={'file': ('big.csv', io.BytesIO(csv_bytes), 'text/csv')},
        )
    assert r.status_code == 413
    assert 'row' in r.json()['detail'].lower()

    conn = get_conn(db_path)
    count = conn.execute("SELECT COUNT(*) FROM gauge_readings").fetchone()[0]
    conn.close()
    assert count == 0  # rejected before any commit - nothing partially inserted


def test_csv_within_row_limit_still_works(tmp_path, monkeypatch):
    db_path = str(tmp_path / 'csv_limit_ok.db')
    monkeypatch.setenv('DATABASE_PATH', db_path)
    _config.get_config.cache_clear()
    init_cloud_tables(db_path)

    csv_bytes = _csv_with_n_rows(10)
    with TestClient(main.app) as client:
        r = client.post(
            '/api/gauges/import-csv',
            files={'file': ('small.csv', io.BytesIO(csv_bytes), 'text/csv')},
        )
    assert r.status_code == 200
    assert r.json()['inserted'] == 10
