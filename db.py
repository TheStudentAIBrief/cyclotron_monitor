import logging
import sqlite3

_log = logging.getLogger('cyclotron.db')

# Keep 30 days of raw events — enough for feature engineering (14-day window) + headroom.
# The events table grows at ~87k rows/day; without pruning it reaches 29M+ rows and
# makes feature engineering queries slow even with indexes.
EVENTS_RETENTION_DAYS = 30

SCHEMA = """
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
    raw_ocr_text TEXT    DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_gauge_lab_ts ON gauge_readings(lab_id, timestamp DESC);
CREATE TABLE IF NOT EXISTS push_tokens (
    token         TEXT PRIMARY KEY,
    lab_id        TEXT NOT NULL,
    platform      TEXT DEFAULT '',
    registered_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS beam_daily (
    date TEXT NOT NULL,
    param TEXT NOT NULL,
    mean REAL, std REAL, min REAL, max REAL, p10 REAL, p90 REAL,
    data_quality TEXT DEFAULT 'ok',
    PRIMARY KEY (date, param)
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
CREATE INDEX IF NOT EXISTS idx_events_code_ts ON events(code, timestamp);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_maint_label_ts ON maintenance_events(component_label, timestamp);
"""

def init_db(db_path: str):
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=EXTRA")  # max durability in WAL mode
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA secure_delete=ON")   # zero freed pages on delete
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()

def upsert_beam_daily(conn, date_str, param, stats, data_quality='ok'):
    conn.execute(
        "INSERT OR REPLACE INTO beam_daily VALUES (?,?,?,?,?,?,?,?,?)",
        [date_str, param, stats.get('mean'), stats.get('std'), stats.get('min'),
         stats.get('max'), stats.get('p10'), stats.get('p90'), data_quality]
    )

def insert_events(conn, rows):
    conn.executemany("INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?)", rows)


def prune_events(db_path: str, keep_days: int = EVENTS_RETENTION_DAYS) -> int:
    """Delete events older than keep_days using table-swap (much faster than row-level DELETE).

    The table-swap approach:
      1. INSERT recent rows into events_keep (uses timestamp index — fast)
      2. DROP the bloated events table (marks all pages free at once — fast)
      3. Rename events_keep → events + recreate indexes

    Call this from the watcher after each successful refresh so the table never
    accumulates more than ~keep_days × 87k rows ≈ 2.6M rows.
    """
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=keep_days)).strftime('%Y-%m-%d')

    # Quick check — skip if table is small (nothing to prune)
    conn = sqlite3.connect(db_path, timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        # Use the index: count only rows older than cutoff (fast range scan)
        old = conn.execute(
            "SELECT COUNT(*) FROM events WHERE timestamp < ?", [cutoff]
        ).fetchone()[0]
        if old == 0:
            return 0

        if old > 1_000_000:
            _log.warning(
                'events table has %s rows older than %s days — running table-swap prune',
                f'{old:,}', keep_days,
            )

        conn.execute("DROP TABLE IF EXISTS events_keep")
        conn.execute("""
            CREATE TABLE events_keep (
                timestamp   TEXT NOT NULL,
                severity    TEXT,
                code        TEXT,
                function    TEXT,
                message     TEXT,
                source_file TEXT,
                UNIQUE(timestamp, source_file, code, function)
            )
        """)
        conn.execute(
            "INSERT INTO events_keep "
            "SELECT timestamp, severity, code, function, message, source_file "
            "FROM events WHERE timestamp >= ?",
            [cutoff],
        )
        conn.execute("DROP TABLE events")
        conn.execute("ALTER TABLE events_keep RENAME TO events")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_code_ts ON events(code, timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp)")
        conn.commit()
        _log.info('prune_events: removed %s rows older than %s', f'{old:,}', cutoff)
        return old
    finally:
        conn.close()

def upsert_maintenance_event(conn, timestamp, component_key, component_label, source_file):
    conn.execute(
        "INSERT OR REPLACE INTO maintenance_events VALUES (?,?,?,?)",
        [timestamp, component_key, component_label, source_file]
    )
