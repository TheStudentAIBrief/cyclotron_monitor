"""
One-time pruning script: archive then shrink the events table.

WHAT THIS DOES:
  1. ARCHIVES every complete calendar month older than the 30-day retention
     window to gzip CSV files in data/events_archive/events_YYYY_MM.csv.gz.
     Existing files are never rewritten; events found for a month that already
     has a file go to events_YYYY_MM_lateN.csv.gz.
  2. TABLE-SWAP: replaces the bloated events table with one that no longer
     holds the archived months, then rebuilds indexes. Only events that were
     archived in step 1 are removed (the part-month around the cutoff stays
     until that month is complete).
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

This script is safe to re-run: nothing is removed from the table unless it
was written to an archive file first.
"""
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path so we can import db.py
sys.path.insert(0, str(Path(__file__).parent.parent))
from db import prune_events, EVENTS_RETENTION_DAYS

# prune_events reports its progress (one line per archived month) and any reason
# for stopping through logging; without this a 10-20 minute run prints nothing.
logging.basicConfig(level=logging.INFO, format='  %(message)s', stream=sys.stdout)

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
# Only whole months are archived, so only whole months are removed: everything
# before the 1st of the month the retention cutoff falls in. The days of that
# month before the cutoff stay in the table until the month is complete.
remove_before = f'{cutoff[:7]}-01'

conn = sqlite3.connect(str(DB), timeout=30)
total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
# Same definition prune_events uses: events with no usable date are never removed.
old  = conn.execute("SELECT COUNT(*) FROM events WHERE timestamp >= '0' AND timestamp < ?",
                    [remove_before]).fetchone()[0]
keep = total - old
conn.close()

print(f"Current DB size:    {size_gb:.1f} GB")
print(f"Events to archive:  {old:,}  (before {remove_before})")
print(f"Events to keep:     {keep:,}  (from {remove_before} onwards)")
print()
print("Existing archive files in data/events_archive/ are never rewritten.")
print()
answer = input("Type 'yes' to proceed: ").strip().lower()
if answer != 'yes':
    print("Aborted.")
    sys.exit(0)

print()

# ── Steps 1 + 2: archive, then remove what was archived ──────────────────────
# Done by db.prune_events(), the same code the watcher runs, rather than a second
# copy of the delete here: this script used to delete everything before the exact
# cutoff DATE, including the days of the cutoff month that the archive step
# deliberately leaves out — events removed without ever being archived.
print("Steps 1-2: Archiving old events to monthly gzip CSV files, then removing")
print("           exactly those events from the table...")
t0 = time.perf_counter()
removed = prune_events(str(DB), keep_days=KEEP_DAYS, archive_dir=str(ARCHIVE_DIR))
t1 = time.perf_counter()

archive_files = sorted(ARCHIVE_DIR.glob('events_*.csv.gz')) if ARCHIVE_DIR.exists() else []
archive_size_mb = sum(f.stat().st_size for f in archive_files) / 1024 / 1024
print(f"  {removed:,} events archived and removed in {t1-t0:.0f}s")
print(f"  {len(archive_files)} archive files  ({archive_size_mb:.0f} MB total)")
if old and not removed:
    print("  NOTHING WAS REMOVED: the archive step did not complete or did not cover")
    print("  every event due for removal (see the messages above). No data was deleted.")
    print("  Fix the cause and run this script again.")
    sys.exit(1)
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
