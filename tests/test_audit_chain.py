"""Tamper-evident audit log.

audit_log lives in the same database it audits, so "append-only" was only a
convention: anyone with database access could edit or remove an entry and leave
no sign. Each entry now carries a hash of itself plus the previous entry's hash,
so an edit, a removal from the middle, or an insertion breaks the chain and
verify() says where. Entries that already existed when the chain was introduced
are chained once, at upgrade, so nothing in the log is left unchecked.
"""
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

import api.main as main
from api import audit
from api.db_cloud import get_conn, init_cloud_tables
from tests.test_rbac import _ADMIN, _PW, _hdr, _make_user, _seed_reading, db_path  # noqa: F401


def _verify(path: str) -> dict:
    conn = get_conn(path)
    try:
        return audit.verify(conn)
    finally:
        conn.close()


def _make_three_entries(c, path):
    """user_create, user_update, delete_gauge_reading -- one from each writer."""
    rid = _seed_reading(path)
    _make_user(c, 'oscar', 'operator')
    c.post('/api/admin/users/oscar', headers=_hdr(_ADMIN), json={'role': 'viewer'})
    assert c.delete(f'/api/gauges/{rid}', headers=_hdr(_ADMIN)).status_code == 200


def test_untouched_log_verifies(db_path):  # noqa: F811
    with TestClient(main.app) as c:
        _make_three_entries(c, db_path)
    result = _verify(db_path)
    assert result['ok'] is True
    assert result['chained'] == 4          # the three made here + the built-in login being created
    assert result['first_bad_id'] is None and result['bad_ids'] == []
    assert len(result['head']) == 64


def test_editing_an_entry_is_detected(db_path):  # noqa: F811
    with TestClient(main.app) as c:
        _make_three_entries(c, db_path)
    conn = get_conn(db_path)
    target = conn.execute("SELECT id FROM audit_log ORDER BY id LIMIT 1 OFFSET 1").fetchone()['id']
    conn.execute("UPDATE audit_log SET actor='someone-else' WHERE id=?", [target])
    conn.commit()
    conn.close()
    result = _verify(db_path)
    assert result['ok'] is False
    assert result['first_bad_id'] == target


def test_removing_an_entry_from_the_middle_is_detected(db_path):  # noqa: F811
    with TestClient(main.app) as c:
        _make_three_entries(c, db_path)
    conn = get_conn(db_path)
    ids = [r['id'] for r in conn.execute("SELECT id FROM audit_log ORDER BY id")]
    conn.execute("DELETE FROM audit_log WHERE id=?", [ids[1]])
    conn.commit()
    conn.close()
    result = _verify(db_path)
    assert result['ok'] is False
    assert result['first_bad_id'] == ids[2]   # the entry after the gap no longer links up


_OLD_AUDIT_SCHEMA = """
CREATE TABLE audit_log (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     TEXT NOT NULL,
    action TEXT NOT NULL,
    lab_id TEXT,
    actor  TEXT,
    detail TEXT
);
"""


def _old_production_db(tmp_path) -> str:
    """A database as production has it today: audit_log with rows and no hash columns."""
    path = str(tmp_path / 'old.db')
    conn = sqlite3.connect(path)
    conn.executescript(_OLD_AUDIT_SCHEMA)
    for i in range(3):
        conn.execute("INSERT INTO audit_log (ts, action, lab_id, actor, detail) VALUES (?,?,?,?,?)",
                     [f'2026-07-0{i + 1}T10:00:00+00:00', 'delete_gauge_reading', 'petlabs-pretoria', '',
                      f'{{"id": {i}}}'])
    conn.commit()
    conn.close()
    return path


def test_upgrade_chains_the_entries_that_already_existed(tmp_path):
    path = _old_production_db(tmp_path)
    init_cloud_tables(path)
    result = _verify(path)
    assert (result['ok'], result['chained']) == (True, 4)   # the 3 existing entries + the upgrade marker


def test_an_entry_from_before_the_upgrade_can_no_longer_be_edited_unnoticed(tmp_path):
    path = _old_production_db(tmp_path)
    init_cloud_tables(path)
    conn = get_conn(path)
    conn.execute("UPDATE audit_log SET detail='{}' WHERE id=1")
    conn.commit()
    conn.close()
    result = _verify(path)
    assert result['ok'] is False and result['first_bad_id'] == 1


def test_wiping_the_hashes_does_not_reset_the_log_to_unchecked(tmp_path):
    """Blanking every hash must read as tampering -- and a later restart must not
    quietly re-chain the altered log as if nothing happened."""
    path = _old_production_db(tmp_path)
    init_cloud_tables(path)
    conn = get_conn(path)
    conn.execute("UPDATE audit_log SET hash=NULL, prev_hash=NULL")
    conn.execute("UPDATE audit_log SET actor='someone-else' WHERE id=2")
    conn.commit()
    conn.close()
    assert _verify(path)['ok'] is False
    init_cloud_tables(path)                      # a restart
    assert _verify(path)['ok'] is False


