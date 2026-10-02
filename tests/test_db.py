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


# ── every deleted event must be in the archive, whatever the table contains ──
# The events table is the regulated fault record; the archive is its permanent
# copy. These tests check the one rule that matters by CONTENT, not by counts:
# anything prune removes from the table must be readable in an archive file.

def _row(ts, message):
    return (ts, 'warning', '11001', 'func', message, f'{message}.log')


def _live_messages(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return {m for (m,) in conn.execute("SELECT message FROM events")}
    finally:
        conn.close()


def _archived_messages(archive_dir):
    found = []
    for f in sorted(archive_dir.glob("*.csv.gz")):
        with gzip.open(f, "rt", encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh)
            next(reader)  # header
            found += [r[4] for r in reader]
    return found


def _insert(db_path, rows):
    conn = sqlite3.connect(db_path)
    insert_events(conn, rows)
    conn.commit()
    conn.close()


def _assert_nothing_lost(db_path, archive_dir, everything):
    lost = set(everything) - _live_messages(db_path) - set(_archived_messages(archive_dir))
    assert lost == set(), f'deleted without being archived: {sorted(lost)}'


def test_an_event_with_an_empty_timestamp_does_not_make_prune_delete_unarchived_events(tmp_path, monkeypatch):
    """One undated row used to make the archive step find "nothing to archive" --
    and the prune then deleted every old event anyway."""
    monkeypatch.setattr(db, 'datetime', _FrozenDatetime)
    db_path = _make_db(tmp_path)
    _insert(db_path, [_row('2026-03-10 08:00:00', 'march-a'), _row('2026-03-11 08:00:00', 'march-b'),
                      _row('2026-04-02 08:00:00', 'april-a'), _row('', 'undated'),
                      _row(_NOW.strftime('%Y-%m-%d %H:%M:%S'), 'recent')])
    archive_dir = tmp_path / "archive"

    pruned = prune_events(db_path, keep_days=30, archive_dir=str(archive_dir))

    _assert_nothing_lost(db_path, archive_dir, ['march-a', 'march-b', 'april-a', 'undated', 'recent'])
    assert pruned == 3                                              # the three dated old events
    assert sorted(_archived_messages(archive_dir)) == ['april-a', 'march-a', 'march-b']
    assert _live_messages(db_path) == {'undated', 'recent'}         # an undated event is never thrown away


def test_an_undated_event_is_kept_even_when_no_archive_is_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'datetime', _FrozenDatetime)
    db_path = _make_db(tmp_path)
    _insert(db_path, [_row('2026-03-10 08:00:00', 'march-a'), _row('', 'undated')])
    assert prune_events(db_path, keep_days=30, archive_dir=None) == 1
    assert _live_messages(db_path) == {'undated'}


def test_events_that_arrive_late_for_an_already_archived_month_are_archived_before_deletion(tmp_path, monkeypatch):
    """An old log file ingested after its month was archived: the month's archive
    file already exists, so the month used to be skipped -- and the late events
    were deleted without ever being written anywhere."""
    monkeypatch.setattr(db, 'datetime', _FrozenDatetime)
    db_path = _make_db(tmp_path)
    archive_dir = tmp_path / "archive"
    _insert(db_path, [_row('2026-03-10 08:00:00', 'march-a')])
    assert prune_events(db_path, keep_days=30, archive_dir=str(archive_dir)) == 1

    _insert(db_path, [_row('2026-03-12 09:00:00', 'march-late-1'), _row('2026-03-13 09:00:00', 'march-late-2')])
    pruned = prune_events(db_path, keep_days=30, archive_dir=str(archive_dir))

    _assert_nothing_lost(db_path, archive_dir, ['march-a', 'march-late-1', 'march-late-2'])
    assert pruned == 2
    assert _live_messages(db_path) == set()
    # the original month file is left exactly as it was (archives are never rewritten)
    with gzip.open(archive_dir / 'events_2026_03.csv.gz', 'rt', encoding='utf-8', newline='') as fh:
        assert [r[4] for r in list(csv.reader(fh))[1:]] == ['march-a']


