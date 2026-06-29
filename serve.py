"""
Secure HTTP server for the cyclotron dashboard.
Replaces `python -m http.server`.  Run via: python main.py serve

Security controls:
  - HTTP Basic Authentication (PBKDF2-SHA256, 600k iterations) — requires setup_credentials.py
  - TLS (HTTPS) — requires setup_tls.py; upgrades to HTTPS automatically when cert exists
  - Whitelist-only file serving (no directory listing, no path traversal)
  - OWASP-recommended HTTP security headers on every response
  - /api/dashboard.json served from configured path; JSON validated before forwarding
  - Localhost-only binding (127.0.0.1:8080/8443); non-GET/HEAD returns 405
  - Rate limiting: 60 requests/minute per IP; dict capped at 1024 entries
  - Server version header scrubbed (Python version not disclosed)
  - Structured access log at data/serve_access.log

Setup:
  python setup_credentials.py      # creates data/.credentials.json
  python setup_tls.py              # creates data/tls/cert.pem + key.pem
  python main.py serve             # auto-upgrades to HTTPS when TLS files exist
"""
import base64
import hashlib
import hmac
import http.server
import json
import logging
import secrets
import ssl
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

_MAX_SERVER_THREADS = 32

_MAX_REQUESTS_PER_MINUTE = 60
_MAX_TRACKED_IPS = 1024
_REALM = 'Cyclotron Monitor'

# Whitelist: URL path → (filename relative to ui_dir, MIME type)
_STATIC: dict[str, tuple[str, str]] = {
    '/':              ('index.html',    'text/html; charset=utf-8'),
    '/index.html':    ('index.html',    'text/html; charset=utf-8'),
    '/patterns.html': ('patterns.html', 'text/html; charset=utf-8'),
    '/style.css':     ('style.css',     'text/css; charset=utf-8'),
    '/app.js':        ('app.js',        'application/javascript; charset=utf-8'),
}

_SEC_HEADERS = [
    ('Content-Security-Policy',
     "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
     "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none';"),
    ('X-Content-Type-Options',            'nosniff'),
    ('X-Frame-Options',                   'DENY'),
    ('Referrer-Policy',                   'no-referrer'),
    ('Permissions-Policy',                'geolocation=(), microphone=(), camera=()'),
    ('Cache-Control',                     'no-store, no-cache, must-revalidate, private'),
    ('Pragma',                            'no-cache'),
    ('X-Permitted-Cross-Domain-Policies', 'none'),
]

# ── Authentication ────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Hash a password with PBKDF2-SHA256, 600 000 iterations.  Returned value is safe to store."""
    salt = secrets.token_bytes(32)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 600_000)
    payload = salt + dk
    return base64.b64encode(payload).decode('ascii')


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verification of a PBKDF2-SHA256 stored hash."""
    try:
        data = base64.b64decode(stored)
        salt, dk_stored = data[:32], data[32:]
        dk_attempt = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 600_000)
        return hmac.compare_digest(dk_attempt, dk_stored)
    except Exception:
        return False


def load_credentials(path: str) -> dict[str, str] | None:
    """Load {username: stored_hash} from a credentials JSON file.  Returns None if not found."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        with open(p, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return {data['username']: data['hash']}
    except (KeyError, json.JSONDecodeError, OSError):
        return None


# ── Session store ────────────────────────────────────────────────────────────
# Browsers don't forward HTTP Basic Auth credentials in JavaScript fetch() calls,
# only in full-page navigations.  Solution: issue a session cookie on first
# successful Basic Auth; the browser sends it automatically with every fetch().

_SESSION_TTL = 8 * 3600.0  # 8 hours (session cookie, no Max-Age = cleared on browser close)
_sessions: dict[str, float] = {}  # token → expiry (time.monotonic())
_session_lock = Lock()


def _new_session() -> str:
    token = secrets.token_urlsafe(32)
    with _session_lock:
        _sessions[token] = time.monotonic() + _SESSION_TTL
        if len(_sessions) > 256:
            now = time.monotonic()
            dead = [k for k, exp in list(_sessions.items()) if exp < now]
            for k in dead:
                del _sessions[k]
    return token


def _valid_session(token: str) -> bool:
    with _session_lock:
        exp = _sessions.get(token)
        if exp is None:
            return False
        if time.monotonic() > exp:
            del _sessions[token]
            return False
        return True


# ── Rate limiting ─────────────────────────────────────────────────────────────

_rate_lock = Lock()
_rate_counts: dict[str, tuple[int, float]] = defaultdict(lambda: (0, 0.0))


