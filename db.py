import csv
import glob
import gzip
import hashlib
import io
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
#
# The events table is the regulated fault record and the monthly archive files
# are its permanent copy. ONE RULE governs everything below: an event may only
# be removed from the table once it is readable in an archive file.

# An event whose timestamp is empty (or starts with anything that sorts below a
# digit) cannot be placed in a month, so it can be neither archived nor judged
# "old". Such rows are left in the table for good: a single one used to make the
# archive step see "nothing to archive" while the prune went on to delete every
# old event regardless.
_DATED = "timestamp >= '0'"
_UNDATED = "timestamp < '0'"

_EVENT_COLUMNS = "timestamp, severity, code, function, message, source_file"


def _next_month(d: _date) -> _date:
    if d.month == 12:
        return _date(d.year + 1, 1, 1)
    return _date(d.year, d.month + 1, 1)


def _event_key(values) -> bytes:
    """Identity of an event as an archive file stores it (its CSV text form)."""
    h = hashlib.blake2b(digest_size=16)
    for v in values:
        data = ('' if v is None else str(v)).encode('utf-8')
        h.update(len(data).to_bytes(4, 'big'))   # length-prefixed: no field-boundary ambiguity
        h.update(data)
    return h.digest()


def _archived_keys(paths) -> set:
    """Keys of every event in the given archive files. Raises if a file cannot be
    read -- a damaged archive must stop the prune, never be taken on trust."""
    keys = set()
    for path in paths:
        with gzip.open(path, 'rt', encoding='utf-8', newline='') as f:
            reader = csv.reader(f)
            next(reader, None)   # header
            for row in reader:
                keys.add(_event_key(row))
    return keys


