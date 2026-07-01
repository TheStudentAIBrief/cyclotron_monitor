# QR Gauge Scan — Integration Notes for Johannes (Base44 eQMS)

This is for wiring the physical gauge QR labels up to the separate GxP eQMS system
(Base44-based, `EMLocation`/`EMReading` entities). No live integration exists yet —
this doc describes what's available today and the recommended path to a real
integration later.

## What the QR code encodes

Each printed gauge label encodes a plain `https`-style URL (currently `http://` in
dev; will be `https://` behind a real domain in production):

```
http://<host>:8000/scan/<gauge_name>
```

For example, gauge `0096` encodes:

```
http://<host>:8000/scan/0096
```

There is nothing proprietary or encoded in the QR itself — it's just a URL. Any
QR reader (including a stock phone camera) can open it.

## Default behavior: human-readable page

Scanning the QR with a phone camera opens the URL directly in a browser, which
returns a simple HTML page showing the gauge's latest reading, location, and
alert/action thresholds. This is meant for a technician standing at the gauge —
no login required.

## Machine-readable contract: `?format=json`

Appending `?format=json` to the same URL returns a JSON body instead of HTML.
This is the shape your corresponder should fetch/poll:

```
GET http://<host>:8000/scan/0096?format=json
```

```json
{
  "gauge_name": "0096",
  "location": "HVAC room (Production Primary)",
  "latest_reading": {
    "value": 87.0,
    "unit": "Pa",
    "timestamp": "2026-06-24T00:00:00Z",
    "confidence": "verified-photo"
  },
  "thresholds": {
    "alert_lo": 15.0,
    "alert_hi": 125.0,
    "action_lo": 10.0,
    "action_hi": 200.0
  },
  "scan_url": "http://<host>:8000/scan/0096"
}
```

If the gauge name doesn't exist (or has never had a reading with a location
attached), the endpoint returns `404` with:

```json
{"error": "unknown gauge", "gauge_name": "<whatever-was-requested>"}
```

Suggested mapping to your entities: `gauge_name` → an `EMLocation` identifier/code,
`location` → the `EMLocation` display name, and the `latest_reading` object → an
`EMReading` record (value/unit/timestamp/confidence). The `thresholds` object gives
you the alert/action bounds already configured on our side, if you want to mirror
them rather than re-enter them.

## Current security posture (intentional, for now)

This endpoint is **deliberately unauthenticated** — no JWT, no API key. It's
read-only (a single `SELECT`, never writes anything) and only exposes the same
handful of fields already printed on the physical label (gauge name, location,
latest value, thresholds). Nothing sensitive is exposed, and nothing can be
modified through it. This is fine for today's demo and for casual phone-camera
scans, but it is **not** meant to be the final state for a production
integration where your system calls back into ours programmatically.

## Recommended next step before production rollout

Before your corresponder starts polling this endpoint in production (rather than
a human occasionally scanning a label), we should add a shared-secret header
check, mirroring the pattern already used elsewhere in this codebase for
server-to-server calls (`api/routes/sync.py`, which requires an `X-Sync-Key`
header matching a configured value, returning `403` otherwise). The same idiom
would apply here: a new header (e.g. `X-Scan-Key`) checked against a configured
secret, required only when a request is clearly automated/server-to-server
rather than a one-off human scan. Happy to add this once you're ready to have
your corresponder call back into this API directly.
