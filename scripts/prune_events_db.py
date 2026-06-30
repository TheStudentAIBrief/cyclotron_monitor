"""
One-time pruning script: archive then shrink the events table.

WHAT THIS DOES:
  1. ARCHIVES all events older than 30 days to monthly gzip CSV files in
     data/events_archive/events_YYYY_MM.csv.gz — one file per calendar month.
     Files already present are skipped (idempotent).
  2. TABLE-SWAP: replaces the bloated events table with one containing only
     the last 30 days, then rebuilds indexes.
  3. VACUUM: rewrites the DB file on disk to reclaim ~7 GB of freed space.

WHAT IS PRESERVED:
  - data/events_archive/ — the complete NNR audit trail (indefinite retention)
  - maintenance_events  — all rows untouched
  - predictions         — all rows untouched
  - beam_daily          — all rows untouched
  - gauge_readings      — all rows untouched
  - petrace_batches     — all rows untouched

HOW TO RUN:
  1. Stop the API:     Ctrl+C in the uvicorn terminal
  2. Stop the watcher: Ctrl+C in the python main.py terminal
  3. Run: python scripts/prune_events_db.py
  4. Restart API and watcher as normal

TIME ESTIMATE:
  - Archive (27M rows, 47 months): ~10-20 minutes
  - Table-swap (copy 2.6M recent rows): ~30 seconds
  - VACUUM (rewrite 7.8 GB file): ~2-5 minutes
  Total: 15-30 minutes

DISK NEEDED: ~1 GB free (for the new compact DB file during VACUUM).
ARCHIVE SIZE: ~200-400 MB total in data/events_archive/ (gzip-compressed).

This script is safe to re-run. It skips months that are already archived
and the table-swap is idempotent.
"""
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path so we can import db.py
sys.path.insert(0, str(Path(__file__).parent.parent))
from db import archive_old_events, EVENTS_RETENTION_DAYS

DB = Path(__file__).parent.parent / 'data' / 'cyclotron.db'
DB = DB.resolve()
ARCHIVE_DIR = DB.parent / 'events_archive'
KEEP_DAYS = EVENTS_RETENTION_DAYS

print(f"DB:          {DB}")
print(f"Archive dir: {ARCHIVE_DIR}")
print(f"Retention:   last {KEEP_DAYS} days\n")

if not DB.exists():
    print("ERROR: DB file not found. Run from the cyclotron_monitor directory.")
    sys.exit(1)

size_gb = DB.stat().st_size / 1024 / 1024 / 1024
cutoff = (datetime.now() - timedelta(days=KEEP_DAYS)).strftime('%Y-%m-%d')

conn = sqlite3.connect(str(DB), timeout=30)
keep = conn.execute("SELECT COUNT(*) FROM events WHERE timestamp >= ?", [cutoff]).fetchone()[0]
old  = conn.execute("SELECT COUNT(*) FROM events WHERE timestamp < ?",  [cutoff]).fetchone()[0]
conn.close()

print(f"Current DB size:    {size_gb:.1f} GB")
print(f"Events to archive:  {old:,}  (before {cutoff})")
print(f"Events to keep:     {keep:,}  (from {cutoff} onwards)")
print()
print("Already-archived months in data/events_archive/ will be skipped.")
print()
answer = input("Type 'yes' to proceed: ").strip().lower()
if answer != 'yes':
    print("Aborted.")
    sys.exit(0)

print()

# ── Step 1: Archive ────────────────────────────────────────────────────────────
print("Step 1: Archiving old events to monthly gzip CSV files...")
t0 = time.perf_counter()
archived = archive_old_events(str(DB), cutoff, str(ARCHIVE_DIR))
t1 = time.perf_counter()

archive_files = sorted(ARCHIVE_DIR.glob('events_*.csv.gz'))
archive_size_mb = sum(f.stat().st_size for f in archive_files) / 1024 / 1024
print(f"  {archived:,} new rows archived in {t1-t0:.0f}s")
print(f"  {len(archive_files)} monthly archive files  ({archive_size_mb:.0f} MB total)")
print()

# ── Step 2: Table-swap ────────────────────────────────────────────────────────
print("Step 2: Table-swap — copy recent rows to a fresh table, drop the old one...")
t0 = time.perf_counter()
conn2 = sqlite3.connect(str(DB), timeout=600)
conn2.execute("PRAGMA journal_mode=WAL")
conn2.execute("DROP TABLE IF EXISTS events_keep")
conn2.execute("""
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
conn2.execute(
    "INSERT INTO events_keep "
    "SELECT timestamp, severity, code, function, message, source_file "
    "FROM events WHERE timestamp >= ?",
    [cutoff],
)
conn2.execute("DROP TABLE events")
conn2.execute("ALTER TABLE events_keep RENAME TO events")
conn2.execute("CREATE INDEX IF NOT EXISTS idx_events_code_ts ON events(code, timestamp)")
conn2.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp)")
conn2.commit()
conn2.close()
print(f"  Done  ({time.perf_counter()-t0:.1f}s)")
print()

# ── Step 3: VACUUM ────────────────────────────────────────────────────────────
print("Step 3: VACUUM — rewrite the DB file to reclaim freed disk space...")
print("  (This rewrites the entire file; it takes a few minutes.)")
t0 = time.perf_counter()
conn3 = sqlite3.connect(str(DB), timeout=600)
conn3.execute("VACUUM")
conn3.close()
print(f"  Done  ({time.perf_counter()-t0:.1f}s)")
print()

# ── Summary ───────────────────────────────────────────────────────────────────
final_size_gb = DB.stat().st_size / 1024 / 1024 / 1024
conn4 = sqlite3.connect(str(DB))
final_count = conn4.execute("SELECT COUNT(*) FROM events").fetchone()[0]
conn4.close()

print("Done.")
print(f"  events rows:  {final_count:,}  (was {old+keep:,})")
print(f"  DB size:      {final_size_gb:.2f} GB  (was {size_gb:.1f} GB)")
print(f"  Archived:     {len(archive_files)} monthly files in {ARCHIVE_DIR}")
print()
print("The watcher will now maintain the 30-day retention window automatically.")
print("Archive files in data/events_archive/ are the permanent NNR audit trail.")