def test_editing_the_recorded_content_of_an_entry_is_detected(db_path):  # noqa: F811
    """`detail` holds the deleted record itself -- the part most worth altering."""
    with TestClient(main.app) as c:
        _make_three_entries(c, db_path)
    conn = get_conn(db_path)
    target = conn.execute("SELECT id FROM audit_log WHERE action='delete_gauge_reading'").fetchone()['id']
    conn.execute("UPDATE audit_log SET detail='{}' WHERE id=?", [target])
    conn.commit()
    conn.close()
    assert _verify(db_path)['first_bad_id'] == target


def test_every_break_is_reported_not_just_the_first(db_path):  # noqa: F811
    """One old break must not hide tampering that happens after it."""
    with TestClient(main.app) as c:
        _make_three_entries(c, db_path)
        _make_user(c, 'vera', 'viewer')
    conn = get_conn(db_path)
    ids = [r['id'] for r in conn.execute("SELECT id FROM audit_log ORDER BY id")]
    conn.execute("UPDATE audit_log SET actor='x' WHERE id IN (?, ?)", [ids[0], ids[2]])
    conn.commit()
    conn.close()
    result = _verify(db_path)
    assert result['bad_ids'] == [ids[0], ids[2]]


def test_a_non_text_value_is_reported_as_a_bad_entry_not_a_crash(db_path):  # noqa: F811
    with TestClient(main.app) as c:
        _make_user(c, 'oscar', 'operator')
    conn = get_conn(db_path)
    conn.execute("UPDATE audit_log SET actor=x'00ff'")
    conn.commit()
    conn.close()
    result = _verify(db_path)
    assert result['ok'] is False and result['first_bad_id'] is not None


