"""In-process per-caller rate limiting for unauthenticated routes.

Same shape as the login limiter in api/main.py: a one-minute window per key,
held in memory, so it assumes a single server process (true for this app's
deployment). Nothing here is shared between processes or survives a restart.
"""
import ipaddress
import os
import threading
import time

from fastapi import Request

_MAX_TRACKED_KEYS = 1024   # bounds memory when a caller cycles through many addresses
_MAX_KEY_LEN = 64


class PerMinuteCounter:
    """Counts events per key in a one-minute window."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[str, tuple[int, float]] = {}

    def count(self, key: str) -> int:
        """How many events this key has had in its current window."""
        with self._lock:
            n, started = self._counts.get(key, (0, 0.0))
            return n if time.monotonic() - started <= 60.0 else 0

    def add(self, key: str) -> int:
        """Record one event; returns the count including it."""
        with self._lock:
            now = time.monotonic()
            n, started = self._counts.get(key, (0, 0.0))
            if now - started > 60.0:
                if len(self._counts) > _MAX_TRACKED_KEYS:
                    for k in [k for k, (_, s) in self._counts.items() if now - s > 60.0]:
                        del self._counts[k]
                n, started = 0, now
            self._counts[key] = (n + 1, started)
            return n + 1

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()


def _normalise(address: str) -> str:
    """One key per caller: drop a port, and treat an IPv6 /64 as a single caller
    (anyone on IPv6 has billions of addresses inside their own /64)."""
    address = address.strip()[:_MAX_KEY_LEN]
    host = address
    if host.startswith('['):                     # [v6]:port
        host = host[1:].split(']', 1)[0]
    elif host.count(':') == 1:                   # v4:port
        host = host.split(':', 1)[0]
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return address
    if ip.version == 6:
        return str(ipaddress.ip_network(f'{ip}/64', strict=False))
    return str(ip)


def forwarded_for(request: Request) -> list[str]:
    """Every X-Forwarded-For header line, as received."""
    return request.headers.getlist('x-forwarded-for')


def client_key(request: Request, trust_forwarded: bool | None = None) -> str:
    """Who to count a request against.

    By default the address the connection came from. Behind a reverse proxy that
    is the proxy, so every visitor shares one allowance; set TRUST_FORWARDED_FOR=1
    there to use the LAST address in X-Forwarded-For instead -- the one the proxy
    itself added, whether it appended to the caller's header line or sent a line
    of its own. Addresses a caller supplies land in front of it and are ignored,
    so the header can't be used to dodge a limit. Leave it unset when clients
    connect directly, or the header would be entirely theirs to set.
    """
    return _normalise(client_address(request, trust_forwarded))


def client_address(request: Request, trust_forwarded: bool | None = None) -> str:
    """The caller's address as received (see client_key for which one that is).
    client_key folds an IPv6 /64 into one key; this does not, for callers that
    must match an exact address."""
    if trust_forwarded is None:
        trust_forwarded = os.environ.get('TRUST_FORWARDED_FOR') == '1'
    if trust_forwarded:
        last = ','.join(forwarded_for(request)).rsplit(',', 1)[-1].strip()
        if last:
            return last[:_MAX_KEY_LEN]
    return request.client.host if request.client else 'unknown'
