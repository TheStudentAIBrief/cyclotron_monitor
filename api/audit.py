"""Tamper-evident audit log.

audit_log sits in the same database it audits, so "append-only" was only a
convention -- anyone with database access could edit or remove an entry and
leave no sign. Three things now make tampering show:

1. Hash chain. Every entry stores a SHA-256 over its own content plus the
   previous entry's hash, so editing an entry, removing one from the middle or
   slipping one in breaks the chain.
2. Id continuity. Entry ids are issued by SQLite's AUTOINCREMENT counter, which
   never reuses a number. Entries removed from the END leave no later entry to
   break the chain, but they do leave the counter ahead of the last entry (or,
   once writing resumes, a hole in the ids) -- verify() reports both.
3. Server log. Each entry's id and hash are also written to the application
   log, which is kept outside the database: an independent witness to what the
   audit log should contain.

What none of this stops: someone with direct database access. They can rebuild
the chain, and for entries at the end they need only delete them and set the id
counter back. That is caught by a copy held elsewhere --
scripts/backup_cloud_db.py keeps the chain head from each off-box backup and
checks the next backup still contains it -- and, for entries newer than the
last backup, only by comparing against the server log. (The log line is written
just before the change commits; if two lines carry the same id, the first
belongs to a change that failed and the last one is the entry that exists.)
"""
import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone

# Same logger the rest of the API uses so the line reaches the host's log stream.
_log = logging.getLogger('uvicorn.error')

# Written once, by the upgrade that introduced the chain: marks where checked
# history starts (see chain_existing / verify).
_CHAIN_STARTED = 'audit_chain_started'


def _entry_hash(prev_hash: str, ts: str, action: str, lab_id, actor, detail) -> str:
    h = hashlib.sha256()
    for part in (prev_hash, ts, action, lab_id or '', actor or '', detail or ''):
        data = part.encode('utf-8')
        # Length-prefixed so ("ab", "c") and ("a", "bc") can never hash the same.
        h.update(len(data).to_bytes(8, 'big'))
        h.update(data)
    return h.hexdigest()


def write(conn: sqlite3.Connection, action: str, actor: str, lab_id: str, detail: str) -> None:
    """Append one entry, linked to the one before it. The caller commits, so the
    entry lands in the same transaction as the change it records."""
    # Take the write lock before reading the previous hash, or two concurrent
    # writers could both link to the same predecessor and fork the chain.
    if not conn.in_transaction:
        conn.execute('BEGIN IMMEDIATE')
    row = conn.execute(
        'SELECT hash FROM audit_log WHERE hash IS NOT NULL ORDER BY id DESC LIMIT 1').fetchone()
    prev_hash = row[0] if row else ''
    ts = datetime.now(timezone.utc).isoformat(timespec='seconds')
    entry_hash = _entry_hash(prev_hash, ts, action, lab_id, actor, detail)
    cur = conn.execute(
        'INSERT INTO audit_log (ts, action, lab_id, actor, detail, prev_hash, hash) '
        'VALUES (?,?,?,?,?,?,?)',
        [ts, action, lab_id, actor, detail, prev_hash, entry_hash],
    )
    # Never the detail (it can hold record content). Logged before the caller
    # commits, so in the rare case the transaction then fails the log has one
    # line the table doesn't -- the safe direction for a witness.
    _log.info('audit entry #%d %s by %s hash=%s', cur.lastrowid, action, actor or '-', entry_hash)


def chain_existing(conn: sqlite3.Connection) -> None:
    """Chain the entries that predate the hash columns, in id order.

    Called exactly once per database -- by the migration that adds the columns
    (api/db_cloud.py), never on a later start-up -- so entries that are missing
    a hash afterwards are always a sign of tampering, not of age. Ends by
    writing a marker entry: ids are only required to be continuous from there
    on, because the older entries may already have gaps nobody recorded."""
    rows = conn.execute(
        'SELECT id, ts, action, lab_id, actor, detail FROM audit_log ORDER BY id').fetchall()
    prev_hash = ''
    for entry_id, ts, action, lab_id, actor, detail in rows:
        entry_hash = _entry_hash(prev_hash, ts, action, lab_id, actor, detail)
        conn.execute('UPDATE audit_log SET prev_hash = ?, hash = ? WHERE id = ?',
                     [prev_hash, entry_hash, entry_id])
        prev_hash = entry_hash
    write(conn, _CHAIN_STARTED, 'system', '', json.dumps({'existing_entries': len(rows)}))


def verify(conn: sqlite3.Connection) -> dict:
    """Check the whole log.

    `bad_ids`: every entry that was altered, follows a removed entry, or has no
    valid hash. After a bad entry the walk re-anchors on that entry's stored
    hash and keeps going, so one old break can't hide tampering after it.
    `removed`: where entries are missing by id -- {'after_id', 'count'} -- both
    holes between entries and entries trimmed off the end.
    `head`: the hash of the last entry."""
    rows = conn.execute(
        'SELECT id, ts, action, lab_id, actor, detail, prev_hash, hash FROM audit_log ORDER BY id'
    ).fetchall()
    markers = [r[0] for r in conn.execute(
        'SELECT id FROM audit_log WHERE action = ? ORDER BY id', [_CHAIN_STARTED])]
    # Only the FIRST marker is genuine (the upgrade writes exactly one). A later
    # one would be an attempt to move the start of checked history past a hole.
    marker = markers[0] if markers else None
    # Ids must be continuous from the upgrade marker, or from the first entry in
    # a database that has had the chain from the start.
    continuous_from = marker if marker is not None else (rows[0][0] if rows else 0)

    prev_hash, prev_id, chained, bad_ids, removed = '', None, 0, list(markers[1:]), []
    for entry_id, ts, action, lab_id, actor, detail, stored_prev, stored_hash in rows:
        try:
            good = (stored_prev == prev_hash and stored_hash == _entry_hash(
                prev_hash, ts, action, lab_id, actor, detail))
        except (AttributeError, TypeError):   # a field holds something that isn't text
            good = False
        if good:
            chained += 1
        else:
            bad_ids.append(entry_id)
        if isinstance(stored_hash, str):
            prev_hash = stored_hash
        if prev_id is not None and prev_id >= continuous_from and entry_id != prev_id + 1:
            removed.append({'after_id': prev_id, 'count': entry_id - prev_id - 1})
        prev_id = entry_id

    try:
        seq = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'audit_log'").fetchone()
    except sqlite3.OperationalError:   # no AUTOINCREMENT counter to compare against
        seq = None
    issued, last_id = (seq[0] if seq else 0), (prev_id or 0)
    # The counter can only ever be at or ahead of the last entry; anything else
    # (missing, not a number, behind) means it was edited by hand.
    counter_ok = isinstance(issued, int) and issued >= last_id
    if counter_ok and issued > last_id and last_id >= continuous_from:
        removed.append({'after_id': last_id, 'count': issued - last_id})

    bad_ids = sorted(set(bad_ids))
    return {'ok': not bad_ids and not removed and counter_ok, 'chained': chained, 'head': prev_hash,
            'first_bad_id': bad_ids[0] if bad_ids else None, 'bad_ids': bad_ids,
            'removed': removed, 'counter_ok': counter_ok}