def _rate_ok(ip: str) -> bool:
    with _rate_lock:
        count, window_start = _rate_counts[ip]
        now = time.monotonic()
        if now - window_start > 60.0:
            if len(_rate_counts) > _MAX_TRACKED_IPS:
                expired = [k for k, (_, ws) in list(_rate_counts.items()) if now - ws > 60.0]
                for k in expired:
                    del _rate_counts[k]
            _rate_counts[ip] = (1, now)
            return True
        if count >= _MAX_REQUESTS_PER_MINUTE:
            return False
        _rate_counts[ip] = (count + 1, window_start)
        return True


# ── Request handler ───────────────────────────────────────────────────────────

_log = logging.getLogger('cyclotron.serve')


class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = 'CyclotronMonitor/1.0'
    sys_version = ''  # suppresses "Python/3.x" from Server response header

    dashboard_path: str = ''
    ui_dir: Path = Path('.')
    credentials: dict[str, str] | None = None  # None = auth disabled
    tls_active: bool = False  # set True when SSL context is configured

    def log_message(self, fmt, *args):
        _log.info('[%s] %s', self.address_string(), fmt % args)

    def log_error(self, fmt, *args):
        _log.warning('[%s] %s', self.address_string(), fmt % args)

    # ── response helpers ──────────────────────────────────────────────────────

    def _send(self, code: int, ctype: str, body: bytes,
              extra_headers: list[tuple[str, str]] | None = None):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        for k, v in _SEC_HEADERS:
            self.send_header(k, v)
        if self.tls_active:
            self.send_header('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
        if extra_headers:
            for k, v in extra_headers:
                self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _deny(self, code: int, msg: str, extra_headers: list[tuple[str, str]] | None = None):
        self.send_response(code)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        body = msg.encode()
        self.send_header('Content-Length', str(len(body)))
        for k, v in _SEC_HEADERS:
            self.send_header(k, v)
        if self.tls_active:
            self.send_header('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
        if extra_headers:
            for k, v in extra_headers:
                self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _cookie_header(self, token: str) -> tuple[str, str]:
        """Build a Set-Cookie header for a new session token."""
        value = f'cm_session={token}; HttpOnly; SameSite=Strict; Path=/'
        if self.tls_active:
            value += '; Secure'
        return ('Set-Cookie', value)

    # ── auth check ────────────────────────────────────────────────────────────

    def _check_auth(self) -> tuple[bool, str | None]:
        """Return (ok, new_token).

        new_token is non-None when Basic Auth just succeeded — caller must set a
        session cookie so subsequent JavaScript fetch() calls carry auth without
        the browser needing to re-send the Authorization header.
        """
        creds = self.credentials
        if creds is None:
            return True, None  # auth disabled

        # 1. Session cookie — issued on previous successful Basic Auth.
        #    Browsers send cookies automatically with same-origin fetch() calls,
        #    which is the only reliable way to authenticate programmatic requests.
        for part in self.headers.get('Cookie', '').split(';'):
            name, _, val = part.strip().partition('=')
            if name == 'cm_session' and val and _valid_session(val):
                return True, None

        # 2. HTTP Basic Auth — initial browser-dialog authentication.
        auth = self.headers.get('Authorization', '')
        if not auth.startswith('Basic '):
            return False, None

        try:
            decoded = base64.b64decode(auth[6:]).decode('utf-8')
            username, _, password = decoded.partition(':')
        except Exception:
            return False, None

        stored = creds.get(username)
        if stored is None:
            # Constant-time dummy verify to prevent username enumeration via timing
            verify_password(password, base64.b64encode(secrets.token_bytes(64)).decode())
            return False, None

        if verify_password(password, stored):
            return True, _new_session()  # issue session cookie
        return False, None

    # ── routing ───────────────────────────────────────────────────────────────

    def _handle(self):
        if not _rate_ok(self.address_string()):
            self._deny(429, 'Too Many Requests')
            return

        ok, new_token = self._check_auth()
        if not ok:
            self._deny(401, 'Unauthorized', [
                ('WWW-Authenticate', f'Basic realm="{_REALM}", charset="UTF-8"')
            ])
            return

        # If just authenticated via Basic Auth, attach a session cookie so
        # JavaScript fetch() calls on this page work without re-sending Basic Auth.
        cookie_headers = [self._cookie_header(new_token)] if new_token else None

        path = self.path.split('?')[0].split('#')[0]

        if path == '/api/dashboard.json':
            self._serve_json(cookie_headers)
            return

        entry = _STATIC.get(path)
        if not entry:
            self._deny(404, 'Not Found')
            return

        rel, mime = entry
        self._serve_file(rel, mime, cookie_headers)

    def _serve_json(self, cookie_headers=None):
        try:
            with open(self.dashboard_path, 'r', encoding='utf-8') as fh:
                obj = json.load(fh)
            body = json.dumps(obj, separators=(',', ':')).encode()
            self._send(200, 'application/json; charset=utf-8', body, cookie_headers)
        except FileNotFoundError:
            self._deny(404, 'Not Found')
        except (json.JSONDecodeError, OSError):
            self._deny(500, 'Internal Server Error')

    def _serve_file(self, rel_name: str, mime: str, cookie_headers=None):
        target = (self.ui_dir / rel_name).resolve()
        try:
            target.relative_to(self.ui_dir.resolve())
        except ValueError:
            self._deny(403, 'Forbidden')
            return
        if not target.is_file():
            self._deny(404, 'Not Found')
            return
        self._send(200, mime, target.read_bytes(), cookie_headers)

    def do_GET(self):     self._handle()
    def do_HEAD(self):    self._handle()
    def do_POST(self):    self._deny(405, 'Method Not Allowed')
    def do_PUT(self):     self._deny(405, 'Method Not Allowed')
    def do_DELETE(self):  self._deny(405, 'Method Not Allowed')
    def do_OPTIONS(self): self._deny(405, 'Method Not Allowed')
    def do_PATCH(self):   self._deny(405, 'Method Not Allowed')


# ── Bounded thread-pool HTTP server ──────────────────────────────────────────

class _BoundedHTTPServer(http.server.HTTPServer):
    """HTTPServer backed by a fixed-size thread pool.

    Python's ThreadingHTTPServer spawns an unbounded number of threads — one per
    request — which can exhaust memory under sustained load.  This class routes
    each accepted connection into a ThreadPoolExecutor with a hard worker cap,
    so concurrent request count is bounded at _MAX_SERVER_THREADS.
    """

    def __init__(self, *args, max_workers: int = _MAX_SERVER_THREADS, **kwargs):
        super().__init__(*args, **kwargs)
        self._pool = ThreadPoolExecutor(max_workers=max_workers)

    def process_request(self, request, client_address):
        self._pool.submit(self._handle_in_thread, request, client_address)

    def _handle_in_thread(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)

    def server_close(self):
        self._pool.shutdown(wait=False)
        super().server_close()


# ── Server entry point ────────────────────────────────────────────────────────

def start_server(dashboard_path: str, ui_dir: Path,
                 host: str = '127.0.0.1', port: int = 8080,
                 log_path: str | None = None,
                 credentials_path: str | None = None,
                 tls_dir: str | None = None):
    if log_path:
        fh = logging.FileHandler(log_path, encoding='utf-8')
        fh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        _log.addHandler(fh)
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
    _log.addHandler(ch)
    _log.setLevel(logging.INFO)

    _Handler.dashboard_path = str(dashboard_path)
    _Handler.ui_dir = ui_dir.resolve()

    if credentials_path:
        creds = load_credentials(credentials_path)
        if creds:
            _Handler.credentials = creds
            _log.info('Authentication enabled (%d user(s) loaded).', len(creds))
        else:
            _log.warning(
                'Credentials file not found or invalid. '
                'Run: python setup_credentials.py  '
                'Authentication is DISABLED until credentials are configured.'
            )
            _Handler.credentials = None
    else:
        _log.warning(
            'No credentials_path configured. '
            'Authentication is DISABLED. '
            'Run: python setup_credentials.py'
        )
        _Handler.credentials = None

    # Auto-detect TLS: if data/tls/cert.pem + key.pem exist, upgrade to HTTPS
    tls_ctx = None
    if tls_dir:
        cert = Path(tls_dir) / 'cert.pem'
        key  = Path(tls_dir) / 'key.pem'
        if cert.is_file() and key.is_file():
            tls_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            tls_ctx.load_cert_chain(certfile=str(cert), keyfile=str(key))
            _Handler.tls_active = True
            # Use 8443 for HTTPS when 8080 is the default HTTP port
            if port == 8080:
                port = 8443
            _log.info('TLS enabled — cert: %s', cert)
        else:
            _log.warning(
                'TLS cert/key not found in %s. '
                'Run: python setup_tls.py  '
                'Server is using HTTP (unencrypted).',
                tls_dir
            )

    scheme = 'https' if tls_ctx else 'http'
    with _BoundedHTTPServer((host, port), _Handler) as srv:
        if tls_ctx:
            srv.socket = tls_ctx.wrap_socket(srv.socket, server_side=True)
        _log.info('Serving on %s://%s:%d/', scheme, host, port)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
