# PetBMS: Cyclotron Maintenance Prediction Monitor

**Machine:** Siemens Eclipse RDS-111 (serial `MI_10150863_105`). PETrace 800 is also shown
in the app (telemetry only — it has no maintenance labels, so it is not predicted).
**Document date:** 12 October 2026, against `petbms-redesign`. Withdraws the June 2026
version, which described a different machine (GE), a different deployment model
(fully offline, no login), and figures that no longer hold.

This is an advisory tool. It does not control the cyclotron and does not replace site
procedures or the machine's own interlocks. It has four parts, per Order Form No. 1:

## 1. Part-health predictor

Predicts, per component, whether maintenance is likely within ~20 days — ION SOURCE,
FOILS (6 foils aggregated), BL1 Target 1, BL2 Target 1. Other components fall back to a
simple usage-counter estimate (no ML).

Runs on the **facility PC** (`monitor/watcher.py`), reading the machine's own log export
and POSTing a dashboard snapshot to the cloud API. The cloud service never runs the
predictor itself — it only ever shows the last snapshot it was sent.

**Not yet in production as tuned.** The facility PC still needs `MODEL_HMAC_KEY` set, a
retrain, and a restart before it runs the current models; unsigned models are refused.

**Accuracy (backtest, 11 October 2026, `docs/accuracy-improvements.md`):** 345 weekly
predictions, 52 service events. False alarms 53%. Alert within 30 days before service:
85%. Any alert in the 90 days before: 96%. 2 events had no alert. These figures are
optimistic — the models have seen this history.

## 2. Gauge identifier and logger

QR-coded gauges (`api/routes/scan.py`) are scanned to open the right reading form
(`api/routes/gauges.py`), logged with who, when, value, unit and status. A photo reading
goes through OCR (Gemini cloud in production — Render has no Ollama; local dev uses a
local Ollama model) and is only saved once the operator confirms it.

## 3. AI widget

An "Ask AI" tab (`api/routes/ask.py`) answers questions against live component data,
using a local Ollama model. The cloud service has no Ollama host configured, so Ask AI
is unavailable there until one is set (`OLLAMA_HOST`); this is read from config, not
checked live.

## 4. Pattern recogniser

Per-component charts of service interval, 30-day pre-service trend, and fault-code
frequency (`patterns.py`), viewed at `ui/patterns.html`. Not yet integrated into the app
or regenerated on a schedule — currently a one-off report, run from the command line.

## Deployment

Three pieces, not one offline box: the **facility PC** (prediction + gauge logging on
site), the **cloud API** (`api/`, FastAPI, deployed on Render as `petlab-api`), and the
**mobile/web app** (`mobile/`, Expo). Login is required; roles are viewer < operator <
admin. Everyone sees "Advisory only. Not a safety system. Follow site procedures." on
the dashboard and Ask screens.

## What's not covered here

Full findings, severities, and open items: the 12 October 2026 catalogue review
(`5 PetBMS core - findings.md`). Model data/lineage, evaluation protocol, and residual
risks: `docs/MODEL_CARD.md`. Retraining triggers: `docs/MODEL_GOVERNANCE.md`. There is no
agreed acceptance spec yet — a draft is with PET Labs for sign-off.