def archive_old_events(db_path: str, cutoff_date: str, archive_dir: str) -> int:
    """Make sure every dated event in a complete month before the cutoff month is
    in a gzip CSV archive file, writing whatever is not there yet.

    Design:
    - Uses MIN/MAX to discover date range in O(1) via the timestamp index —
      avoids a DISTINCT scan over millions of rows.
    - One file per calendar month: events_YYYY_MM.csv.gz
    - Writes are atomic and durable: temp file → fsync → os.replace(). The rows
      are deleted straight afterwards, so the file must really be on disk first.
    - An existing archive file is never rewritten and never trusted blind. When a
      month that already has files also has events in the table (an old log
      ingested later, or a run that archived but did not get to delete), the
      files are read back and only events they do NOT contain are written, to a
      supplementary events_YYYY_MM_lateN.csv.gz. Skipping the month because "it
      already has a file" let prune_events() delete those events unarchived.
    - Rows are streamed, never loaded a month at a time (a busy month is millions
      of rows).
    - Undated events (see _DATED) are not archived here; prune_events() never
      deletes them either.
    - If anything fails, the exception propagates; prune_events() then deletes
      nothing and removes the files this run wrote.

    Returns the number of events confirmed to be in the archive: every dated
    event older than the start of the cutoff month. prune_events() relies on
    that being exactly what it is about to delete.
    """
    os.makedirs(archive_dir, exist_ok=True)
    cutoff_month = cutoff_date[:7]  # YYYY-MM — don't archive the partial cutoff month

    conn = sqlite3.connect(db_path, timeout=120)
    try:
        bounds = conn.execute(
            f"SELECT MIN(timestamp), MAX(timestamp) FROM events WHERE {_DATED} AND timestamp < ?",
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
            stem = os.path.join(archive_dir, f'events_{tag.replace("-", "_")}')
            existing = [p for p in (f'{stem}.csv.gz', *sorted(glob.glob(f'{glob.escape(stem)}_late*.csv.gz')))
                        if os.path.exists(p)]
            rows = conn.execute(
                f"SELECT {_EVENT_COLUMNS} FROM events "
                "WHERE timestamp >= ? AND timestamp < ? ORDER BY timestamp",
                [m_start, m_end],
            )
            first = rows.fetchone()
            if first is None:
                continue
            already = _archived_keys(existing) if existing else set()

            in_table = written = 0
            tmp_path = raw = text = None
            try:
                row = first
                while row is not None:
                    in_table += 1
                    if not already or _event_key(row) not in already:
                        if text is None:
                            # Atomic write: temp file in same directory → os.replace()
                            tmp_fd, tmp_path = tempfile.mkstemp(
                                prefix='.events_tmp_', suffix='.csv.gz', dir=archive_dir)
                            raw = os.fdopen(tmp_fd, 'wb')
                            text = io.TextIOWrapper(gzip.GzipFile(fileobj=raw, mode='wb'),
                                                    encoding='utf-8', newline='')
                            writer = csv.writer(text)
                            writer.writerow(['timestamp', 'severity', 'code', 'function',
                                             'message', 'source_file'])
                        writer.writerow(row)
                        written += 1
                    row = rows.fetchone()
                if text is not None:
                    text.close()                 # finishes the gzip stream (leaves `raw` open)
                    raw.flush()
                    os.fsync(raw.fileno())       # on disk before anything is deleted
                    raw.close()
                    raw = None
                    archive_path, late = f'{stem}.csv.gz', 0
                    while os.path.exists(archive_path):   # never rewrite an existing archive
                        late += 1
                        archive_path = f'{stem}_late{late}.csv.gz'
                    os.replace(tmp_path, archive_path)
                    _log.info('archive_old_events: %s → %s rows (%s)', tag, f'{written:,}',
                              os.path.basename(archive_path))
            except Exception:
                for handle in (text, raw):
                    try:
                        if handle is not None:
                            handle.close()
                    except Exception:
                        pass
                try:
                    if tmp_path is not None:
                        os.unlink(tmp_path)
                except OSError:
                    pass
                raise

            total += in_table

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

    archive_old_events() only ever archives whole calendar months strictly
    before the cutoff month (it deliberately skips the partial cutoff month —
    a still-partial month can't be written once and left alone). Deleting
    everything before the exact cutoff DATE would therefore silently drop the
    unarchived partial-month days. When archiving, the delete boundary is
    rounded back to the start of the cutoff month so nothing is ever deleted
    that wasn't guaranteed to be archived first.

    The delete is also tied to what the archive step actually covered: with the
    write lock held, the rows about to go are counted again, and if that is not
    exactly the number just confirmed in the archive (events arrived in between,
    or anything else unexpected) nothing is deleted and the next run tries again.
    Whenever a run ends without deleting, the archive files it wrote are removed
    again, so a run that keeps failing cannot fill the disk with copies. Only one
    prune runs at a time (a lock file in archive_dir): two at once could each
    delete rows the other archived. Undated events (see _DATED) are never deleted.

    Never raises for a busy or locked database — it returns 0 and the next call
    tries again. Call this from the watcher after each successful refresh to
    maintain the retention window automatically.
    """
    cutoff = (datetime.now() - timedelta(days=keep_days)).strftime('%Y-%m-%d')
    delete_cutoff = f'{cutoff[:7]}-01' if archive_dir else cutoff
    old_sql = f"SELECT COUNT(*) FROM events WHERE {_DATED} AND timestamp < ?"

    conn = sqlite3.connect(db_path, timeout=60)
    try:
        old = conn.execute(old_sql, [delete_cutoff]).fetchone()[0]
        if old == 0:
            return 0
    finally:
        conn.close()

    if not archive_dir:
        return _swap_out_old_events(db_path, old_sql, delete_cutoff, keep_days, expected=None)

    os.makedirs(archive_dir, exist_ok=True)
    # Released automatically if this process dies, unlike a plain lock file.
    lock = sqlite3.connect(os.path.join(archive_dir, '.prune.lock'), timeout=0)
    try:
        try:
            lock.execute('BEGIN EXCLUSIVE')
        except sqlite3.OperationalError:
            _log.warning('prune_events: another prune is already running — skipping this run')
            return 0

        before = set(os.listdir(archive_dir))
        removed = 0
        try:
            # Archive first — abort prune if this fails
            archived = archive_old_events(db_path, cutoff, archive_dir)
            if archived:
                _log.info('prune_events: %s rows confirmed in the archive before pruning', f'{archived:,}')
            removed = _swap_out_old_events(db_path, old_sql, delete_cutoff, keep_days, expected=archived)
        except Exception as exc:
            _log.error(
                'prune_events: archive to %s failed (%s) — prune aborted to preserve data',
                archive_dir, exc,
            )
        if not removed:
            # Nothing was deleted, so every event is still in the table: the files
            # this run wrote are not needed, and leaving them would add a full
            # duplicate set on every failed attempt.
            for name in set(os.listdir(archive_dir)) - before:
                if name.endswith('.csv.gz'):
                    try:
                        os.unlink(os.path.join(archive_dir, name))
                    except OSError:
                        pass
        return removed
    finally:
        lock.close()


def _swap_out_old_events(db_path: str, old_sql: str, delete_cutoff: str, keep_days: int,
                         expected: int | None) -> int:
    """Remove dated events before delete_cutoff with a table swap; returns how many.

    `expected` is the number of events the archive step confirmed; if the rows now
    due for deletion are not exactly that many, nothing is deleted (0 is returned).
    None means no archive is configured. A locked database also returns 0."""
    conn = sqlite3.connect(db_path, timeout=60)
    try:
        # Lock out other writers, then make sure the rows about to be deleted are
        # exactly the rows that were just archived.
        conn.execute('BEGIN IMMEDIATE')
        old = conn.execute(old_sql, [delete_cutoff]).fetchone()[0]
        if expected is not None and expected != old:
            _log.error(
                'prune_events: %s rows are due for deletion but %s were confirmed in the archive — '
                'nothing deleted; will retry on the next run',
                f'{old:,}', f'{expected:,}',
            )
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
        # Two indexed range reads (recent events, then undated ones) rather than
        # one OR, which would scan the whole table under the write lock.
        conn.execute(
            f"INSERT INTO events_keep SELECT {_EVENT_COLUMNS} FROM events WHERE timestamp >= ? "
            f"UNION ALL SELECT {_EVENT_COLUMNS} FROM events WHERE {_UNDATED}",
            [delete_cutoff],
        )
        conn.execute("DROP TABLE events")
        conn.execute("ALTER TABLE events_keep RENAME TO events")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_code_ts ON events(code, timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp)")
        conn.commit()
        _log.info('prune_events: removed %s rows older than %s', f'{old:,}', delete_cutoff)
        return old
    except sqlite3.OperationalError as exc:
        # Typically "database is locked" (a long ingest holding the write lock).
        # Nothing was committed; the caller treats 0 as "try again next time".
        _log.warning('prune_events: could not prune this time (%s) — will retry on the next run', exc)
        return 0
    finally:
        conn.close()
