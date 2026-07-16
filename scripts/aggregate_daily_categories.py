"""Populate the daily_category table that the human-contact features in
features.engineer read from.

WHY: the model's `humact_*` features count operator-action and machine-struggle
events (commands, resets, tuning, interlock aborts, comms timeouts, excess power,
standby/shutdown) in 7/14-day windows. Counting them per prediction from the raw
event log would mean a substring scan over millions of rows every time, so instead
we precompute a small daily (date, category, count) table here and the features sum
over it. Run this alongside the beam_daily aggregation whenever new events land.

The category logic MUST stay identical to what the models were trained on — do not
change CATEGORIES or the row filter without retraining.

USAGE:
    python scripts/aggregate_daily_categories.py [--db data/cyclotron.db] [--days N]
(--days limits to the last N calendar days; omit to aggregate every date present.
Inference only needs the last ~14 days, which the event retention window covers.)
"""
import argparse
import sqlite3
from collections import defaultdict

# category -> substring triggers (cheap `in` checks). MUST match the training
# substrate and features.engineer.HUMACT_CATEGORIES.
CATEGORIES = {
    'mode_change':    ('mode',),
    'reset':          ('reset', 'setlifetime', 'cleared', 'zeroed'),
    'service_manual': ('service', 'manual', 'technician', 'engineer', 'maintenance', 'replace'),
    'operator_cmd':   ('command ', 'seq #', 'operator', 'login', 'logout', 'acknowl', 'request'),
    'interlock':      ('interlock', 'door', 'access', 'aborting', 'abort'),
    'tune_adjust':    ('adjust', 'calibrat', 'tune', 'optimi', 'override', 'clamp'),
    'struggle':       ('timed out', 'excess power', 'attempt to raise', 'failed', 'retry', 'timeout'),
    'power_state':    ('shutdown', 'standby', 'startup', 'warm'),
}
_ITEMS = list(CATEGORIES.items())


def aggregate(db_path: str, days: int | None = None) -> dict:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS daily_category "
        "(date TEXT, category TEXT, count INTEGER, PRIMARY KEY(date, category))"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dailycat_date ON daily_category(date)")
    where, params = "", []
    if days is not None:
        latest = conn.execute("SELECT MAX(date(timestamp)) FROM events").fetchone()[0]
        if latest:
            where = "WHERE date(timestamp) >= date(?, ?)"
            params = [latest, f"-{int(days)} days"]

    day_cat: dict = defaultdict(lambda: defaultdict(int))
    cur = conn.execute(
        f"SELECT timestamp, function, message FROM events {where}", params
    )
    for ts, func, msg in cur:
        if not ts or not msg:
            continue
        # Same row filter as the training substrate: only non-numeric-telemetry
        # rows (message starts with a letter) or explicit mode lines.
        if not (msg[0].isalpha() or (func and 'mode' in func)):
            continue
        low = (msg + ' ' + (func or '')).lower()
        d = ts[:10]
        dc = day_cat[d]
        for cat, trigs in _ITEMS:
            for t in trigs:
                if t in low:
                    dc[cat] += 1
                    break

    rows = [(d, c, n) for d, cats in day_cat.items() for c, n in cats.items()]
    # Replace the recomputed dates so re-runs are idempotent.
    for d in day_cat:
        conn.execute("DELETE FROM daily_category WHERE date=?", [d])
    conn.executemany("INSERT OR REPLACE INTO daily_category VALUES (?,?,?)", rows)
    conn.commit()
    n_days = len(day_cat)
    conn.close()
    return {"days": n_days, "rows": len(rows)}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/cyclotron.db")
    ap.add_argument("--days", type=int, default=None)
    args = ap.parse_args()
    r = aggregate(args.db, args.days)
    print(f"daily_category updated: {r['days']} days, {r['rows']} rows.")


if __name__ == "__main__":
    main()
