import gzip
import csv
import sqlite3
from datetime import datetime, timedelta
from db import init_db, insert_events, prune_events


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


def test_prune_never_deletes_more_than_it_archived(tmp_path):
    # Reproduces the real incident: keep_days=30 makes the cutoff fall in the
    # MIDDLE of a calendar month. archive_old_events only ever archives whole
    # months strictly before the cutoff month (it deliberately skips the
    # partial cutoff month), so prune_events must not delete anything from
    # that partial month either — it has to wait until a future run when the
    # whole month is safely archivable. The invariant that must always hold:
    # every row deleted must appear in the archive.
    db_path = _make_db(tmp_path)
    conn = sqlite3.connect(db_path)

    cutoff_date = datetime.now() - timedelta(days=30)
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
        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
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
