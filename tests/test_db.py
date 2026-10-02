import gzip
import csv
import sqlite3
from datetime import datetime, timedelta

import db
from db import init_db, insert_events, prune_events

# The prune tests build rows relative to "now" and assume the 30-day cutoff falls
# in the MIDDLE of a month. On a real clock that is false about one day a month
# (whenever now minus 30 days is the 1st), and the test then failed for a reason
# that has nothing to do with the code. Pin the clock instead.
_NOW = datetime(2026, 6, 20, 12, 0, 0)        # cutoff = 21 May: mid-month


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return _NOW


def _make_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    return db_path


def _read_archive_row_count(archive_dir):
    total = 0
    for f in archive_dir.glob("*.csv.gz"):
        with gzip.open(f, "rt", encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh)
            next(reader)  # header
            total += sum(1 for _ in reader)
    return total


def test_prune_never_deletes_more_than_it_archived(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'datetime', _FrozenDatetime)
    # Reproduces the real incident: keep_days=30 makes the cutoff fall in the
    # MIDDLE of a calendar month. archive_old_events only ever archives whole
    # months strictly before the cutoff month (it deliberately skips the
    # partial cutoff month), so prune_events must not delete anything from
    # that partial month either — it has to wait until a future run when the
    # whole month is safely archivable. The invariant that must always hold:
    # every row deleted must appear in the archive.
    db_path = _make_db(tmp_path)
    conn = sqlite3.connect(db_path)

    cutoff_date = _NOW - timedelta(days=30)
    # One row from a fully-complete prior month — safe to archive + delete.
    old_month_row = (
        (cutoff_date.replace(day=1) - timedelta(days=40)).strftime('%Y-%m-%d %H:%M:%S'),
        'warning', '11001', 'func', 'old month event', 'hyper_old.log'
    )
    # One row from the partial cutoff month, one day before the cutoff date.
    # This is the row the bug used to delete without ever archiving — it must
    # now be left alone (still live) until its whole month is safely archivable.
    partial_month_row = (
        (cutoff_date - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S'),
        'warning', '11001', 'func', 'partial cutoff month event', 'hyper_partial.log'
    )
    # One recent row, well within the retention window (must be kept, not deleted).
    recent_row = (
        _NOW.strftime('%Y-%m-%d %H:%M:%S'),
        'warning', '11001', 'func', 'recent event', 'hyper_recent.log'
    )
    insert_events(conn, [old_month_row, partial_month_row, recent_row])
    conn.commit()
    conn.close()

    archive_dir = tmp_path / "archive"
    pruned = prune_events(db_path, keep_days=30, archive_dir=str(archive_dir))

    assert pruned == 1  # only old_month_row — the fully-complete prior month
    archived_count = _read_archive_row_count(archive_dir)
    assert archived_count == pruned, (
        f"archived {archived_count} rows but deleted {pruned} — "
        f"{pruned - archived_count} rows were permanently lost"
    )

    conn = sqlite3.connect(db_path)
    remaining = {row[0] for row in conn.execute("SELECT message FROM events").fetchall()}
    conn.close()
    # partial_month_row must survive — it was never archived, so it must never be deleted.
    assert remaining == {'partial cutoff month event', 'recent event'}


def test_prune_on_a_first_of_month_cutoff_still_archives_everything_it_deletes(tmp_path, monkeypatch):
    """The case that made the test above fail on real dates: when the cutoff is the
    1st of a month, the day before it belongs to a COMPLETE month, so it is rightly
    archived and deleted. What must hold on every date: deleted == archived."""
    now = datetime(2026, 10, 1, 12, 0, 0)            # cutoff = 1 September

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(db, 'datetime', _Frozen)
    db_path = _make_db(tmp_path)
    conn = sqlite3.connect(db_path)
    cutoff_date = now - timedelta(days=30)
    insert_events(conn, [
        ((cutoff_date.replace(day=1) - timedelta(days=40)).strftime('%Y-%m-%d %H:%M:%S'),
         'warning', '11001', 'func', 'old month event', 'a.log'),
        ((cutoff_date - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S'),
         'warning', '11001', 'func', 'day before cutoff', 'b.log'),
        (now.strftime('%Y-%m-%d %H:%M:%S'), 'warning', '11001', 'func', 'recent event', 'c.log'),
    ])
    conn.commit()
    conn.close()

    archive_dir = tmp_path / "archive"
    pruned = prune_events(db_path, keep_days=30, archive_dir=str(archive_dir))

    assert pruned == 2
    assert _read_archive_row_count(archive_dir) == 2
