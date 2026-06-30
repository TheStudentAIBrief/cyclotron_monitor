import csv
import gzip
import logging
import os
import sqlite3
import tempfile
from datetime import date as _date, datetime, timedelta
from pathlib import Path

_log = logging.getLogger('cyclotron.db')

# Keep 30 days of raw events in the live DB — enough for feature engineering
# (14-day window) + headroom. The IBA Cyclone 18/9 generates ~87k rows/day;
# without pruning the table reaches 29M+ rows and makes feature engineering slow.
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


# Migrations for gauge_readings extended columns (added after initial release).
# ALTER TABLE ... ADD COLUMN is idempotent via OperationalError suppression.
_GAUGE_MIGRATIONS = [
    "ALTER TABLE gauge_readings ADD COLUMN location    TEXT DEFAULT ''",
    "ALTER TABLE gauge_readings ADD COLUMN alert_lo    REAL",
    "ALTER TABLE gauge_readings ADD COLUMN alert_hi    REAL",
    "ALTER TABLE gauge_readings ADD COLUMN action_lo   REAL",
    "ALTER TABLE gauge_readings ADD COLUMN action_hi   REAL",
    "ALTER TABLE gauge_readings ADD COLUMN confidence  TEXT DEFAULT ''",
    "ALTER TABLE gauge_readings ADD COLUMN verified_by TEXT DEFAULT ''",
    "ALTER TABLE gauge_readings ADD COLUMN verified_at TEXT DEFAULT ''",
]


def init_db(db_path: str):
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=EXTRA")  # max durability in WAL mode
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA secure_delete=ON")   # zero freed pages on delete
    conn.executescript(SCHEMA)
    for sql in _GAUGE_MIGRATIONS:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # column already exists
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


def upsert_maintenance_event(conn, timestamp, component_key, component_label, source_file):
    conn.execute(
        "INSERT OR REPLACE INTO maintenance_events VALUES (?,?,?,?)",
        [timestamp, component_key, component_label, source_file]
    )


# ── Archive / prune ────────────────────────────────────────────────────────────

def _next_month(d: _date) -> _date:
    if d.month == 12:
        return _date(d.year + 1, 1, 1)
    return _date(d.year, d.month + 1, 1)


def archive_old_events(db_path: str, cutoff_date: str, archive_dir: str) -> int:
    """Export events older than cutoff_date to monthly gzip CSV files.

    Design:
    - Uses MIN/MAX to discover date range in O(1) via the timestamp index —
      avoids a DISTINCT scan over millions of rows.
    - One file per calendar month: events_YYYY_MM.csv.gz
    - Writes are atomic: temp file → os.replace() on the same filesystem.
    - Months that already have an archive file are skipped — the IBA Cyclone
      only inserts current-timestamp events, so old months are immutable.
    - If any month write fails, the exception propagates; prune_events() will
      abort the prune rather than delete un-archived data.

    Returns total rows written to new archive files (0 = all already archived).
    """
    os.makedirs(archive_dir, exist_ok=True)
    cutoff_month = cutoff_date[:7]  # YYYY-MM — don't archive the partial cutoff month

    conn = sqlite3.connect(db_path, timeout=120)
    try:
        bounds = conn.execute(
            "SELECT MIN(timestamp), MAX(timestamp) FROM events WHERE timestamp < ?",
            [cutoff_date],
        ).fetchone()
        if not bounds[0]:
            return 0

        # Build month list in Python — no GROUP BY / DISTINCT on the large table
        start = _date.fromisoformat(bounds[0][:10]).replace(day=1)
        end   = _date.fromisoformat(bounds[1][:10]).replace(day=1)

        months = []
        m = start
        while m <= end:
            tag = m.strftime('%Y-%m')
            if tag >= cutoff_month:
                break
            months.append((tag, m.isoformat(), _next_month(m).isoformat()))
            m = _next_month(m)

        total = 0
        for tag, m_start, m_end in months:
            archive_path = os.path.join(archive_dir, f'events_{tag.replace("-", "_")}.csv.gz')
            if os.path.exists(archive_path):
                continue  # already archived — immutable month, safe to skip

            rows = conn.execute(
                "SELECT timestamp, severity, code, function, message, source_file "
                "FROM events WHERE timestamp >= ? AND timestamp < ? ORDER BY timestamp",
                [m_start, m_end],
            ).fetchall()

            if not rows:
                continue

            # Atomic write: temp file in same directory → os.replace()
            tmp_fd, tmp_path = tempfile.mkstemp(
                prefix='.events_tmp_', suffix='.csv.gz', dir=archive_dir,
            )
            try:
                os.close(tmp_fd)
                with gzip.open(tmp_path, 'wt', encoding='utf-8', newline='') as f:
                    w = csv.writer(f)
                    w.writerow(['timestamp', 'severity', 'code', 'function',
                                'message', 'source_file'])
                    w.writerows(rows)
                os.replace(tmp_path, archive_path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

            total += len(rows)
            _log.info('archive_old_events: %s → %s rows', tag, f'{len(rows):,}')

    finally:
        conn.close()

    return total


def prune_events(db_path: str, keep_days: int = EVENTS_RETENTION_DAYS,
                 archive_dir: str | None = None) -> int:
    """Archive then delete events older than keep_days using a table-swap.

    The table-swap is O(kept_rows) not O(total_rows) — much faster than
    DELETE on 27M rows because it only writes the 2.6M rows we keep, then
    drops the old table in one operation.

    Safety contract: if archive_dir is provided and archiving fails for any
    reason, the prune is ABORTED — we never delete data that wasn't archived.

    Call this from the watcher after each successful refresh to maintain the
    retention window automatically.
    """
    cutoff = (datetime.now() - timedelta(days=keep_days)).strftime('%Y-%m-%d')

    conn = sqlite3.connect(db_path, timeout=60)
    try:
        old = conn.execute(
            "SELECT COUNT(*) FROM events WHERE timestamp < ?", [cutoff]
        ).fetchone()[0]
        if old == 0:
            return 0
    finally:
        conn.close()

    # Archive first — abort prune if this fails
    if archive_dir:
        try:
            archived = archive_old_events(db_path, cutoff, archive_dir)
            if archived:
                _log.info('prune_events: archived %s rows before pruning', f'{archived:,}')
        except Exception as exc:
            _log.error(
                'prune_events: archive to %s failed (%s) — prune aborted to preserve data',
                archive_dir, exc,
            )
            return 0

    # Table-swap: keep only recent events
    conn = sqlite3.connect(db_path, timeout=60)
    try:
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
