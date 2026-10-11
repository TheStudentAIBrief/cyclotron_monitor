"""The training-DB restore must rebuild daily_category from the full history with
exactly the counts scripts/aggregate_daily_categories.py would produce."""
import csv
import gzip
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
from aggregate_daily_categories import aggregate, row_categories  # noqa: E402
from restore_events_for_training import restore  # noqa: E402

COLS = ("timestamp", "severity", "code", "function", "message", "source_file")
OLD = [
    ("2024-01-05 08:00:00", "INFO", "", "seq", "command initacc", "a.log"),
    ("2024-01-05 08:01:00", "WARN", "10206", "isc", "serial port timed out, retry", "a.log"),
    ("2024-01-05 08:02:00", "INFO", "", "isc", "12.5 3.1 0.4", "a.log"),          # telemetry: filtered
    ("2024-01-06 09:00:00", "INFO", "", "state", "entering standby", "a.log"),
]
LIVE = [
    ("2024-03-01 10:00:00", "INFO", "", "seq", "operator reset of interlock", "b.log"),
    ("2024-03-02 10:00:00", "WARN", "10504", "isc", "excess power", "b.log"),
]


def _make(tmp_path, archived, live):
    live_db = tmp_path / "live.db"
    con = sqlite3.connect(live_db)
    con.execute("CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, "
                "severity TEXT, code TEXT, function TEXT, message TEXT, source_file TEXT)")
    con.execute("CREATE TABLE beam_daily (date TEXT)")
    con.execute("CREATE TABLE maintenance_events (timestamp TEXT, component_label TEXT)")
    con.executemany(f"INSERT INTO events ({','.join(COLS)}) VALUES (?,?,?,?,?,?)", live)
    con.commit()
    con.close()
    arch = tmp_path / "archive"
    arch.mkdir()
    with gzip.open(arch / "events_2024_01.csv.gz", "wt", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(COLS)
        w.writerows(archived)
    return str(live_db), str(arch), str(tmp_path / "train.db")


def _cats(db):
    con = sqlite3.connect(db)
    rows = dict(((d, c), n) for d, c, n in con.execute("SELECT date, category, count FROM daily_category"))
    con.close()
    return rows


def test_row_categories_filters_numeric_telemetry():
    assert row_categories("isc", "12.5 3.1 0.4") is None
    assert row_categories("isc", "") is None
    assert row_categories("isc", "serial port timed out, retry") == ["struggle"]


def test_restore_rebuilds_daily_category_matching_the_aggregation_script(tmp_path):
    live_db, arch, out = _make(tmp_path, OLD, LIVE)
    result = restore(live_db, arch, out)
    got = _cats(out)

    # Reference: every row in one DB, counted by the production aggregation script.
    ref_db = tmp_path / "ref.db"
    con = sqlite3.connect(ref_db)
    con.execute("CREATE TABLE events (timestamp TEXT, severity TEXT, code TEXT, function TEXT, "
                "message TEXT, source_file TEXT)")
    con.executemany("INSERT INTO events VALUES (?,?,?,?,?,?)", OLD + LIVE)
    con.commit()
    con.close()
    aggregate(str(ref_db))

    assert got == _cats(str(ref_db))
    assert got[("2024-01-05", "struggle")] == 1
    assert ("2024-01-06", "power_state") in got
    assert result["category_days"] == 4


def test_archived_rows_inside_the_live_window_are_not_counted_twice(tmp_path):
    overlap = OLD + [LIVE[0]]            # the archive also holds a row that is still live
    live_db, arch, out = _make(tmp_path, overlap, LIVE)
    restore(live_db, arch, out)
    got = _cats(out)
    assert got[("2024-03-01", "reset")] == 1
    assert got[("2024-03-01", "operator_cmd")] == 1


# ── keeping daily_category current on site ───────────────────────────────────
def _events_db(tmp_path, rows):
    db = tmp_path / "site.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE events (timestamp TEXT, severity TEXT, code TEXT, function TEXT, "
                "message TEXT, source_file TEXT)")
    con.executemany("INSERT INTO events VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return str(db)


def _add(db, rows):
    con = sqlite3.connect(db)
    con.executemany("INSERT INTO events VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def test_aggregate_recent_matches_a_full_recount_as_events_arrive(tmp_path):
    from aggregate_daily_categories import aggregate_recent
    day1 = [("2024-05-01 08:00:00", "INFO", "", "seq", "command initacc", "a.log"),
            ("2024-05-01 09:00:00", "WARN", "", "isc", "comms timed out", "a.log")]
    more_day1 = [("2024-05-01 23:00:00", "WARN", "", "isc", "retry failed", "a.log")]
    day2 = [("2024-05-02 08:00:00", "INFO", "", "state", "entering standby", "a.log")]
    db = _events_db(tmp_path, day1)
    aggregate_recent(db)                      # first run creates the table
    assert _cats(db)[("2024-05-01", "struggle")] == 1
    _add(db, more_day1 + day2)                # the last counted day grows, a new day starts
    aggregate_recent(db)
    got = _cats(db)

    (tmp_path / "ref").mkdir()
    ref = _events_db(tmp_path / "ref", day1 + more_day1 + day2)
    aggregate(ref)
    assert got == _cats(ref)
    assert got[("2024-05-01", "struggle")] == 2


def test_aggregate_recent_leaves_days_older_than_its_window_alone(tmp_path):
    from aggregate_daily_categories import aggregate_recent, RECENT_MAX_DAYS
    import db as proddb
    assert RECENT_MAX_DAYS < proddb.EVENTS_RETENTION_DAYS   # never recount a part-pruned day
    old = [("2024-01-01 08:00:00", "INFO", "", "seq", "command initacc", "a.log")]
    new = [("2024-05-02 08:00:00", "INFO", "", "state", "entering standby", "a.log")]
    db = _events_db(tmp_path, old + new)
    aggregate(db)
    con = sqlite3.connect(db)
    con.execute("DELETE FROM events WHERE timestamp < '2024-02-01'")   # the prune removes old rows
    con.execute("DELETE FROM daily_category WHERE date='2024-05-02'")  # newest day not yet counted
    con.commit()
    con.close()
    aggregate_recent(db)
    got = _cats(db)
    assert got[("2024-01-01", "operator_cmd")] == 1     # history kept although its events are gone
    assert got[("2024-05-02", "power_state")] == 1
