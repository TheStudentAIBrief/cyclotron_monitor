"""Restore archived fault-code event history into a *training* database.

WHY THIS EXISTS
---------------
The live `events` table is pruned to a short retention window (~30 days). That is
correct for the operational DB, but it means that when models are trained against
it, every historical training row sees ZERO fault-code activity — so all ~31
fault-code features are constant 0 and get silently dropped by the model's
VarianceThreshold. The deployed models therefore learn nothing from the
carefully-engineered fault codes and run on beam_daily statistics alone.

The pruned history is not lost: `data/events_archive/*.csv.gz` holds it. This
script rebuilds a training DB with the full fault-code history restored, so a
retrain can actually use those features. Validated: with history restored, fault
features run ~2-46x higher in the pre-maintenance window than at rest (real
predictive signal), and retrained models improve strict detection and enable the
predictor's gated model-override (false-alarm rate 61%->41% on the walk-forward
backtest).

SAFETY
------
* The live DB is opened READ-ONLY (immutable). It is never modified.
* Output is a SEPARATE training DB (default: data/cyclotron_train.db). Do not point
  the operational app at it.
* Only *coded* rows (fault codes + the '(Warn 11001)' lifetime-counter messages)
  are restored — ~0.2% of archived rows — everything the fault/counter features
  need, without the multi-GB INFO-log noise.

USAGE
-----
    python scripts/restore_events_for_training.py \
        [--live data/cyclotron.db] [--archives data/events_archive] \
        [--out data/cyclotron_train.db]

Then retrain against the output DB, e.g.:
    python -c "from models.trainer import train_component, COMPONENTS; \
        [train_component(c, 'data/cyclotron_train.db', 'data/models') for c in COMPONENTS]"
(inference is unaffected by the retention window: fault features look back only
7-14 days, which the live DB's retention still covers.)
"""
import argparse
import csv
import glob
import gzip
import sqlite3
from pathlib import Path

_EVENT_COLS = ("timestamp", "severity", "code", "function", "message", "source_file")


def _is_coded(row: dict) -> bool:
    code = (row.get("code") or "").strip()
    return bool(code) or "(Warn 11001)" in (row.get("message") or "")


def restore(live_db: str, archives_dir: str, out_db: str) -> dict:
    out_path = Path(out_db)
    if out_path.exists():
        out_path.unlink()

    con = sqlite3.connect(str(out_path), uri=True)
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")

    # Copy small tables + the events schema from the live DB (strictly read-only).
    live_uri = f"file:{Path(live_db).as_posix()}?mode=ro&immutable=1"
    con.execute(f"ATTACH DATABASE '{live_uri}' AS live")
    for table in ("beam_daily", "maintenance_events", "petrace_batches"):
        ddl = con.execute(
            "SELECT sql FROM live.sqlite_master WHERE name=?", (table,)
        ).fetchone()
        if ddl and ddl[0]:
            con.execute(ddl[0])
            con.execute(f"INSERT INTO {table} SELECT * FROM live.{table}")
    events_ddl = con.execute(
        "SELECT sql FROM live.sqlite_master WHERE name='events'"
    ).fetchone()[0]
    con.execute(events_ddl)
    # coded rows currently live (recent retention window)
    con.execute(
        "INSERT OR IGNORE INTO events SELECT * FROM live.events "
        "WHERE (code IS NOT NULL AND code != '') OR message LIKE '%(Warn 11001)%'"
    )
    con.commit()
    try:
        con.execute("DETACH DATABASE live")
    except sqlite3.OperationalError:
        pass  # detaches on close; we only read from it

    # restore coded rows from the archives
    insert = (f"INSERT OR IGNORE INTO events ({','.join(_EVENT_COLS)}) "
              f"VALUES ({','.join('?' * len(_EVENT_COLS))})")
    added = 0
    for fn in sorted(glob.glob(str(Path(archives_dir) / "*.csv.gz"))):
        with gzip.open(fn, "rt") as fh:
            batch = [tuple(r.get(c) for c in _EVENT_COLS)
                     for r in csv.DictReader(fh) if _is_coded(r)]
        if batch:
            con.executemany(insert, batch)
            added += len(batch)
    con.commit()
    con.execute("CREATE INDEX IF NOT EXISTS idx_events_code_ts ON events(code, timestamp)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp)")
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    span = con.execute("SELECT MIN(timestamp), MAX(timestamp) FROM events").fetchone()
    con.close()
    return {"archived_added": added, "events_total": total,
            "span": span, "out_db": str(out_path)}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", default="data/cyclotron.db")
    ap.add_argument("--archives", default="data/events_archive")
    ap.add_argument("--out", default="data/cyclotron_train.db")
    args = ap.parse_args()
    result = restore(args.live, args.archives, args.out)
    print(f"Restored {result['archived_added']} archived coded rows.")
    print(f"Training DB: {result['out_db']}  "
          f"({result['events_total']} coded events, "
          f"span {result['span'][0]} -> {result['span'][1]})")


if __name__ == "__main__":
    main()
