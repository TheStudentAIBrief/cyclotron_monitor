# Access control — roles and accounts

Role-based access control and least privilege, added following an external
security review. Before this, the API had exactly one login, so every caller
had the same full access.

## Roles

Each role includes everything the one above it can do.

| Role | Can do | Routes |
|---|---|---|
| `viewer` | Read dashboards, records, gauge history; ask the assistant; register for alerts | every `GET`, `POST /api/ask`, `POST /api/push/register` |
| `operator` | Submit gauge readings (manual or photo) | `POST /api/gauges`, `POST /api/gauges/reading` |
| `admin` | Bulk import, delete a reading, manage accounts | `/api/admin/import/*`, `POST /api/gauges/import-csv`, `POST /api/gauges/eur-photos`, `DELETE /api/gauges/{id}`, `/api/admin/users*` |

A refused action returns `403` with a reason; the session stays valid.

Not covered by roles, by design: `POST /sync/dashboard` (server-to-server,
`X-Sync-Key` only) and `GET /scan/{gauge}` (QR labels, no login possible).

## Accounts

- **Built-in admin** — the original login in `data/.credentials.json`
  (`BOOTSTRAP_USERNAME` / `BOOTSTRAP_PASSWORD`). Always an admin, cannot be
  changed through the API. Keep it as the break-glass account; give people
  their own accounts instead of sharing it.
- **Per-person accounts** — rows in the `users` table, created by an admin.

Usernames are lowercase (letters, digits, dot, dash, underscore).

The role is read from the store on every request, so a demotion or a disabled
account takes effect immediately, including for tokens already issued. A
password reset, a disable or a re-enable also ends every session the account
already had — the person has to log in again — so resetting a password really
does evict whoever was using the old one. A role change on its own does not
log anyone out.

## Managing accounts (admin only)

```bash
# create
curl -X POST $API/api/admin/users -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"username": "j.smith", "password": "<12+ characters>", "role": "operator"}'

# list (never returns password hashes)
curl $API/api/admin/users -H "Authorization: Bearer $TOKEN"

# change role / disable / reset password -- send only the fields to change
curl -X POST $API/api/admin/users/j.smith -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"disabled": true}'
```

Every create/update is written to `audit_log` (`user_create` / `user_update`)
with the admin who did it. Passwords are never logged.

## Least privilege in practice

- **Security testing and any automated tool** must use a dedicated `viewer`
  account, never an admin one — a tool's own "read-only" instructions are
  advisory, a viewer account that cannot write is a real boundary.
- **Data pushes** (`scripts/push_data_to_cloud.py` and the import scripts) need
  `admin`. Use a dedicated admin account for them so its activity is separable
  in the audit log, and disable it when not in use.
- **Day-to-day lab use** is `operator`.

## Audit log and backups

- **Tamper-evident audit log.** Each `audit_log` entry stores a hash of itself
  plus the previous entry's hash (`api/audit.py`), so an edited, removed or
  inserted entry breaks the chain. `GET /api/admin/audit/verify` (admin) reports
  whether the chain is intact and lists every entry where it breaks. Entries
  that already exist are chained once, on the first start-up after this deploys;
  from then on an entry without a valid hash always counts as a break.
- **Off-box backup.** `GET /api/admin/backup` (admin) returns a consistent copy
  of the whole database; taking one is itself audit-logged. Run
  `python scripts/backup_cloud_db.py --api https://<host>` on a schedule from a
  machine you control: it saves the copy to `backups/` (git-ignored — it holds
  every password hash), checks the copy and its audit chain, and checks the log
  still contains the chain head recorded by the previous backup. That last
  check is what catches a log rebuilt from scratch on the server; it exits
  non-zero if anything fails. The first run has nothing to compare against and
  says so. To accept a known break and start again, delete
  `backups/last_audit_head.txt` deliberately.
- **Entries removed from the end.** Removing the newest entries leaves no later
  entry to break the chain, so two more things cover it. Entry ids come from a
  counter that never reuses a number: the verify check (and the backup script)
  report entries missing by id, whether trimmed off the end or left as a hole
  once writing resumes. And each entry's id and hash are also written to the
  server log (`audit entry #<id> … hash=…`), which is kept outside the database.
- Limit to know about: someone with direct database access who rebuilds the
  chain *and* resets the id counter can still remove entries newer than the last
  backup; only the server log then shows it. Running the backup script often
  (hourly is reasonable) keeps that window short.

## Deploying this change

1. No migration step: new tables and columns are created on start-up, and the
   existing login keeps working as admin.
2. `mobile/dist` is rebuilt in this change, so the app shows the server's reason
   for a refused action instead of logging the user out.
3. The on-prem sync client now refuses a `cloud_api_url` that is not `https`
   (except to the local machine). On any machine that runs the sync, run
   `python -m monitor.cloud_sync` after updating: it prints whether the sync
   will run and exits non-zero if the configured address is refused (it never
   prints the key). A refused address is also logged as an ERROR on every
   refresh. `config.json.example` uses `https`, so a standard setup is unaffected.
4. After deploy: create per-person accounts, then stop sharing the built-in
   admin; schedule the backup script.
5. Treat this deploy as one-way. The previous release writes audit entries
   without a hash, so anything it records after a rollback (e.g. a deleted
   reading) is reported as a broken entry from then on. If a rollback is
   unavoidable, avoid deletions while on the old release and note the ids.
