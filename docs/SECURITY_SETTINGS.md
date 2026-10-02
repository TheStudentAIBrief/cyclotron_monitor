# Security settings — what to set, and where

Settings and one-time steps that go with the security controls. Accounts, roles,
the audit log and backups are in [ACCESS_CONTROL.md](ACCESS_CONTROL.md).

## Cloud service (Render environment)

| Setting | What it does | Default if unset |
|---|---|---|
| `CLOUD_SYNC_KEY` | The key the facility PC presents to replace the dashboard. | Sync is off. |
| `CLOUD_SYNC_KEY_NEXT` | A second key accepted alongside the first while rotating. | Only the first key works. |
| `SYNC_ALLOWED_SOURCES` | Addresses or CIDR ranges (IPv4 or IPv6) a sync may come from, comma-separated. A sync with a valid key from anywhere else is refused and written to the audit log. Needs `TRUST_FORWARDED_FOR` to be right first: otherwise every caller appears as the proxy. | A valid key is accepted from any address. |
| `TRUST_FORWARDED_FOR=1` | Take the caller's address from the proxy's `X-Forwarded-For`. Check `GET /api/admin/client-address` first. | Every caller appears as the proxy. |
| `QR_LINK_SECRET` | QR links made by the server and the label script carry a code derived from this. | Links carry no code. |
| `QR_REQUIRE_CODE=1` | A gauge page opens only for a link with the right code. | A gauge name alone opens its page. |
| `BOOTSTRAP_PASSWORD` + `BOOTSTRAP_FORCE_RESET=true` | Replace the built-in admin's password on the next start. | The existing login is kept. |

### Rotating the sync key (no downtime)

1. Set `CLOUD_SYNC_KEY_NEXT` to a new random value. Both keys now work.
2. On the facility PC, set the `CLOUD_SYNC_KEY` environment variable to the new
   value and run `python -m monitor.cloud_sync` to confirm the sync will run.
3. Move the new value into `CLOUD_SYNC_KEY` on the cloud service and remove
   `CLOUD_SYNC_KEY_NEXT`. The old key stops working.

The first sync from each address (each IPv4 address, or IPv6 /64) writes a
`sync_source_new` entry to the audit log, so every source that has replaced the
dashboard is named there. At most 20 new sources are accepted in an hour; after
that a sync from yet another new source is refused until the hour is up, and one
entry says so. An address in regular use is never affected. Nothing is read from
a caller who has not shown the key, and a body that is not shaped like a
dashboard is refused, not stored.

### Replacing the built-in admin password

1. Set `BOOTSTRAP_PASSWORD` to a new password of at least 12 characters (check
   for a stray space at either end) and `BOOTSTRAP_FORCE_RESET=true`, then
   restart the service.
2. Log in with the new password, then remove `BOOTSTRAP_FORCE_RESET`.

The reset is written to the audit log (`builtin_password_reset`) and ends every
session that was opened with the old password. If the new password is missing or
too short, or the new login cannot be written, nothing changes and the old login
keeps working. Leaving the flag set does no harm: once the login matches, later
restarts change nothing. (So to end every session, set a new password; a reset to
the same password does nothing.)

### Switching on QR link codes

1. Set `QR_LINK_SECRET` on the cloud service to a long random value.
2. Reprint the labels with the same value set where the script runs:
   `QR_LINK_SECRET=... python scripts/generate_gauge_qr_labels.py ...`
3. Once the new labels are on the gauges, set `QR_REQUIRE_CODE=1`. Old labels
   stop working at that point.

## Facility PC

| Setting | What it does | Default if unset |
|---|---|---|
| `MODEL_HMAC_KEY` | Signs prediction models when they are trained and checks the signature before one is loaded. | Models are written unsigned and are not used. |
| `QR_LINK_SECRET` | Same value as on the cloud service, when printing labels. | Labels carry no code. |
| `MODEL_ALLOW_UNSIGNED=1` | Accept unsigned models. For a test machine only. | Unsigned models are refused. |
| `CLOUD_SYNC_KEY` | The sync key, kept out of `config.json`. | `cloud_sync_key` in `config.json` is used. |

### Signing the prediction models (do this when updating to this version)

A model file is code: loading one runs whatever it contains. From this version a
model that is unsigned, or that has changed since it was signed, is not loaded.
The component is still monitored on its lifetime counter alone. Because the
counter cannot see what the model would have seen, such a component is never
shown as GREEN: it is held at YELLOW or above, its card says why, and it is
named in the alert file. The same applies if a model file has been removed while
its other files are still there.

1. Set `MODEL_HMAC_KEY` to a long random value for the account the watcher runs as.
2. Re-train: `python main.py train-only`. The new models are signed as they are
   written.

There is deliberately no way to sign a model that is already on disk: the old
checksum can be recomputed by anyone who can replace the file, so signing what
is there would bless a swapped model too. Until step 2 is done, every component
that has a model shows YELLOW or above.

### The local dashboard

`python main.py serve` listens on this machine only (127.0.0.1). It refuses to
start on a network address unless TLS (`python setup_tls.py`) and a login
(`python setup_credentials.py`) are both in place.

`start_dev.ps1` is for a developer's own network. It serves the API to every
machine on that network, over plain HTTP unless `-EnableTLS` is given, with
auto-reload on. Do not run it on the facility network.

## AI assistant

The assistant is shown only component names from a fixed list, status words from
a fixed set, numbers, and sentences the system itself writes. Free text in the
synced dashboard — which whoever holds the sync key controls — never reaches it.
Every answer is returned with the real status of each component, read from the
data by the server, and the app shows that status under the answer.
