"""GET /api/records/* rejects absurdly high page numbers (MED-31 hardening) —
SQLite's OFFSET scans and discards every preceding row, so an unbounded page
number is an authenticated full-table-scan DoS vector."""
import os
import tempfile

os.environ.setdefault('DATABASE_PATH', os.path.join(tempfile.gettempdir(), 'petlab_records_page_test.db'))

from fastapi.testclient import TestClient  # noqa: E402

from api import config as _config  # noqa: E402
_config.get_config.cache_clear()

import api.main as main  # noqa: E402
from api.auth import get_current_user  # noqa: E402
from api.db_cloud import init_cloud_tables  # noqa: E402


def test_absurdly_high_page_number_is_rejected(tmp_path, monkeypatch):
    db_path = str(tmp_path / 'records.db')
    monkeypatch.setenv('DATABASE_PATH', db_path)
    _config.get_config.cache_clear()
    prev_override = main.app.dependency_overrides.get(get_current_user)
    main.app.dependency_overrides[get_current_user] = lambda: {'username': 't', 'lab_id': 'lab1'}
    try:
        init_cloud_tables(db_path)
        with TestClient(main.app) as c:
            r = c.get('/api/records/maintenance', params={'page': 10**12})
        assert r.status_code == 422
    finally:
        _config.get_config.cache_clear()
        main.app.dependency_overrides.pop(get_current_user, None)
        if prev_override is not None:
            main.app.dependency_overrides[get_current_user] = prev_override


def test_normal_page_number_still_works(tmp_path, monkeypatch):
    db_path = str(tmp_path / 'records2.db')
    monkeypatch.setenv('DATABASE_PATH', db_path)
    _config.get_config.cache_clear()
    prev_override = main.app.dependency_overrides.get(get_current_user)
    main.app.dependency_overrides[get_current_user] = lambda: {'username': 't', 'lab_id': 'lab1'}
    try:
        init_cloud_tables(db_path)
        with TestClient(main.app) as c:
            r = c.get('/api/records/maintenance', params={'page': 1})
        assert r.status_code == 200
    finally:
        _config.get_config.cache_clear()
        main.app.dependency_overrides.pop(get_current_user, None)
        if prev_override is not None:
            main.app.dependency_overrides[get_current_user] = prev_override
