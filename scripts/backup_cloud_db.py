"""
Pull an off-box backup of the cloud database and check it.

The cloud instance (e.g. Render) keeps everything in one SQLite file on one disk
with no shell access. This downloads a consistent copy through the admin-only
GET /api/admin/backup, then checks:
  1. the copy is a healthy SQLite database,
  2. its audit log's hash chain is intact (api/audit.py), and
  3. the audit log still contains the chain head recorded by the PREVIOUS
     backup -- if it doesn't, history was rewritten on the server since then.

Run it on a schedule from a machine you control. Exits non-zero if any check
fails (the downloaded file is kept either way, for investigation).

Usage:
    python scripts/backup_cloud_db.py --api https://petlab-api-qad3.onrender.com

The account must be an admin. Username/password come from --username and the
PETBMS_USERNAME / PETBMS_PASSWORD environment variables, or are prompted for.
Backups contain every account's password hash: keep --out-dir private (the
default `backups/` is git-ignored).

Check 3 compares against `last_audit_head.txt` in --out-dir. On the very first
run there is nothing to compare with, and the script says so. To accept a known
break and start a new baseline, delete that file deliberately and run again.
"""
import argparse
import getpass
import os
import pathlib
import sqlite3
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from api import audit  # noqa: E402

_HEAD_FILE = 'last_audit_head.txt'


def check_backup(path: str, previous_head: str | None) -> dict:
    """Checks 1-3 above. `head` is this copy's chain head, to save for next time."""
    problems, head, chained = [], '', 0
    # as_uri() escapes characters like '#' that would otherwise cut the path short.
    uri = pathlib.Path(path).resolve().as_uri() + '?mode=ro'
    try:
        conn = sqlite3.connect(uri, uri=True)
        try:
            integrity = conn.execute('PRAGMA integrity_check').fetchone()[0]
            if integrity != 'ok':
                problems.append(f'database integrity check failed: {integrity}')
            chain = audit.verify(conn)
            head, chained = chain['head'], chain['chained']
            if chain['bad_ids']:
                problems.append(
                    f"audit log chain is broken at {len(chain['bad_ids'])} entr"
                    f"{'y' if len(chain['bad_ids']) == 1 else 'ies'}, first ids: {chain['bad_ids'][:10]}")
            if not chain['counter_ok']:
                problems.append("the audit log's id counter has been altered (entries may have "
                                'been removed from the end)')
            for gap in chain['removed'][:10]:
                problems.append(
                    f"{gap['count']} audit log entr{'y is' if gap['count'] == 1 else 'ies are'} "
                    f"missing after entry id {gap['after_id']}")
            if previous_head and not conn.execute(
                    'SELECT 1 FROM audit_log WHERE hash = ?', [previous_head]).fetchone():
                problems.append(
                    'audit log no longer contains the head recorded by the previous backup '
                    '-- entries were rewritten or removed on the server since then')
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        problems.append(f'not a readable database: {exc}')
    return {'ok': not problems, 'problems': problems, 'head': head, 'chained': chained}


def read_previous_head(out_dir: pathlib.Path) -> str | None:
    """The chain head saved by the last good run, or None on a genuine first run.

    An existing-but-empty file is an error, not a first run: silently skipping
    the comparison would let a rewritten log become the new baseline."""
    head_file = pathlib.Path(out_dir) / _HEAD_FILE
    if not head_file.exists():
        return None
    head = head_file.read_text(encoding='utf-8').strip()
    if not head:
        sys.exit(f'ERROR: {head_file} exists but is empty, so there is nothing to compare this '
                 'backup against. Restore it, or delete it deliberately to start a new baseline.')
    return head


def write_head(out_dir: pathlib.Path, head: str) -> None:
    head_file = pathlib.Path(out_dir) / _HEAD_FILE
    tmp = head_file.with_suffix('.tmp')
    tmp.write_text(head, encoding='utf-8')
    os.replace(tmp, head_file)   # never leaves a half-written (empty) baseline behind


def _login(api: str, username: str, password: str) -> str:
    # allow_redirects=False: a redirect would re-send the password to another host.
    r = requests.post(f'{api}/auth/login', data={'username': username, 'password': password},
                      timeout=60, allow_redirects=False)
    if r.status_code != 200:
        sys.exit(f'ERROR: login failed (HTTP {r.status_code}).')
    return r.json()['access_token']


def _download(api: str, token: str, dest: pathlib.Path) -> None:
    part = dest.with_suffix('.part')   # only a complete download gets a backup's name
    with requests.get(f'{api}/api/admin/backup', headers={'Authorization': f'Bearer {token}'},
                      stream=True, timeout=300, allow_redirects=False) as r:
        if r.status_code != 200:
            sys.exit(f'ERROR: backup download failed (HTTP {r.status_code}).')
        with open(part, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    os.replace(part, dest)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--api', required=True, help='e.g. https://petlab-api-qad3.onrender.com')
    parser.add_argument('--out-dir', default=str(ROOT / 'backups'))
    parser.add_argument('--username', default=os.environ.get('PETBMS_USERNAME'))
    args = parser.parse_args()

    api = args.api.rstrip('/')
    parts = urlsplit(api)
    if parts.scheme != 'https' and parts.hostname not in ('localhost', '127.0.0.1'):
        sys.exit('ERROR: --api must be https (the admin password and the backup travel over it).')

    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    previous_head = read_previous_head(out_dir)   # before logging in: fail early on a bad baseline

    username = args.username or input('Admin username: ').strip()
    password = os.environ.get('PETBMS_PASSWORD') or getpass.getpass('Password: ')

    dest = out_dir / f"petlab-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.db"
    _download(api, _login(api, username, password), dest)
    result = check_backup(str(dest), previous_head)

    print(f'Saved {dest} ({dest.stat().st_size} bytes)')
    print(f"Audit log: {result['chained']} entries verified")
    if not result['ok']:
        for problem in result['problems']:
            print(f'PROBLEM: {problem}')
        print(f'{_HEAD_FILE} was NOT updated, so the next run compares against the same last good backup.')
        return 1
    write_head(out_dir, result['head'])
    if previous_head is None:
        print('FIRST RUN: there was no earlier backup to compare against, so check 3 '
              '(history not rewritten) did NOT run. It will from the next run on.')
    else:
        print('All checks passed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
