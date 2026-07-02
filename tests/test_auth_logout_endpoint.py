"""POST /auth/logout revokes the access token used to call it (JWT revocation hardening)."""
import os
import tempfile

os.environ.setdefault('DATABASE_PATH', os.path.join(tempfile.gettempdir(), 'petlab_logout_test.db'))

from fastapi.testclient import TestClient  # noqa: E402

from api import config as _config  # noqa: E402
_config.get_config.cache_clear()

import api.main as main  # noqa: E402
from api.auth import create_tokens, get_current_user  # noqa: E402
from api.db_cloud import init_cloud_tables  # noqa: E402


def test_logout_revokes_the_access_token(tmp_path, monkeypatch):
    # Other test modules install a dependency_overrides lambda for get_current_user
    # on this same shared app instance at import time and never remove it — clear
    # it here so this test exercises the real auth/revocation path, not a bypass,
    # then restore it so later-running test modules aren't affected.
    prev_override = main.app.dependency_overrides.pop(get_current_user, None)

    db_path = str(tmp_path / 'logout.db')
    init_cloud_tables(db_path)
    monkeypatch.setenv('DATABASE_PATH', db_path)
    _config.get_config.cache_clear()
    try:
        toks = create_tokens('tester', 'petlabs-pretoria')
        with TestClient(main.app) as c:
            r = c.post('/auth/logout', headers={'Authorization': f"Bearer {toks['access_token']}"})
            assert r.status_code == 200

            # the same token must now be rejected everywhere, not just re-usable for logout
            r2 = c.get('/api/dashboard', headers={'Authorization': f"Bearer {toks['access_token']}"})
            assert r2.status_code == 401
    finally:
        _config.get_config.cache_clear()
        if prev_override is not None:
            main.app.dependency_overrides[get_current_user] = prev_override


def test_logout_requires_a_valid_token(tmp_path, monkeypatch):
    prev_override = main.app.dependency_overrides.pop(get_current_user, None)

    db_path = str(tmp_path / 'logout_noauth.db')
    init_cloud_tables(db_path)
    monkeypatch.setenv('DATABASE_PATH', db_path)
    _config.get_config.cache_clear()
    try:
        with TestClient(main.app) as c:
            r = c.post('/auth/logout')
            assert r.status_code == 401
    finally:
        _config.get_config.cache_clear()
        if prev_override is not None:
            main.app.dependency_overrides[get_current_user] = prev_override