def test_a_run_that_archived_but_did_not_delete_is_finished_safely_next_time(tmp_path, monkeypatch):
    """Archive written, then the process died before the delete."""
    monkeypatch.setattr(db, 'datetime', _FrozenDatetime)
    db_path = _make_db(tmp_path)
    archive_dir = tmp_path / "archive"
    _insert(db_path, [_row('2026-03-10 08:00:00', 'march-a'), _row('2026-04-02 08:00:00', 'april-a')])
    db.archive_old_events(db_path, (_NOW - timedelta(days=30)).strftime('%Y-%m-%d'), str(archive_dir))

    pruned = prune_events(db_path, keep_days=30, archive_dir=str(archive_dir))

    _assert_nothing_lost(db_path, archive_dir, ['march-a', 'april-a'])
    assert pruned == 2 and _live_messages(db_path) == set()


def test_prune_gives_up_if_events_slip_in_between_the_archive_and_the_delete(tmp_path, monkeypatch):
    """The delete must only ever remove what the archive step just wrote."""
    monkeypatch.setattr(db, 'datetime', _FrozenDatetime)
    db_path = _make_db(tmp_path)
    archive_dir = tmp_path / "archive"
    _insert(db_path, [_row('2026-03-10 08:00:00', 'march-a')])
    real_archive = db.archive_old_events

    def _archive_then_a_late_event_lands(*args, **kwargs):
        written = real_archive(*args, **kwargs)
        _insert(db_path, [_row('2026-03-20 08:00:00', 'march-snuck-in')])
        return written

    monkeypatch.setattr(db, 'archive_old_events', _archive_then_a_late_event_lands)
    assert prune_events(db_path, keep_days=30, archive_dir=str(archive_dir)) == 0
    assert _live_messages(db_path) == {'march-a', 'march-snuck-in'}        # nothing deleted this time

    monkeypatch.setattr(db, 'archive_old_events', real_archive)
    assert prune_events(db_path, keep_days=30, archive_dir=str(archive_dir)) == 2   # ...and the next run completes
    _assert_nothing_lost(db_path, archive_dir, ['march-a', 'march-snuck-in'])
    assert _live_messages(db_path) == set()


def test_a_timestamp_that_is_not_a_real_date_stops_the_prune_rather_than_losing_data(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'datetime', _FrozenDatetime)
    db_path = _make_db(tmp_path)
    archive_dir = tmp_path / "archive"
    _insert(db_path, [_row('1', 'garbage-timestamp'), _row('2026-03-10 08:00:00', 'march-a')])
    assert prune_events(db_path, keep_days=30, archive_dir=str(archive_dir)) == 0
    assert _live_messages(db_path) == {'garbage-timestamp', 'march-a'}


