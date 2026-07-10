"""Tests for the server-to-server POST /sync/dashboard endpoint (X-Sync-Key auth).

No existing test file covered this route before the 2026-07-02 self-pentest found
its key comparison used a non-constant-time `!=` (timing side-channel on
cloud_sync_key). These lock in both the existing 404-for-every-failure-mode
behavior and the constant-time comparison fix.
"""
import os
import tempfile

os.environ.setdefault('DATABASE_PATH', os.path.join(tempfile.gettempdir(), 'petlab_sync_test.db'))
os.environ['CLOUD_SYNC_KEY'] = 'test-sync-key-0123456789'

from fastapi.testclient import TestClient  # noqa: E402

from api import config as _config  # noqa: E402
_config.get_config.cache_clear()

import api.main as main  # noqa: E402
import api.routes.sync as sync  # noqa: E402
from api.db_cloud import init_cloud_tables  # noqa: E402

init_cloud_tables(os.environ['DATABASE_PATH'])

_VALID_KEY = 'test-sync-key-0123456789'


def test_sync_dashboard_accepts_correct_key():
    with TestClient(main.app) as client:
        r = client.post(
            '/sync/dashboard',
            json={'hello': 'world'},
            headers={'X-Sync-Key': _VALID_KEY},
        )
    assert r.status_code == 200
    assert r.json()['status'] == 'ok'


def test_sync_dashboard_rejects_wrong_key_with_404():
    with TestClient(main.app) as client:
        r = client.post(
            '/sync/dashboard',
            json={'hello': 'world'},
            headers={'X-Sync-Key': 'wrong-key'},
        )
    # 404, not 403 -- deliberately indistinguishable from a nonexistent route.
    assert r.status_code == 404


def test_sync_dashboard_rejects_missing_key_with_404():
    with TestClient(main.app) as client:
        r = client.post('/sync/dashboard', json={'hello': 'world'})
    assert r.status_code == 404


def test_sync_key_comparison_is_constant_time(monkeypatch):
    """Regression test for the self-pentest finding: the comparison must go
    through hmac.compare_digest, not Python's short-circuiting `!=`."""
    calls = []
    real_compare_digest = sync.hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real_compare_digest(a, b)

    monkeypatch.setattr(sync.hmac, 'compare_digest', spy)

    with TestClient(main.app) as client:
        client.post(
            '/sync/dashboard',
            json={'hello': 'world'},
            headers={'X-Sync-Key': 'wrong-key'},
        )

    assert len(calls) == 1
    assert calls[0] == ('wrong-key', _VALID_KEY)
