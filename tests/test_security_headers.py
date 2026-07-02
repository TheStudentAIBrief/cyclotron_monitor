"""Regression tests for the security-headers middleware added 2026-07-02
(self-pentest finding: production sent no CSP/X-Frame-Options/HSTS/etc. at all,
even though this same FastAPI app serves the login page and every facility
dashboard, not just the JSON API)."""
import os
import tempfile

os.environ.setdefault('DATABASE_PATH', os.path.join(tempfile.gettempdir(), 'petlab_secheaders_test.db'))

from fastapi.testclient import TestClient  # noqa: E402

from api import config as _config  # noqa: E402
_config.get_config.cache_clear()

import api.main as main  # noqa: E402


def test_health_response_has_hardening_headers():
    with TestClient(main.app) as client:
        r = client.get('/health')
    assert r.status_code == 200
    assert r.headers['X-Content-Type-Options'] == 'nosniff'
    assert r.headers['X-Frame-Options'] == 'DENY'
    assert r.headers['Referrer-Policy'] == 'no-referrer'
    assert 'max-age=31536000' in r.headers['Strict-Transport-Security']
    assert r.headers['Content-Security-Policy']


def test_csp_default_src_is_self_only():
    with TestClient(main.app) as client:
        r = client.get('/health')
    csp = r.headers['Content-Security-Policy']
    assert "default-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp


def test_csp_script_src_permits_the_pwas_own_inline_hydration_script():
    """The built PWA's index.html has one inline <script type="module"> (Expo
    Router's hydration flag). The CSP must include a matching sha256 source for
    it, computed from the real file on disk, not a value that could silently
    go stale after a future `expo export` rebuild."""
    index_path = main._WEB_BUILD_DIR / 'index.html'
    if not index_path.is_file():
        import pytest
        pytest.skip('mobile/dist/index.html not built in this environment')

    expected_sources = main._inline_script_csp_sources(index_path)
    assert expected_sources, 'expected at least one inline <script> hash to compute'

    with TestClient(main.app) as client:
        r = client.get('/health')
    csp = r.headers['Content-Security-Policy']
    for source in expected_sources.split(' '):
        assert source in csp
