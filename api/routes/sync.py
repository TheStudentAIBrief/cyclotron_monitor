"""
Server-to-server sync endpoint — called by monitor/cloud_sync.py on the on-prem machine.
Protected by X-Sync-Key header (not JWT); never expose this key in the mobile app.

Whoever holds the key can replace the dashboard every user sees, so:
  - nothing is read or parsed for a caller who has not shown the key;
  - only something shaped like a dashboard is stored (see Dashboard below);
  - a second key is accepted while rotating (CLOUD_SYNC_KEY_NEXT), so the key
    can be changed without the sync stopping;
  - the first sync from each address is written to the audit log, and
    SYNC_ALLOWED_SOURCES can restrict syncs to known addresses.
"""
import hmac
import ipaddress
import json as _json
import logging
import math
import os
import threading
import time
from datetime import datetime, timezone
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, ValidationError, field_validator

from api import audit
from api.config import get_config
from api.db_cloud import get_conn
from api.ratelimit import client_address

router = APIRouter()
_log = logging.getLogger('uvicorn.error')

# A real dashboard is a few kilobytes.
MAX_BODY_BYTES = 256 * 1024

# ── what a dashboard looks like ──────────────────────────────────────────────
# Mirrors monitor/dashboard_writer.write_dashboard. Unknown fields are dropped
# rather than refused, so a facility PC on slightly older or newer code still
# syncs; what is stored is this model written back out, never the raw body.
# Types are exact: text is never read as a number or a number as text.


