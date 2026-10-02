import sqlite3

from api import audit

_ADD_AUDIT_HASH = "ALTER TABLE audit_log ADD COLUMN hash TEXT"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS gauge_readings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    lab_id       TEXT    NOT NULL,
    gauge_name   TEXT    DEFAULT '',
    timestamp    TEXT    NOT NULL,
    value        REAL,
    unit         TEXT    DEFAULT '',
    is_alert     INTEGER DEFAULT 0,
    alert_reason TEXT    DEFAULT '',
    photo_path   TEXT    DEFAULT '',
    raw_ocr_text TEXT    DEFAULT '',
    location     TEXT    DEFAULT '',
    alert_lo     REAL,
    alert_hi     REAL,
    action_lo    REAL,
    action_hi    REAL,
    confidence   TEXT    DEFAULT '',
    verified_by  TEXT    DEFAULT '',
    verified_at  TEXT    DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_gauge_lab_ts ON gauge_readings(lab_id, timestamp DESC);

CREATE TABLE IF NOT EXISTS push_tokens (
    token         TEXT PRIMARY KEY,
    lab_id        TEXT NOT NULL,
    platform      TEXT DEFAULT '',
    registered_at TEXT NOT NULL
);

-- Populated by the on-prem data bridge (monitor/cloud_sync.py).
-- Stores the full dashboard JSON blob per lab; GET /api/dashboard reads from here.
CREATE TABLE IF NOT EXISTS synced_dashboard (
    lab_id    TEXT PRIMARY KEY,
    payload   TEXT NOT NULL,
    synced_at TEXT NOT NULL,
    sources   TEXT              -- JSON list of addresses syncs have come from (api/routes/sync.py)
);

