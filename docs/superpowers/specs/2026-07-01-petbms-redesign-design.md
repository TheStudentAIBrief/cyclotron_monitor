# PetBMS Redesign — Design Spec

- **Date:** 2026-07-01
- **Status:** Design approved (brainstorming). Next: implementation plan.
- **Author:** Theo Laredo (with Claude Code)

## Purpose

Take the PET Lab Monitor app (`cyclotron_monitor`) from a working local-dev prototype to a
polished, data-rich, installable product, rebranded as **PetBMS**. The app monitors a cyclotron
for **PET Labs Pharmaceuticals (Pty) Ltd** (Pretoria, South Africa) — the real-world radiopharmaceutical
producer this app serves. This spec covers: ingesting newly-received operational data, a signature
color system sourced from PET Labs' real branding, a UI/navigation cleanup, and an installable
app-like delivery method that avoids the Apple Developer Program.

## Decisions (locked during brainstorming)

1. **Branding source:** PET Labs Pharmaceuticals' actual identity — extracted from their email
   signature logo (navy/maroon/teal orbiting spheres, black "PET Labs Pharmaceuticals" wordmark)
   and their live site (petlabs.co.za: primary blue `#1863DC`, deep blue `#0056A7`). NOT the
   unrelated US pet-supplement brand "PetLab Co".
2. **App name:** **PetBMS** — replaces "PET Lab Monitor" / "cyclotron monitor" everywhere user-facing.
3. **Delivery:** Installable **PWA** (Progressive Web App) via Expo web export, backed by the
   existing FastAPI/Render cloud backend. Add-to-home-screen gives a native-app look (icon, splash,
   standalone chrome, no browser bar). Graceful fallback to a normal responsive website if not
   installed. **No Apple Developer Program, no App Store, no EAS native build** — explicit user
   constraint. The existing Expo Go dev-build path for on-device testing is unaffected.
4. **New data to integrate:** 6 downloaded `.eml` files containing 156 unique raw cyclotron beam
   log files (`1.log`–`159.log`, gap at 12–14, tracer batch "geps", dated from 2024-06-16) and
   17 backfill gauge photos (currently the `gauge_readings` table is empty — no photos ever
   submitted through the app).
5. **Out of scope for this pass:** the already-approved "Corner AI" floating chat bubble spec
   (`2026-06-30-corner-ai-widget-design.md`) and native iOS/Android home-screen widgets
   (superseded by the PWA approach).
6. **Obsidian logging:** ongoing work logged to the Obsidian vault as it happens (via the
   `jameswolensky/obsidian-second-brain` skill, install pending explicit user permission) in
   addition to the existing memory system.

## Architecture

No new services. Three existing pieces get extended:

```
Downloads (.eml files)  →  one-off extraction script  →  data/log_import/*.log
                                                             │
mobile/ (Expo/React Native)   api/ (FastAPI, Render)   ingest.py + parsers/beam_parser.py
  - Theme.ts (new)              - unchanged endpoints        │
  - dashboard widgets  ◄────────────────────────────────►  data/cyclotron.db
  - PWA manifest + SW (new)                                  ▲
  - rebrand → PetBMS                                    gauge OCR backfill script (new, one-off)
                                                          17 photos → qwen2.5vl:7b → gauge_readings
```

## Components

| Component | Status | Responsibility |
|---|---|---|
| `scripts/extract_eml_logs.py` | **new** | pulls `.log` attachments + logo out of the 6 `.eml` files, dedupes by filename, drops into a staging dir |
| `parsers/beam_parser.py` | edit | content-sniff detection (header row match) so numbered `.log` files are recognized, not just `*beam*`/`*hyper*`/`*ui*` filenames |
| `ingest.py` | edit | call the widened detection; idempotent re-run safe (existing upsert pattern) |
| `scripts/backfill_gauge_photos.py` | **new** | feeds the 17 staged gauge photos through the existing OCR pipeline into `gauge_readings` |
| `mobile/constants/Theme.ts` | **new** | single source of truth for PetBMS colors/typography, replacing hardcoded hex across screens |
| `mobile/app/*` screens | edit | consume `Theme.ts`; visual cleanup; nav simplification |
| `mobile/app.json` / `web/manifest.json` | edit/new | PetBMS name, icons, standalone display mode |
| Dashboard screen | edit | new widgets for beam-parameter history + gauge photo history (previously empty states) |

## Data flow

1. `extract_eml_logs.py` reads the 6 local `.eml` files, deduplicates `.log` attachments by
   filename (later email in the sequence wins on conflict — verified identical payloads for
   overlapping numbers), writes to a staging directory + extracts the logo PNG for color reference.
2. `ingest.py` (existing entrypoint, widened detection) parses each `.log` via `beam_parser.py`,
   aggregates daily stats, upserts into `beam_daily` / `events` tables — same code path as today,
   just recognizing more filenames.
3. `backfill_gauge_photos.py` runs each of the 17 staged images through the existing OCR
   (`qwen2.5vl:7b`) function used by the live gauge-submission endpoint, inserting rows into
   `gauge_readings` with a `source: "backfill"` marker so they're distinguishable from live
   submissions.
4. Dashboard queries these now-populated tables and renders new widgets (trend cards, gauge
   history) instead of empty states.
5. `mobile web` export produces static assets + `manifest.json`; served by the existing backend;
   installable to a phone home screen.

## Error handling

- `.eml` extraction: skip malformed parts, log a warning, continue (matches `ingest.py`'s existing
  per-file try/except pattern) — a bad email shouldn't abort the whole batch.
- Log parsing: unrecognized `.log` content (fails the header sniff) is skipped with a warning, not
  a hard failure — same tolerance the current beam/hyper split already has.
- Gauge OCR backfill: failures per-photo are logged and skipped; does not block the rest of the
  batch; a summary of failures is printed at the end for manual follow-up.
- PWA: if `manifest.json`/service worker fails to register, the app still functions as a normal
  website — no functional regression, only the "looks like an app" affordance is lost.

## Testing

- **Unit:** `beam_parser.py` header-sniff detection against real extracted `.log` fixtures (a
  sample from the 156, not all — representative sizes/content types); `ingest.py` idempotency
  (re-running doesn't duplicate rows); gauge OCR backfill against 2-3 real staged photos with
  known-good expected readings.
- **Integration:** full `ingest_all()` run against the complete staged log set into a scratch
  SQLite DB, assert row counts and no exceptions; dashboard endpoint returns non-empty data after
  ingestion.
- **Visual/manual:** theme applied consistently across all 5 tabs; PWA installs on a phone and
  looks/behaves like a native app (icon, splash, no browser chrome); fallback website still works
  if not installed.
- **Final regression:** full existing test suite (`mobile/__tests__/*`, `tests/*`) plus the new
  tests above, run together, before calling this done.

## Future (explicitly deferred)

Corner AI chat bubble (spec already exists, separate work) · native iOS/Android home-screen
widgets · EAS/App Store distribution.
