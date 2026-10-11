"""Off-box backup of the cloud database.

The cloud data is one SQLite file on one disk with no shell access, so the only
way to get a copy off the box is through the API. GET /api/admin/backup hands an
admin a consistent snapshot; scripts/backup_cloud_db.py pulls and checks it on a
schedule. A copy held elsewhere is also what makes a rewritten audit log
provable: the head of the chain saved last time must still be in the next copy.
"""
import sqlite3

import pytest
from fastapi.testclient import TestClient

import api.main as main
from api import audit
from api.db_cloud import get_conn
from scripts import backup_cloud_db
from tests.test_rbac import _ADMIN, _hdr, _make_user, _seed_reading, db_path  # noqa: F401


def _download(c, tmp_path, name='backup.db'):
    r = c.get('/api/admin/backup', headers=_hdr(_ADMIN))
    assert r.status_code == 200, r.text
    path = tmp_path / name
    path.write_bytes(r.content)
    return path


def test_admin_gets_a_usable_copy_of_the_whole_database(db_path, tmp_path):  # noqa: F811
    rid = _seed_reading(db_path)
    with TestClient(main.app) as c:
        _make_user(c, 'oscar', 'operator')
        copy = _download(c, tmp_path)
    conn = sqlite3.connect(copy)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == 'ok'
    assert conn.execute("SELECT COUNT(*) FROM gauge_readings WHERE id=?", [rid]).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    conn.close()


def test_only_an_admin_can_take_a_backup(db_path):  # noqa: F811
    """The copy contains every password hash -- operators and viewers must not get it."""
    with TestClient(main.app) as c:
        _make_user(c, 'oscar', 'operator')
        assert c.get('/api/admin/backup', headers=_hdr('oscar')).status_code == 403
        assert c.get('/api/admin/backup').status_code == 401


def test_taking_a_backup_is_itself_audited(db_path, tmp_path):  # noqa: F811
    with TestClient(main.app) as c:
        copy = _download(c, tmp_path)
    conn = get_conn(db_path)
    row = conn.execute("SELECT actor FROM audit_log WHERE action='db_backup'").fetchone()
    conn.close()
    assert row is not None and row['actor'] == _ADMIN
    # ...and the entry is inside the copy too, so the copy records its own making
    inside = sqlite3.connect(copy)
    assert inside.execute("SELECT COUNT(*) FROM audit_log WHERE action='db_backup'").fetchone()[0] == 1
    inside.close()


def test_backup_check_accepts_a_copy_that_continues_the_previous_one(db_path, tmp_path):  # noqa: F811
    with TestClient(main.app) as c:
        first = _download(c, tmp_path, 'first.db')
        _make_user(c, 'oscar', 'operator')
        second = _download(c, tmp_path, 'second.db')
    previous_head = backup_cloud_db.check_backup(str(first), previous_head=None)['head']
    result = backup_cloud_db.check_backup(str(second), previous_head=previous_head)
    assert result['ok'] is True and result['problems'] == []


def test_backup_check_flags_history_rewritten_since_the_last_copy(db_path, tmp_path):  # noqa: F811
    """Someone with database access rebuilds the whole chain after altering an old
    entry: the log verifies on its own, but no longer contains the head we saved."""
    with TestClient(main.app) as c:
        _make_user(c, 'oscar', 'operator')
        first = _download(c, tmp_path, 'first.db')
        previous_head = backup_cloud_db.check_backup(str(first), previous_head=None)['head']

        conn = get_conn(db_path)
        rows = [dict(r) for r in conn.execute("SELECT ts, action, lab_id, actor, detail FROM audit_log ORDER BY id")]
        conn.execute("DELETE FROM audit_log")
        for r in rows:
            audit.write(conn, r['action'], 'innocent-bystander', r['lab_id'], r['detail'])
        conn.commit()
        conn.close()

        second = _download(c, tmp_path, 'second.db')
    result = backup_cloud_db.check_backup(str(second), previous_head=previous_head)
    assert result['ok'] is False
    assert any('previous backup' in p for p in result['problems'])


def test_backup_check_flags_a_broken_chain(db_path, tmp_path):  # noqa: F811
    with TestClient(main.app) as c:
        _make_user(c, 'oscar', 'operator')
        conn = get_conn(db_path)
        conn.execute("UPDATE audit_log SET actor='someone-else'")
        conn.commit()
        conn.close()
        copy = _download(c, tmp_path)
    result = backup_cloud_db.check_backup(str(copy), previous_head=None)
    assert result['ok'] is False


def test_backup_response_must_not_be_cached(db_path):  # noqa: F811
    """It is a file full of password hashes."""
    with TestClient(main.app) as c:
        r = c.get('/api/admin/backup', headers=_hdr(_ADMIN))
    assert r.headers['cache-control'] == 'no-store'


def test_backup_check_rejects_a_file_that_is_not_a_database(tmp_path):
    """e.g. an HTML error page saved under a backup's name."""
    fake = tmp_path / 'petlab-fake.db'
    fake.write_text('<html>Service unavailable</html>')
    result = backup_cloud_db.check_backup(str(fake), previous_head=None)
    assert result['ok'] is False


def test_backup_check_flags_recent_entries_trimmed_back_past_the_last_backup(db_path, tmp_path):  # noqa: F811
    with TestClient(main.app) as c:
        _make_user(c, 'oscar', 'operator')
        first = _download(c, tmp_path, 'first.db')
        previous_head = backup_cloud_db.check_backup(str(first), previous_head=None)['head']
        conn = get_conn(db_path)
        conn.execute("DELETE FROM audit_log WHERE id >= (SELECT MAX(id) - 1 FROM audit_log)")
        conn.commit()
        conn.close()
        second = _download(c, tmp_path, 'second.db')
    result = backup_cloud_db.check_backup(str(second), previous_head=previous_head)
    assert result['ok'] is False


def test_a_first_run_is_reported_as_unchecked_and_an_empty_baseline_is_an_error(tmp_path):
    """The continuity check silently not running must never look like a pass:
    an emptied baseline file would otherwise make a rewritten log the new normal."""
    assert backup_cloud_db.read_previous_head(tmp_path) is None            # genuine first run
    (tmp_path / backup_cloud_db._HEAD_FILE).write_text('')
    with pytest.raises(SystemExit):
        backup_cloud_db.read_previous_head(tmp_path)
    backup_cloud_db.write_head(tmp_path, 'a' * 64)
    assert backup_cloud_db.read_previous_head(tmp_path) == 'a' * 64


def test_admin_can_see_how_their_request_reaches_the_server(db_path):  # noqa: F811
    """Needed before turning on TRUST_FORWARDED_FOR: what does the proxy in front of
    the server actually send?"""
    with TestClient(main.app) as c:
        r = c.get('/api/admin/client-address', headers={**_hdr(_ADMIN), 'X-Forwarded-For': '1.2.3.4, 203.0.113.7'})
        _make_user(c, 'oscar', 'operator')
        refused = c.get('/api/admin/client-address', headers=_hdr('oscar'))
    assert r.status_code == 200
    body = r.json()
    assert body['x_forwarded_for'] == ['1.2.3.4, 203.0.113.7']
    assert body['connecting_address'] == 'testclient'
    assert body['counted_as_now'] == 'testclient'
    assert body['counted_as_if_trusted'] == '203.0.113.7'
    assert refused.status_code == 403