def _number(value):
    """A JSON number, or None. NaN and infinity become "missing": the writer can
    emit them, and they are not valid JSON for the app's parser."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('must be a number')
    try:
        value = float(value)
    except OverflowError:
        raise ValueError('number out of range')
    return value if math.isfinite(value) else None


def _text(value):
    if not isinstance(value, str) or not value.isprintable():
        raise ValueError('must be a single line of printable text')
    return value


_Number = Annotated[Optional[float], BeforeValidator(_number)]
_Line = Annotated[str, StringConstraints(max_length=40), BeforeValidator(_text)]


class Component(BaseModel):
    model_config = ConfigDict(extra='ignore')

    name: Annotated[str, StringConstraints(min_length=1, max_length=60), BeforeValidator(_text)]
    risk_score: _Number = None
    days_estimate: _Number = None
    alert_level: Literal['RED', 'ORANGE', 'YELLOW', 'GREEN']
    pct_life_used: _Number = None
    last_maintenance: Optional[_Line] = None
    top_reasons: list[Annotated[str, StringConstraints(max_length=200), BeforeValidator(_text)]] = Field(
        default_factory=list, max_length=5)
    counter_days: _Number = None
    primary_signal: Optional[Literal['COUNTER', 'MODEL', 'BOTH', 'COUNTER_ONLY', 'MODEL_OVERRIDE']] = None
    warning: Optional[Annotated[str, StringConstraints(max_length=600), BeforeValidator(_text)]] = None
    trained_at: Optional[_Line] = None
    model_age_days: _Number = None
    model_days_estimate: _Number = None
    model_risk: _Number = None


class Dashboard(BaseModel):
    model_config = ConfigDict(extra='ignore')

    generated_at: _Line
    components: list[Component] = Field(max_length=50)

    @field_validator('generated_at')
    @classmethod
    def _is_a_time(cls, value: str) -> str:
        datetime.fromisoformat(value)   # ValueError -> refused
        return value


# ── where a sync comes from ──────────────────────────────────────────────────

MAX_KNOWN_SOURCES = 16
# Audit entries about sources, per hour, for this process: a key holder could
# otherwise fill the audit log by syncing from address after address. There are
# two separate allowances, so that refused sources cannot use up what new
# sources need:
#   'new'      a source not seen before. Once the allowance is spent, a sync from
#              yet another new source is REFUSED until the hour is up -- so every
#              source that has ever replaced the dashboard is named in the log.
#   'refused'  a source outside SYNC_ALLOWED_SOURCES. Once spent, further refused
#              sources go to the server log only (they changed nothing).
# In both cases one last entry says the allowance ran out.
MAX_SOURCE_ENTRIES_PER_HOUR = 20
_budgets = {kind: {'started': 0.0, 'used': 0, 'seen': set()} for kind in ('new', 'refused')}
_budget_lock = threading.Lock()


def reset_audit_budget() -> None:
    with _budget_lock:
        for budget in _budgets.values():
            budget.update(started=0.0, used=0, seen=set())


def _spend(kind: str, source: str) -> str:
    """'record', 'last' (record that the allowance has run out) or 'skip'."""
    with _budget_lock:
        budget = _budgets[kind]
        now = time.monotonic()
        if now - budget['started'] > 3600:
            budget.update(started=now, used=0, seen=set())
        if kind == 'refused':
            if source in budget['seen']:
                return 'skip'              # this address was already recorded this hour
            budget['seen'].add(source)
        budget['used'] += 1
        if budget['used'] <= MAX_SOURCE_ENTRIES_PER_HOUR:
            return 'record'
        return 'last' if budget['used'] == MAX_SOURCE_ENTRIES_PER_HOUR + 1 else 'skip'


def _record(conn, kind: str, action: str, lab_id: str, source: str, detail: str) -> bool:
    """Write a source entry if the hourly allowance permits; True if `detail` itself was written."""
    outcome = _spend(kind, source)
    if outcome == 'record':
        audit.write(conn, action, 'sync', lab_id, detail)
        return True
    if outcome == 'last':
        audit.write(conn, action, 'sync', lab_id,
                    f'more than {MAX_SOURCE_ENTRIES_PER_HOUR} such sources in the last hour; '
                    + ('further new sources are refused until the hour is up' if kind == 'new' else
                       'further refused sources are in the server log only until the hour is up'))
    return False


def _exact_ip(address: str):
    """The caller's IP address (port and brackets removed; an IPv4 address carried
    inside IPv6 is read as IPv4), or None if it is not an address."""
    host = address.strip()
    if host.startswith('['):
        host = host[1:].split(']', 1)[0]
    elif host.count(':') == 1:
        host = host.split(':', 1)[0]
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return None
    return ip.ipv4_mapped or ip if ip.version == 6 else ip


def _source_of(address: str) -> str:
    """How a caller is named in the audit log and the list of known sources: its
    IPv4 address, or its IPv6 /64 (one site has billions of addresses in its /64)."""
    ip = _exact_ip(address)
    if ip is None:
        return address.strip()[:64] or 'unknown'
    return str(ip) if ip.version == 4 else str(ipaddress.ip_network(f'{ip}/64', strict=False))


def _source_allowed(address: str) -> bool:
    """True if SYNC_ALLOWED_SOURCES is unset, or lists this address (addresses or
    CIDR ranges, comma-separated). A list that cannot be read allows nothing."""
    listed = os.environ.get('SYNC_ALLOWED_SOURCES', '').strip()
    if not listed:
        return True
    ip = _exact_ip(address)
    try:
        allowed = [ipaddress.ip_network(item.strip(), strict=False) for item in listed.split(',') if item.strip()]
    except ValueError:
        allowed = None
    if ip is None or allowed is None:
        _log.error('sync: SYNC_ALLOWED_SOURCES or the caller address could not be read - refusing the sync')
        return False
    return any(ip in net for net in allowed)


def _require_sync_key(request: Request, x_sync_key: Optional[str] = Header(None, alias='X-Sync-Key')) -> str:
    """The caller's source (one per IPv4 address or IPv6 /64) if it may sync, else 404.

    404 for every failure -- missing header, wrong key, unconfigured key, or a
    source that is not allowed -- so the reply never says which check failed.
    Runs before the request body is read, so nothing is parsed for a caller
    without the key."""
    cfg = get_config()
    # Constant-time comparison -- a plain `!=` short-circuits on the first differing
    # byte, letting a network attacker recover cloud_sync_key one byte at a time via
    # request timing (same class of issue BOOTSTRAP_PASSWORD is protected against
    # in api/auth.py's _verify_password).
    keys = [k for k in (cfg.get('cloud_sync_key', ''), cfg.get('cloud_sync_key_next', '')) if k]
    matched = False
    for expected in keys:   # no early exit: compare against both whatever the outcome
        matched |= bool(x_sync_key) and hmac.compare_digest(x_sync_key.encode(), expected.encode())
    if not matched:
        raise HTTPException(status_code=404)

    address = client_address(request)
    source = _source_of(address)
    if not _source_allowed(address):
        _log.warning('sync: refused a sync with a valid key from %s (not in SYNC_ALLOWED_SOURCES)', source)
        conn = get_conn(cfg['db_path'])
        try:
            _record(conn, 'refused', 'sync_refused_source', cfg.get('lab_id', 'default'), source,
                    f'a sync with a valid key came from {source}, which is not in SYNC_ALLOWED_SOURCES')
            conn.commit()
        finally:
            conn.close()
        raise HTTPException(status_code=404)
    return source


def _known_sources(row) -> dict[str, int]:
    """Each known source and how many syncs it has made."""
    try:
        known = _json.loads(row['sources']) if row and row['sources'] else {}
    except (ValueError, RecursionError):
        return {}
    if isinstance(known, list):
        known = {s: 1 for s in known}
    if not isinstance(known, dict):
        return {}
    return {s: n for s, n in known.items() if isinstance(n, int) and not isinstance(n, bool) and n > 0}


def _remember(known: dict[str, int], source: str) -> dict[str, int]:
    """Count this sync. When the list is full, the least-used source goes, and
    among equals the newest -- so an address in regular use (the facility's) is
    never pushed out by a burst of one-off sources."""
    known = dict(known)
    known[source] = min(known.get(source, 0) + 1, 10 ** 9)
    while len(known) > MAX_KNOWN_SOURCES:
        others = [s for s in known if s != source]
        fewest = min(known[s] for s in others)
        del known[[s for s in others if known[s] == fewest][-1]]
    return known


def _store(payload: Dashboard, source: str) -> dict:
    cfg = get_config()
    lab_id = cfg.get('lab_id', 'default')
    ts = datetime.now(timezone.utc).isoformat(timespec='seconds')
    conn = get_conn(cfg['db_path'])
    try:
        conn.execute('BEGIN IMMEDIATE')
        known = _known_sources(
            conn.execute("SELECT sources FROM synced_dashboard WHERE lab_id=?", [lab_id]).fetchone())
        if source not in known:
            _log.warning('sync: dashboard sync from a source not seen before: %s', source)
            if not _record(conn, 'new', 'sync_source_new', lab_id, source,
                           f'first dashboard sync seen from {source}'):
                # Too many new sources this hour. Refused rather than let one
                # replace the dashboard without being named in the audit log.
                conn.commit()                  # keeps the "allowance ran out" entry, if one was just written
                raise HTTPException(status_code=404)
        known = _remember(known, source)
        conn.execute(
            "INSERT OR REPLACE INTO synced_dashboard (lab_id, payload, synced_at, sources) VALUES (?,?,?,?)",
            [lab_id, _json.dumps(payload.model_dump(), allow_nan=False), ts, _json.dumps(known)],
        )
        conn.commit()
        return {'status': 'ok', 'lab_id': lab_id, 'synced_at': ts}
    finally:
        conn.close()


@router.post('/sync/dashboard')
async def sync_dashboard(request: Request, source: str = Depends(_require_sync_key)):
    """Accept a full dashboard JSON payload from the on-prem data bridge."""
    declared = request.headers.get('content-length', '')
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail='Too large to be a dashboard')
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail='Too large to be a dashboard')
    try:
        payload = Dashboard.model_validate(_json.loads(body))
    except ValidationError as exc:
        # Where it failed and why, never the rejected value itself.
        problems = exc.errors(include_input=False, include_url=False, include_context=False)
        raise HTTPException(status_code=422, detail=[{'loc': list(p['loc']), 'msg': p['msg']} for p in problems])
    except (ValueError, RecursionError):
        raise HTTPException(status_code=422, detail='Not JSON')
    return await run_in_threadpool(_store, payload, source)