def test_the_manual_prune_script_never_deletes_what_it_did_not_archive(tmp_path):
    """scripts/prune_events_db.py had its own copy of the delete step, which still
    removed the partial cutoff month that the archive step deliberately skips."""
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(db.__file__).resolve().parent
    (tmp_path / 'scripts').mkdir()
    (tmp_path / 'data').mkdir()
    shutil.copy(repo / 'db.py', tmp_path / 'db.py')
    shutil.copy(repo / 'scripts' / 'prune_events_db.py', tmp_path / 'scripts' / 'prune_events_db.py')
    db_path = str(tmp_path / 'data' / 'cyclotron.db')
    init_db(db_path)

    now = datetime.now()
    cutoff = now - timedelta(days=30)
    rows = {
        'two-months-before-cutoff': (cutoff.replace(day=1) - timedelta(days=40)),
        'day-before-cutoff': cutoff - timedelta(days=1),
        'first-of-cutoff-month': cutoff.replace(day=1, hour=0, minute=0, second=1),
        'recent': now,
    }
    _insert(db_path, [_row(ts.strftime('%Y-%m-%d %H:%M:%S'), name) for name, ts in rows.items()])

    result = subprocess.run([sys.executable, 'scripts/prune_events_db.py'], cwd=tmp_path, input='yes\n',
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_nothing_lost(db_path, tmp_path / 'data' / 'events_archive', list(rows))
    assert 'recent' in _live_messages(db_path)
    assert 'two-months-before-cutoff' not in _live_messages(db_path)          # it really pruned
    assert 'two-months-before-cutoff' in _archived_messages(tmp_path / 'data' / 'events_archive')


def _boundary_days():
    """Days on which the 30-day cutoff lands on the 1st, 2nd, 15th, last-but-one
    or last day of a month, across 2027 and 2028 (a leap year)."""
    day = datetime(2027, 1, 1, 12, 0, 0)
    while day < datetime(2029, 1, 1):
        cutoff = day - timedelta(days=30)
        next_day, day_after = cutoff + timedelta(days=1), cutoff + timedelta(days=2)
        if cutoff.day in (1, 2, 15) or next_day.day == 1 or day_after.day == 1:
            yield day
        day += timedelta(days=1)


def test_nothing_is_lost_on_any_boundary_day(tmp_path, monkeypatch):
    """Every awkward date across two years, with events on both sides of every
    boundary, an undated event, and a second run after late events arrive. No
    event may leave the table without being in the archive -- and the prune must
    actually remove what it should (a prune that never deletes would "lose"
    nothing either)."""
    cases = 0
    for today in _boundary_days():
        class _Frozen(datetime):
            @classmethod
            def now(cls, tz=None, _today=today):   # (a class body can't see a local called `now`)
                return _today

        monkeypatch.setattr(db, 'datetime', _Frozen)
        case_dir = tmp_path / f'case{cases}'
        case_dir.mkdir()
        db_path = _make_db(case_dir)
        archive_dir = case_dir / 'archive'
        cutoff = today - timedelta(days=30)
        month_start = cutoff.replace(day=1, hour=0, minute=0, second=0)
        fmt = '%Y-%m-%d %H:%M:%S'
        first = [
            _row((month_start - timedelta(days=75)).strftime(fmt), 'long-ago'),
            _row((month_start - timedelta(seconds=1)).strftime(fmt), 'last-second-of-previous-month'),
            _row(month_start.strftime(fmt), 'first-second-of-cutoff-month'),
            _row(cutoff.strftime(fmt), 'at-cutoff'),
            _row('', 'undated'),
            _row(today.strftime(fmt), 'now'),
        ]
        _insert(db_path, first)
        prune_events(db_path, keep_days=30, archive_dir=str(archive_dir))
        late = [_row((month_start - timedelta(days=74)).strftime(fmt), 'late-arrival'),
                _row((month_start - timedelta(seconds=2)).strftime(fmt), 'late-arrival-month-end')]
        _insert(db_path, late)
        prune_events(db_path, keep_days=30, archive_dir=str(archive_dir))

        label = f'{today:%Y-%m-%d}'
        everything = [r[4] for r in first + late]
        live, archived = _live_messages(db_path), _archived_messages(archive_dir)
        assert set(everything) - live - set(archived) == set(), f'{label}: deleted without being archived'
        # exactly the whole months before the cutoff month are gone, each archived once
        assert live == {'first-second-of-cutoff-month', 'at-cutoff', 'undated', 'now'}, label
        assert sorted(archived) == ['last-second-of-previous-month', 'late-arrival',
                                    'late-arrival-month-end', 'long-ago'], label
        cases += 1
    assert cases > 100


# ── a run that cannot finish must leave nothing behind and delete nothing ────

def _archive_files(archive_dir):
    return sorted(f.name for f in archive_dir.iterdir() if not f.name.startswith('.prune')) if archive_dir.exists() else []


def _two_old_months(db_path):
    _insert(db_path, [_row('2026-03-10 08:00:00', 'march-a'), _row('2026-04-02 08:00:00', 'april-a')])


def test_a_failed_archive_write_deletes_nothing_and_leaves_no_files(tmp_path, monkeypatch):
    """Disk full / file locked while writing the second month. The first month's
    file was already written -- it must not be left to pile up run after run."""
    monkeypatch.setattr(db, 'datetime', _FrozenDatetime)
    db_path = _make_db(tmp_path)
    archive_dir = tmp_path / "archive"
    _two_old_months(db_path)
    real_replace, calls = db.os.replace, []

    def _fails_on_second_file(src, dst):
        calls.append(dst)
        if len(calls) % 2 == 0:
            raise OSError(28, 'No space left on device')
        return real_replace(src, dst)

    monkeypatch.setattr(db.os, 'replace', _fails_on_second_file)
    for _ in range(3):                                   # the watcher retries every refresh
        assert prune_events(db_path, keep_days=30, archive_dir=str(archive_dir)) == 0
    assert _live_messages(db_path) == {'march-a', 'april-a'}
    assert _archive_files(archive_dir) == []

    monkeypatch.setattr(db.os, 'replace', real_replace)  # disk freed
    assert prune_events(db_path, keep_days=30, archive_dir=str(archive_dir)) == 2
    assert _archive_files(archive_dir) == ['events_2026_03.csv.gz', 'events_2026_04.csv.gz']


def test_a_run_that_stops_before_deleting_leaves_no_files_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'datetime', _FrozenDatetime)
    db_path = _make_db(tmp_path)
    archive_dir = tmp_path / "archive"
    _two_old_months(db_path)
    real_archive = db.archive_old_events
    monkeypatch.setattr(db, 'archive_old_events', lambda *a, **k: real_archive(*a, **k) + 1)   # says it wrote more than is due
    for _ in range(3):
        assert prune_events(db_path, keep_days=30, archive_dir=str(archive_dir)) == 0
    assert _live_messages(db_path) == {'march-a', 'april-a'}
    assert _archive_files(archive_dir) == []


def test_re_ingesting_events_that_are_already_archived_does_not_write_them_again(tmp_path, monkeypatch):
    """`main.py train` re-ingests every log still on disk; that must not add another
    full copy of the history to the archive each time."""
    monkeypatch.setattr(db, 'datetime', _FrozenDatetime)
    db_path = _make_db(tmp_path)
    archive_dir = tmp_path / "archive"
    for _ in range(4):
        _two_old_months(db_path)
        assert prune_events(db_path, keep_days=30, archive_dir=str(archive_dir)) == 2
        assert _live_messages(db_path) == set()
    assert _archive_files(archive_dir) == ['events_2026_03.csv.gz', 'events_2026_04.csv.gz']
    assert sorted(_archived_messages(archive_dir)) == ['april-a', 'march-a']


def test_each_batch_of_late_events_gets_its_own_file(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'datetime', _FrozenDatetime)
    db_path = _make_db(tmp_path)
    archive_dir = tmp_path / "archive"
    for i in range(3):
        _insert(db_path, [_row(f'2026-03-1{i} 08:00:00', f'march-{i}')])
        assert prune_events(db_path, keep_days=30, archive_dir=str(archive_dir)) == 1
    assert _archive_files(archive_dir) == ['events_2026_03.csv.gz', 'events_2026_03_late1.csv.gz',
                                           'events_2026_03_late2.csv.gz']
    assert sorted(_archived_messages(archive_dir)) == ['march-0', 'march-1', 'march-2']


def test_a_damaged_archive_file_stops_the_prune_instead_of_being_trusted(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'datetime', _FrozenDatetime)
    db_path = _make_db(tmp_path)
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    (archive_dir / 'events_2026_03.csv.gz').write_bytes(b'this is not a gzip file')
    _two_old_months(db_path)
    assert prune_events(db_path, keep_days=30, archive_dir=str(archive_dir)) == 0
    assert _live_messages(db_path) == {'march-a', 'april-a'}
    assert _archive_files(archive_dir) == ['events_2026_03.csv.gz']      # and April's new file was not left behind


def test_a_second_prune_running_at_the_same_time_does_nothing(tmp_path, monkeypatch):
    """Two overlapping prunes (the manual script run while the watcher is up) could
    each delete rows the other one archived."""
    monkeypatch.setattr(db, 'datetime', _FrozenDatetime)
    db_path = _make_db(tmp_path)
    archive_dir = tmp_path / "archive"
    _two_old_months(db_path)
    archive_dir.mkdir()
    other = sqlite3.connect(str(archive_dir / '.prune.lock'), timeout=0)
    other.execute('BEGIN EXCLUSIVE')                      # "the other prune"
    try:
        assert prune_events(db_path, keep_days=30, archive_dir=str(archive_dir)) == 0
        assert _live_messages(db_path) == {'march-a', 'april-a'}
        assert _archive_files(archive_dir) == []
    finally:
        other.close()
    assert prune_events(db_path, keep_days=30, archive_dir=str(archive_dir)) == 2   # free again


def test_the_archive_is_forced_to_disk_before_anything_is_deleted(tmp_path, monkeypatch):
    """The delete is durable the moment it commits; the archive must be too, or a
    power cut just afterwards could leave the events in neither place."""
    monkeypatch.setattr(db, 'datetime', _FrozenDatetime)
    db_path = _make_db(tmp_path)
    _two_old_months(db_path)
    order = []
    real_fsync, real_replace = db.os.fsync, db.os.replace
    monkeypatch.setattr(db.os, 'fsync', lambda fd: (order.append('fsync'), real_fsync(fd))[1])
    monkeypatch.setattr(db.os, 'replace', lambda a, b: (order.append('publish'), real_replace(a, b))[1])
    assert prune_events(db_path, keep_days=30, archive_dir=str(tmp_path / "archive")) == 2
    assert order == ['fsync', 'publish', 'fsync', 'publish']


def test_an_event_whose_timestamp_starts_with_a_space_is_never_deleted(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'datetime', _FrozenDatetime)
    db_path = _make_db(tmp_path)
    archive_dir = tmp_path / "archive"
    _insert(db_path, [_row(' 2026-03-10 08:00:00', 'leading-space'), _row('2026-03-10 08:00:00', 'march-a')])
    assert prune_events(db_path, keep_days=30, archive_dir=str(archive_dir)) == 1
    assert _live_messages(db_path) == {'leading-space'}
    _assert_nothing_lost(db_path, archive_dir, ['leading-space', 'march-a'])


def test_a_locked_database_makes_prune_step_aside_rather_than_crash_the_watcher(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'datetime', _FrozenDatetime)
    db_path = _make_db(tmp_path)
    archive_dir = tmp_path / "archive"
    _two_old_months(db_path)
    real_connect = sqlite3.connect
    monkeypatch.setattr(db.sqlite3, 'connect',
                        lambda path, timeout=60, **kw: real_connect(path, timeout=0.2 if path == db_path else timeout, **kw))
    writer = real_connect(db_path)
    writer.execute('BEGIN IMMEDIATE')                     # e.g. a long ingest holding the write lock
    try:
        assert prune_events(db_path, keep_days=30, archive_dir=str(archive_dir)) == 0
    finally:
        writer.rollback()
        writer.close()
    assert _live_messages(db_path) == {'march-a', 'april-a'}
    assert _archive_files(archive_dir) == []


def test_a_locked_database_does_not_crash_the_watcher_when_no_archive_is_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'datetime', _FrozenDatetime)
    db_path = _make_db(tmp_path)
    _two_old_months(db_path)
    real_connect = sqlite3.connect
    monkeypatch.setattr(db.sqlite3, 'connect', lambda path, timeout=60, **kw: real_connect(path, timeout=0.2, **kw))
    writer = real_connect(db_path)
    writer.execute('BEGIN IMMEDIATE')
    try:
        assert prune_events(db_path, keep_days=30, archive_dir=None) == 0
    finally:
        writer.rollback()
        writer.close()
    assert _live_messages(db_path) == {'march-a', 'april-a'}


def test_the_manual_script_reports_failure_when_it_removed_nothing(tmp_path):
    """It used to print "Done." and exit 0 (after a pointless VACUUM) even when the
    archive step had failed and nothing was pruned."""
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(db.__file__).resolve().parent
    (tmp_path / 'scripts').mkdir()
    (tmp_path / 'data' / 'events_archive').mkdir(parents=True)
    shutil.copy(repo / 'db.py', tmp_path / 'db.py')
    shutil.copy(repo / 'scripts' / 'prune_events_db.py', tmp_path / 'scripts' / 'prune_events_db.py')
    db_path = str(tmp_path / 'data' / 'cyclotron.db')
    init_db(db_path)
    old = (datetime.now() - timedelta(days=30)).replace(day=1) - timedelta(days=40)
    _insert(db_path, [_row(old.strftime('%Y-%m-%d %H:%M:%S'), 'old-event')])
    (tmp_path / 'data' / 'events_archive' / f'events_{old:%Y_%m}.csv.gz').write_bytes(b'damaged')

    result = subprocess.run([sys.executable, 'scripts/prune_events_db.py'], cwd=tmp_path, input='yes\n',
                            capture_output=True, text=True, timeout=120)
    assert result.returncode != 0
    assert 'NOTHING WAS REMOVED' in result.stdout and 'VACUUM' not in result.stdout.split('NOTHING WAS REMOVED')[1]
    assert _live_messages(db_path) == {'old-event'}
