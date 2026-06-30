"""
One-time pruning script: shrink the events table from 29.7M rows to last 30 days.

WHAT THIS DOES:
  - Keeps all events from the last 30 days (2,601,506 rows as of 2026-06-30)
  - Permanently deletes 27,135,371 events older than 30 days (from 2022-03-21)
  - Runs VACUUM to shrink the DB file from 7.8 GB to ~1 GB

WHAT THIS DOES NOT TOUCH:
  - maintenance_events  (94 rows — all preserved)
  - predictions         (5 rows  — all preserved)
  - beam_daily          (preserved)
  - gauge_readings      (preserved)
  - petrace_batches     (preserved)

HOW TO RUN:
  1. Stop the API:   Ctrl+C in the uvicorn terminal
  2. Stop the watcher: Ctrl+C in the python main.py terminal
  3. Run: python scripts/prune_events_db.py
  4. Restart API and watcher as normal

TIME ESTIMATE: 2–5 minutes (table-swap is fast; VACUUM rebuilds the file).
DISK NEEDED:  ~1 GB free (for the new compact DB file during VACUUM).
"""
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta

DB = os.path.join(os.path.dirname(__file__), '..', 'data', 'cyclotron.db')
DB = os.path.abspath(DB)
KEEP_DAYS = 30

print(f"DB: {DB}")
print(f"Keeping last {KEEP_DAYS} days of events\n")

if not os.path.exists(DB):
    print("ERROR: DB file not found. Run from the cyclotron_monitor directory.")
    sys.exit(1)

# Safety: confirm before running
size_gb = os.path.getsize(DB) / 1024 / 1024 / 1024
print(f"Current DB size: {size_gb:.1f} GB")

cutoff = (datetime.now() - timedelta(days=KEEP_DAYS)).strftime('%Y-%m-%d')
conn = sqlite3.connect(DB, timeout=60)
keep = conn.execute("SELECT COUNT(*) FROM events WHERE timestamp >= ?", [cutoff]).fetchone()[0]
old = conn.execute("SELECT COUNT(*) FROM events WHERE timestamp < ?", [cutoff]).fetchone()[0]
conn.close()

print(f"Events to delete: {old:,}  (before {cutoff})")
print(f"Events to keep:   {keep:,}  (from {cutoff} onwards)")
print()
answer = input("Type 'yes' to proceed: ").strip().lower()
if answer != 'yes':
    print("Aborted.")
    sys.exit(0)

print()
conn = sqlite3.connect(DB, timeout=600)
conn.execute('PRAGMA journal_mode=WAL')

print("Step 1/5: Creating events_keep with recent rows...")
t0 = time.perf_counter()
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
print(f"  {keep:,} rows inserted  ({time.perf_counter()-t0:.1f}s)")

print("Step 2/5: Dropping old events table...")
t0 = time.perf_counter()
conn.execute("DROP TABLE events")
print(f"  Done  ({time.perf_counter()-t0:.1f}s)")

print("Step 3/5: Renaming events_keep → events...")
t0 = time.perf_counter()
conn.execute("ALTER TABLE events_keep RENAME TO events")
conn.commit()
print(f"  Done  ({time.perf_counter()-t0:.1f}s)")

print("Step 4/5: Rebuilding indexes...")
t0 = time.perf_counter()
conn.execute("CREATE INDEX IF NOT EXISTS idx_events_code_ts ON events(code, timestamp)")
conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp)")
conn.commit()
print(f"  Done  ({time.perf_counter()-t0:.1f}s)")

conn.close()

print("Step 5/5: VACUUM (rewrites the file to reclaim 7.8 GB → ~1 GB)...")
t0 = time.perf_counter()
conn2 = sqlite3.connect(DB, timeout=600)
conn2.execute("VACUUM")
conn2.close()
print(f"  Done  ({time.perf_counter()-t0:.1f}s)")

final_size = os.path.getsize(DB) / 1024 / 1024 / 1024
conn3 = sqlite3.connect(DB)
final_count = conn3.execute("SELECT COUNT(*) FROM events").fetchone()[0]
conn3.close()

print(f"\nDone.")
print(f"  events rows: {final_count:,}")
print(f"  DB size:     {final_size:.2f} GB  (was {size_gb:.1f} GB)")