-- PETrace 800 (PET Labs Pretoria) — one row per production batch.
CREATE TABLE IF NOT EXISTS petrace_batches (
    batch_no       INTEGER PRIMARY KEY,
    batch_date     TEXT    NOT NULL,
    tracer_num     INTEGER DEFAULT 0,
    tracer_name    TEXT    DEFAULT '',
    site           TEXT    DEFAULT '',
    duration_s     REAL    DEFAULT 0,
    row_count      INTEGER DEFAULT 0,
    foil_no        INTEGER,
    peak_target_uA REAL    DEFAULT 0,
    avg_target_uA  REAL    DEFAULT 0,
    total_muAh     REAL    DEFAULT 0,
    avg_arc_I      REAL    DEFAULT 0,
    avg_vacuum_P   REAL    DEFAULT 0,
    peak_vacuum_P  REAL    DEFAULT 0,
    rf_efficiency  REAL    DEFAULT 0,
    ingested_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_petrace_date ON petrace_batches(batch_date DESC);

-- Daily-aggregated beam-parameter stats (mirrors the local db.py schema).
-- Populated by a one-time/periodic push from the on-prem machine — Render never
-- runs ingest.py itself. dashboard.py's _beam_trend() tolerates this table not
-- existing yet (returns [] rather than erroring).
CREATE TABLE IF NOT EXISTS beam_daily (
    date TEXT NOT NULL,
    param TEXT NOT NULL,
    mean REAL, std REAL, min REAL, max REAL, p10 REAL, p90 REAL,
    data_quality TEXT DEFAULT 'ok',
    PRIMARY KEY (date, param)
);

-- Records tab data (mirrors the local db.py schema) — same one-time/periodic
-- push model as beam_daily above, via /api/admin/import/*.
CREATE TABLE IF NOT EXISTS maintenance_events (
    timestamp TEXT NOT NULL,
    component_key TEXT NOT NULL,
    component_label TEXT NOT NULL,
    source_file TEXT,
    PRIMARY KEY (timestamp, component_key)
);
CREATE TABLE IF NOT EXISTS predictions (
    run_at TEXT NOT NULL,
    component TEXT NOT NULL,
    risk_score REAL,
    days_estimate REAL,
    alert_level TEXT,
    primary_signal TEXT,
    top_features TEXT,
    PRIMARY KEY (run_at, component)
);
CREATE TABLE IF NOT EXISTS events (
    timestamp TEXT NOT NULL,
    severity TEXT,
    code TEXT,
    function TEXT,
    message TEXT,
    source_file TEXT,
    UNIQUE(timestamp, source_file, code, function)
);

-- Append-only audit log (NNR). Records deletions/mutations of regulated records:
-- who did what, when, and the prior content — so a record can't vanish without a trace.
CREATE TABLE IF NOT EXISTS audit_log (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     TEXT NOT NULL,
    action TEXT NOT NULL,
    lab_id TEXT,
    actor  TEXT,
    detail TEXT,
    -- Hash chain (api/audit.py): each entry hashes its own content plus the
    -- previous entry's hash, so an edit or a removal is detectable. NULL on
    -- entries written before the chain existed.
    prev_hash TEXT,
    hash      TEXT
);

-- JWT revocation list. A token's jti lands here on explicit logout, letting
-- get_current_user()/get_refresh_payload() reject it immediately instead of
-- trusting it until its natural expiry (up to 7 days for a refresh token).
CREATE TABLE IF NOT EXISTS revoked_tokens (
    jti        TEXT PRIMARY KEY,
    expires_at TEXT NOT NULL,
    revoked_at TEXT NOT NULL
);

-- Per-person accounts with a role (viewer < operator < admin; see api/auth.py's
-- require_role). The original single login in data/.credentials.json is NOT
-- stored here -- it stays as a built-in admin so this table being empty (every
-- deploy before RBAC existed) can never lock the lab out.
CREATE TABLE IF NOT EXISTS users (
    username      TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL,
    disabled      INTEGER NOT NULL DEFAULT 0,
    -- Baked into each token; bumped on password reset / enable / disable so
    -- older tokens stop working. Starts at 1 so a per-person token can never be
    -- mistaken for the built-in admin's (always 0).
    token_version INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    created_by    TEXT NOT NULL DEFAULT ''
);
"""


_MIGRATIONS = [
    "ALTER TABLE gauge_readings ADD COLUMN location TEXT DEFAULT ''",
    "ALTER TABLE gauge_readings ADD COLUMN alert_lo REAL",
    "ALTER TABLE gauge_readings ADD COLUMN alert_hi REAL",
    "ALTER TABLE gauge_readings ADD COLUMN action_lo REAL",
    "ALTER TABLE gauge_readings ADD COLUMN action_hi REAL",
    "ALTER TABLE gauge_readings ADD COLUMN confidence TEXT DEFAULT ''",
    "ALTER TABLE gauge_readings ADD COLUMN verified_by TEXT DEFAULT ''",
    "ALTER TABLE gauge_readings ADD COLUMN verified_at TEXT DEFAULT ''",
    # These three tables were never scoped by lab -- structurally unable to
    # isolate labs from each other's records (F-004 hardening). Backfilled
    # to 'petlabs-pretoria' since that's the only lab_id this deployment has
    # ever run under; new rows get it from the server's own config, not client
    # input (see api/routes/admin_import.py).
    "ALTER TABLE maintenance_events ADD COLUMN lab_id TEXT NOT NULL DEFAULT 'petlabs-pretoria'",
    "ALTER TABLE predictions ADD COLUMN lab_id TEXT NOT NULL DEFAULT 'petlabs-pretoria'",
    "ALTER TABLE events ADD COLUMN lab_id TEXT NOT NULL DEFAULT 'petlabs-pretoria'",
    "ALTER TABLE synced_dashboard ADD COLUMN sources TEXT",
    # For any database whose users table predates token_version (dev/test DBs
    # created while RBAC was being built).
    "ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 1",
    # Existing deployments: audit_log predates the hash chain (api/audit.py).
    "ALTER TABLE audit_log ADD COLUMN prev_hash TEXT",
    _ADD_AUDIT_HASH,
]

_POST_MIGRATION_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_maint_lab_ts ON maintenance_events(lab_id, timestamp DESC)",
    "CREATE INDEX IF NOT EXISTS idx_predictions_lab_run ON predictions(lab_id, run_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_events_lab_ts ON events(lab_id, timestamp DESC)",
]


def init_cloud_tables(db_path: str) -> None:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")     # readers don't block on writers
    conn.execute("PRAGMA secure_delete=ON")     # zero freed pages on delete
    conn.executescript(_SCHEMA)
    # One transaction for every migration below, so a start-up that dies part-way
    # leaves nothing behind and the next one retries from scratch. Without it each
    # ALTER commits by itself -- and a hash column added without the existing
    # entries being chained would be skipped ("column already exists") forever.
    try:
        conn.execute('BEGIN IMMEDIATE')
        for sql in _MIGRATIONS:
            try:
                conn.execute(sql)
            except sqlite3.OperationalError:
                continue  # column already exists
            if sql == _ADD_AUDIT_HASH:
                # This is the one start-up on which the hash column appears, so chain
                # the entries that already exist now. Doing it only here means a log
                # whose hashes are later blanked is never quietly re-chained.
                audit.chain_existing(conn)
        for sql in _POST_MIGRATION_INDEXES:
            conn.execute(sql)  # index creation is idempotent regardless of column history
        conn.commit()
    finally:
        conn.close()   # closing without a commit rolls the migrations back


def get_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn
