"""The on-site watcher and the API can be pointed at the same database file.

The API's start-up (init_cloud_tables) adds a lab_id column to events,
maintenance_events and predictions. The on-site writers must keep working after
that, and pruning must not undo it.
"""
import sqlite3

import pytest

import db
from api.db_cloud import init_cloud_tables
from db import init_db, insert_events, prune_events, upsert_maintenance_event
from tests.test_db import _FrozenDatetime, _NOW, _row

_RECENT = _NOW.strftime('%Y-%m-%d %H:%M:%S')


@pytest.fixture
def shared_db(tmp_path):
    """A database the watcher created and the API then started up on."""
    path = str(tmp_path / 'shared.db')
    init_db(path)
    init_cloud_tables(path)
    return path


def _query(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _columns(db_path, table):
    return [r[1] for r in _query(db_path, f"PRAGMA table_info({table})")]


def _indexes(db_path, table):
    """Every index on the table, with the columns it covers."""
    found = {}
    for _, name, *_rest in _query(db_path, f"PRAGMA index_list({table})"):
        found[name] = [r[2] for r in _query(db_path, f"PRAGMA index_info({name})")]
    return found


def _named_indexes(db_path, table):
    return {n: cols for n, cols in _indexes(db_path, table).items() if not n.startswith('sqlite_autoindex')}


def _unique_column_sets(db_path, table):
    return sorted(sorted(cols) for n, cols in _indexes(db_path, table).items()
                  if n.startswith('sqlite_autoindex'))


def test_the_watcher_can_still_store_events_after_the_api_has_started(shared_db):
    conn = sqlite3.connect(shared_db)
    insert_events(conn, [_row(_RECENT, 'after-api-start')])
    conn.commit()
    conn.close()

    assert _query(shared_db, "SELECT message FROM events") == [('after-api-start',)]


def test_the_watcher_can_still_store_maintenance_events_after_the_api_has_started(shared_db):
    conn = sqlite3.connect(shared_db)
    upsert_maintenance_event(conn, _RECENT, 'ion_source', 'Ion source', 'reset.log')
    conn.commit()
    conn.close()

    assert _query(shared_db, "SELECT component_key, component_label, source_file FROM maintenance_events") == [
        ('ion_source', 'Ion source', 'reset.log')]


def test_the_watcher_can_still_store_predictions_after_the_api_has_started(shared_db):
    conn = sqlite3.connect(shared_db)
    db.upsert_prediction(conn, '2026-06-20', 'ion_source', 0.812, 4.5, 'warning', 'arc current', '["a", "b"]')
    conn.commit()
    conn.close()

    assert _query(shared_db, "SELECT run_at, component, risk_score, days_estimate, alert_level, "
                             "primary_signal, top_features FROM predictions") == [
        ('2026-06-20', 'ion_source', 0.812, 4.5, 'warning', 'arc current', '["a", "b"]')]


def test_a_prediction_for_the_same_day_and_component_replaces_the_earlier_one(tmp_path):
    path = str(tmp_path / 'onsite.db')
    init_db(path)
    conn = sqlite3.connect(path)
    db.upsert_prediction(conn, '2026-06-20', 'ion_source', 0.2, 30.0, 'ok', 'none', '[]')
    db.upsert_prediction(conn, '2026-06-20', 'ion_source', 0.9, 2.0, 'critical', 'arc current', '[]')
    conn.commit()
    conn.close()

    assert _query(path, "SELECT risk_score, alert_level FROM predictions") == [(0.9, 'critical')]


def _fill_and_prune(db_path, tmp_path):
    """Two old events (pruned) and two recent ones (kept); returns how many were pruned."""
    conn = sqlite3.connect(db_path)
    insert_events(conn, [_row('2026-03-10 08:00:00', 'march-a'), _row('2026-03-11 08:00:00', 'march-b'),
                         _row(_RECENT, 'recent-a'), _row(_RECENT, 'recent-b')])
    conn.commit()
    conn.close()
    return prune_events(db_path, keep_days=30, archive_dir=str(tmp_path / 'archive'))


def test_pruning_keeps_the_column_and_index_the_api_added(shared_db, tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'datetime', _FrozenDatetime)
    columns, indexes = _columns(shared_db, 'events'), _named_indexes(shared_db, 'events')
    assert 'lab_id' in columns and 'idx_events_lab_ts' in indexes      # what the API start-up added

    assert _fill_and_prune(shared_db, tmp_path) == 2

    assert _columns(shared_db, 'events') == columns
    assert _named_indexes(shared_db, 'events') == indexes
    # The API's own query for the Records tab still runs.
    assert len(_query(shared_db, "SELECT message FROM events WHERE lab_id=? AND code=?",
                      ['petlabs-pretoria', '11001'])) == 2


def test_pruning_keeps_each_events_lab(shared_db, tmp_path, monkeypatch):
    """The kept rows are copied, not re-created: a lab id that is not the default survives."""
    monkeypatch.setattr(db, 'datetime', _FrozenDatetime)
    conn = sqlite3.connect(shared_db)
    conn.execute("INSERT INTO events (timestamp, severity, code, function, message, source_file, lab_id) "
                 "VALUES (?,?,?,?,?,?,?)", [*_row(_RECENT, 'other-lab'), 'another-lab'])
    conn.commit()
    conn.close()

    assert _fill_and_prune(shared_db, tmp_path) == 2

    assert _query(shared_db, "SELECT lab_id FROM events WHERE message='other-lab'") == [('another-lab',)]


def test_pruning_twice_on_a_shared_database_still_keeps_the_schema(shared_db, tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'datetime', _FrozenDatetime)
    columns, indexes = _columns(shared_db, 'events'), _named_indexes(shared_db, 'events')
    assert _fill_and_prune(shared_db, tmp_path) == 2

    conn = sqlite3.connect(shared_db)
    insert_events(conn, [_row('2026-04-02 08:00:00', 'april-late')])
    conn.commit()
    conn.close()
    assert prune_events(shared_db, keep_days=30, archive_dir=str(tmp_path / 'archive')) == 1

    assert _columns(shared_db, 'events') == columns
    assert _named_indexes(shared_db, 'events') == indexes
    init_cloud_tables(shared_db)                                       # the API can start again on it
    assert _columns(shared_db, 'events') == columns


@pytest.mark.parametrize('shared', [False, True])
def test_pruning_keeps_the_rule_against_duplicate_events(tmp_path, monkeypatch, shared):
    monkeypatch.setattr(db, 'datetime', _FrozenDatetime)
    path = str(tmp_path / 'events.db')
    init_db(path)
    if shared:
        init_cloud_tables(path)
    unique_before = _unique_column_sets(path, 'events')
    assert unique_before == [['code', 'function', 'source_file', 'timestamp']]

    assert _fill_and_prune(path, tmp_path) == 2

    assert _unique_column_sets(path, 'events') == unique_before
    conn = sqlite3.connect(path)
    insert_events(conn, [_row(_RECENT, 'recent-a')])                   # already there: ignored
    conn.commit()
    conn.close()
    assert _query(path, "SELECT COUNT(*) FROM events WHERE message='recent-a'") == [(1,)]


def test_pruning_a_plain_on_site_database_leaves_its_schema_as_it_was(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'datetime', _FrozenDatetime)
    path = str(tmp_path / 'onsite.db')
    init_db(path)
    columns, indexes = _columns(path, 'events'), _named_indexes(path, 'events')

    assert _fill_and_prune(path, tmp_path) == 2

    assert _columns(path, 'events') == columns
    assert _named_indexes(path, 'events') == indexes


def test_pruning_leaves_no_spare_table_behind(shared_db, tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'datetime', _FrozenDatetime)
    tables = _query(shared_db, "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")

    assert _fill_and_prune(shared_db, tmp_path) == 2

    assert _query(shared_db, "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name") == tables


def test_no_on_site_code_writes_to_a_shared_table_without_naming_its_columns():
    """The predictions rows are written from inside the watcher loop and main.py,
    where a test cannot easily reach -- so check the source instead."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    sources = [*root.glob('*.py'), *(p for d in ('monitor', 'scripts', 'features', 'models', 'parsers')
                                     for p in (root / d).glob('*.py'))]
    # Also catches a statement split over two string literals.
    positional = re.compile(r'''INTO\s+(events|maintenance_events|predictions)[\s"']*VALUES''', re.IGNORECASE)

    offenders = [f'{p.name}: {m.group(0)}' for p in sources
                 for m in positional.finditer(p.read_text(encoding='utf-8'))]

    assert offenders == []


def _execute(db_path, *statements):
    conn = sqlite3.connect(db_path)
    for sql in statements:
        conn.execute(sql)
    conn.commit()
    conn.close()


def test_pruning_keeps_a_trigger_that_protects_the_events_table(shared_db, tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'datetime', _FrozenDatetime)
    _execute(shared_db, "CREATE TRIGGER events_append_only BEFORE UPDATE ON events "
                        "BEGIN SELECT RAISE(ABORT, 'append-only'); END")

    assert _fill_and_prune(shared_db, tmp_path) == 2

    with pytest.raises(sqlite3.IntegrityError, match='append-only'):
        _execute(shared_db, "UPDATE events SET message='tampered'")


def test_pruning_does_not_break_a_view_of_the_events_table(shared_db, tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'datetime', _FrozenDatetime)
    _execute(shared_db, "CREATE VIEW recent_faults AS SELECT message FROM events WHERE code='11001'")

    assert _fill_and_prune(shared_db, tmp_path) == 2

    assert len(_query(shared_db, "SELECT message FROM recent_faults")) == 2
    _execute(shared_db, "INSERT INTO events (timestamp, code, message, source_file) "
                        "VALUES ('2026-04-02 08:00:00', '11001', 'april-late', 'late.log')")
    assert prune_events(shared_db, keep_days=30, archive_dir=str(tmp_path / 'archive')) == 1   # and prunes again


def test_a_table_swap_that_cannot_start_changes_nothing(shared_db, tmp_path, monkeypatch):
    """A table already using the swap's working name makes the swap fail: nothing may be lost."""
    monkeypatch.setattr(db, 'datetime', _FrozenDatetime)
    _execute(shared_db, "CREATE TABLE events_old (x)")
    columns, indexes = _columns(shared_db, 'events'), _named_indexes(shared_db, 'events')

    assert _fill_and_prune(shared_db, tmp_path) == 0

    assert _query(shared_db, "SELECT COUNT(*) FROM events") == [(4,)]
    assert _columns(shared_db, 'events') == columns
    assert _named_indexes(shared_db, 'events') == indexes
    assert list((tmp_path / 'archive').glob('*.csv.gz')) == []