def test_concurrent_writers_cannot_fork_the_chain(db_path):  # noqa: F811
    def _writer(n):
        for i in range(15):
            conn = get_conn(db_path)
            try:
                if n % 2:                                  # like users.py: a write first...
                    conn.execute("INSERT INTO push_tokens (token, lab_id, registered_at) VALUES (?,?,?)",
                                 [f't{n}-{i}', 'lab', 'now'])
                else:                                      # ...like the delete route: a read first
                    conn.execute("SELECT COUNT(*) FROM gauge_readings").fetchone()
                audit.write(conn, 'test', f'writer-{n}', 'lab', '{}')
                conn.commit()
            finally:
                conn.close()

    threads = [threading.Thread(target=_writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    result = _verify(db_path)
    assert (result['ok'], result['chained']) == (True, 61)   # 60 + the built-in login being created


def test_an_unhashed_entry_slipped_in_after_the_chain_started_is_detected(db_path):  # noqa: F811
    with TestClient(main.app) as c:
        _make_user(c, 'oscar', 'operator')
    conn = get_conn(db_path)
    cur = conn.execute("INSERT INTO audit_log (ts, action, lab_id, actor, detail) VALUES (?,?,?,?,?)",
                       ['2026-10-01T10:00:00+00:00', 'user_update', 'petlabs-pretoria', 'ghost', '{}'])
    conn.commit()
    conn.close()
    result = _verify(db_path)
    assert result['ok'] is False
    assert result['first_bad_id'] == cur.lastrowid


def test_admin_can_check_the_log_over_the_api(db_path):  # noqa: F811
    with TestClient(main.app) as c:
        _make_three_entries(c, db_path)
        r = c.get('/api/admin/audit/verify', headers=_hdr(_ADMIN))
        _make_user(c, 'oscar2', 'operator')
        refused = c.get('/api/admin/audit/verify', headers=_hdr('oscar2'))
    assert r.status_code == 200
    assert r.json()['ok'] is True
    assert refused.status_code == 403


# ── entries removed from the END (no later entry is left to expose the gap) ───

def test_trimming_the_newest_entries_is_detected_without_waiting_for_a_backup(db_path):  # noqa: F811
    with TestClient(main.app) as c:
        _make_three_entries(c, db_path)
    conn = get_conn(db_path)
    last = conn.execute("SELECT MAX(id) FROM audit_log").fetchone()[0]
    conn.execute("DELETE FROM audit_log WHERE id >= ?", [last - 1])
    conn.commit()
    conn.close()
    result = _verify(db_path)
    assert result['ok'] is False
    assert result['removed'] == [{'after_id': last - 2, 'count': 2}]


def test_trimming_then_carrying_on_as_normal_is_still_detected(db_path):  # noqa: F811
    """The realistic case: remove the evidence, then the system keeps writing. The
    new entry links cleanly to the last survivor -- only the id it was given
    shows that entries once sat in between."""
    with TestClient(main.app) as c:
        _make_three_entries(c, db_path)
        conn = get_conn(db_path)
        last = conn.execute("SELECT MAX(id) FROM audit_log").fetchone()[0]
        conn.execute("DELETE FROM audit_log WHERE id = ?", [last])
        conn.commit()
        conn.close()
        _make_user(c, 'vera', 'viewer')
    result = _verify(db_path)
    assert result['ok'] is False
    assert result['bad_ids'] == []                              # the chain itself still links
    assert result['removed'] == [{'after_id': last - 1, 'count': 1}]


def test_an_untouched_log_reports_nothing_removed(db_path):  # noqa: F811
    with TestClient(main.app) as c:
        _make_three_entries(c, db_path)
    assert _verify(db_path)['removed'] == []


def test_gaps_that_already_existed_before_the_upgrade_are_not_false_alarms(tmp_path):
    """Production's existing log may already have gaps we know nothing about; the
    upgrade records where checked history starts instead of flagging them forever."""
    path = _old_production_db(tmp_path)
    conn = sqlite3.connect(path)
    conn.execute("DELETE FROM audit_log WHERE id = 2")
    conn.commit()
    conn.close()
    init_cloud_tables(path)
    result = _verify(path)
    assert (result['ok'], result['removed']) == (True, [])
    # ...but a removal after the upgrade is caught
    conn = get_conn(path)
    conn.execute("DELETE FROM audit_log WHERE id = (SELECT MAX(id) FROM audit_log)")
    conn.commit()
    conn.close()
    assert _verify(path)['ok'] is False


def test_each_entry_is_also_written_to_the_server_log(db_path, caplog):  # noqa: F811
    """The server log is kept outside the database, so it is an independent
    witness to what the audit log should contain."""
    with caplog.at_level('INFO', logger='uvicorn.error'):
        with TestClient(main.app) as c:
            _make_user(c, 'oscar', 'operator')
    conn = get_conn(db_path)
    entry_id, entry_hash = conn.execute("SELECT id, hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith('audit entry')]
    assert any(f'#{entry_id} ' in line and entry_hash in line and 'user_create' in line for line in lines)
    assert all(_PW not in line for line in lines)


# ── upgrade safety (the first production start-up) ───────────────────────────

def test_an_interrupted_upgrade_is_retried_cleanly_on_the_next_start(tmp_path, monkeypatch):
    """If the first start-up dies part-way through, the next one must still chain the
    existing entries -- not find the column already there and skip it forever."""
    path = _old_production_db(tmp_path)
    real = audit.chain_existing

    def _dies(conn):
        raise sqlite3.OperationalError('disk I/O error')

    monkeypatch.setattr(audit, 'chain_existing', _dies)
    with pytest.raises(sqlite3.OperationalError):
        init_cloud_tables(path)
    monkeypatch.setattr(audit, 'chain_existing', real)
    init_cloud_tables(path)
    result = _verify(path)
    assert (result['ok'], result['chained']) == (True, 4)


def test_restarting_does_not_write_a_second_upgrade_marker(tmp_path):
    path = _old_production_db(tmp_path)
    for _ in range(3):
        init_cloud_tables(path)
    conn = get_conn(path)
    markers = conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='audit_chain_started'").fetchone()[0]
    conn.close()
    assert markers == 1 and _verify(path)['ok'] is True


def test_upgrading_an_empty_old_log_works(tmp_path):
    path = str(tmp_path / 'empty-old.db')
    conn = sqlite3.connect(path)
    conn.executescript(_OLD_AUDIT_SCHEMA)
    conn.close()
    init_cloud_tables(path)
    result = _verify(path)
    assert (result['ok'], result['chained']) == (True, 1)


def test_a_rolled_back_change_leaves_no_hole_in_the_log(db_path):  # noqa: F811
    """An honest failure (the change and its audit entry both roll back) must not
    look like a removed entry afterwards."""
    with TestClient(main.app) as c:
        _make_user(c, 'oscar', 'operator')
        conn = get_conn(db_path)
        audit.write(conn, 'test', 'someone', 'lab', '{}')
        conn.rollback()
        conn.close()
        _make_user(c, 'vera', 'viewer')
    result = _verify(db_path)
    assert (result['ok'], result['removed']) == (True, [])


def test_a_forged_second_upgrade_marker_does_not_excuse_removed_entries(tmp_path):
    path = _old_production_db(tmp_path)
    init_cloud_tables(path)                                  # genuine marker written here
    conn = get_conn(path)
    for i in range(3):
        audit.write(conn, 'test', 'someone', 'lab', '{}')
    conn.commit()
    last = conn.execute("SELECT MAX(id) FROM audit_log").fetchone()[0]
    conn.execute("DELETE FROM audit_log WHERE id >= ?", [last - 1])   # trim two...
    audit.write(conn, 'audit_chain_started', 'system', '', '{}')      # ...and claim history starts here
    conn.commit()
    conn.close()
    assert _verify(path)['ok'] is False


@pytest.mark.parametrize('tamper', [
    "UPDATE sqlite_sequence SET seq = NULL WHERE name = 'audit_log'",
    "UPDATE sqlite_sequence SET seq = 'x' WHERE name = 'audit_log'",
    "DELETE FROM sqlite_sequence WHERE name = 'audit_log'",
])
def test_a_tampered_id_counter_is_reported_not_a_crash(db_path, tamper):  # noqa: F811
    with TestClient(main.app) as c:
        _make_three_entries(c, db_path)
    conn = get_conn(db_path)
    conn.execute(tamper)
    conn.commit()
    conn.close()
    assert _verify(db_path)['ok'] is False
