"""POST /auth/login is rate-limited per IP to block brute-force/credential-stuffing."""
import os
import tempfile

os.environ.setdefault('DATABASE_PATH', os.path.join(tempfile.gettempdir(), 'petlab_login_rl_test.db'))

from fastapi.testclient import TestClient  # noqa: E402

from api import config as _config  # noqa: E402
_config.get_config.cache_clear()

import api.main as main  # noqa: E402


def test_login_is_blocked_after_too_many_attempts(monkeypatch):
    monkeypatch.setattr(main, '_LOGIN_MAX_ATTEMPTS_PER_MINUTE', 3)
    main._login_rate_counts.clear()  # isolate from any other test's attempts

    with TestClient(main.app) as c:
        statuses = [
            c.post('/auth/login', data={'username': 'nobody', 'password': 'wrong'}).status_code
            for _ in range(5)
        ]

    # first 3 attempts are evaluated normally (401 — wrong credentials, no such user);
    # once the per-minute cap is exceeded, further attempts are rejected with 429
    # before credentials are even checked.
    assert statuses[:3] == [401, 401, 401]
    assert statuses[3:] == [429, 429]

    main._login_rate_counts.clear()
