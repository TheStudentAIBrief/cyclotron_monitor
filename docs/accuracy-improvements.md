# PetBMS accuracy improvements — deploy runbook

Empirical diagnosis (walk-forward backtest) found the predictor's real weakness is a
**~60% false-alarm rate**, not detection: the calendar counter flags any component past
its average cycle as overdue, and it drives ~100% of false alarms. Separately, the
`events` table is pruned to ~30 days, so every historical fault-code feature is zero at
train time and dropped by `VarianceThreshold` — the models never learn from fault codes.

Improvements land as a **3-rung ladder**, each rung independently detection-preserving.
All numbers are from the walk-forward backtest (override ON vs the pre-change baseline of
MAE 27.0 / detection 96%–76% / level 54% / false-alarm 60%).

| Rung | What | Loose det. | Strict det. | Level acc | False-alarm |
|------|------|-----------|-------------|-----------|-------------|
| 1 | Gated model override (this PR) | 94% | 76% | 60% | **52%** |
| 2 | + restore fault history & retrain | 94% | **83%** | 58% | 52% |
| 3 | + event-grouped-CV trainer | **96%** | 80% | **68%** | **41%** |

## Safety contract (all rungs)
- The override only ever **downgrades** a RED/ORANGE alarm, and only when the model is
  confidently healthy (`model_risk < 0.25` **and** `model_days > 28`). A faulting
  component keeps high risk / low days and is never downgraded → detection preserved.
- Strict (≤30-day) detection is held at every rung; the only cost is ~2pp of *loose*
  (>30-day-early) detection on one component.
- Nothing changes the hand-tuned alert/blend logic or the `anomaly_score` bar.

## Rung 1 — ship the override (this PR)
Merge `accuracy-improvements` → `petbms-redesign`. Works with the current (fault-blind)
models immediately: false alarms 60%→52%, level accuracy 54%→60%, strict detection held.
No retrain, no data changes. Rollback = revert the merge.

## Rung 2 — un-blind the models (restore fault history + retrain)
Models are trained on the Render disk, but the fault-code archives live on the local
machine and Render's DB is also pruned — so there is no fault history on Render to restore.
Two options:

**Option A — retrain locally, upload pickles (recommended, least Render surface):**
```bash
# 1. Build a training DB with fault history restored (live DB read-only):
python scripts/restore_events_for_training.py --out data/cyclotron_train.db
# 2. Retrain against it:
python -c "from models.trainer import train_component, COMPONENTS; \
    [train_component(c, 'data/cyclotron_train.db', 'data/models') for c in COMPONENTS]"
# 3. Verify no regression:
python backtest.py            # expect strict detection up (~83%), false alarms ~52%
# 4. Upload data/models/*.pkl (+ .sha256) to the Render persistent disk (/data), restart.
```
Inference is unaffected by retention: fault features look back only 7–14 days, which the
live retention window still covers.

**Option B — restore on Render:** ship `data/events_archive/*` to the Render disk and run
`restore_events_for_training.py` + retrain there. More Render-side surface; only pick this
if local-train-and-upload isn't workable.

## Rung 3 — the headline (event-grouped-CV trainer)
The 41% false-alarm / 68% level-accuracy result needs the better-calibrated trainer already
prototyped in `experiments/event-grouped-cv/` (holds out whole maintenance events per fold;
its OOF-isotonic calibration is what sharpens the override's gate). Promote it to production
as a **separate, reviewed** change (it alters the training pipeline, so it deserves its own
PR + backtest), then re-run Rung 2's retrain. Do not bundle it into the override PR.

## Rung 4 — human-contact features (mined from the full 31M-event log)
The models only ever saw fault codes. Mining every event surfaced that **operator actions**
(commands, resets, tuning/calibration) and **machine-struggle telemetry** (comms timeouts,
excess-power, ISC-clamping, interlock aborts, standby/shutdown) rise before maintenance and were
100% ignored. Added 8 category window-count features (`humact_*`, `features/engineer.py`), read
from a precomputed `daily_category` table. Walk-forward result: **false alarms 41%→36%, strict
detection 76%→78%, loose detection held 96%, 0 events lost** — applied to all components except
BL2 (its 8 events overfit the extra features and it lost one real event; excluded).

`daily_category` is kept current automatically: the on-site monitor and `main.py predict`
call `aggregate_recent()` before every prediction (it recounts only from the last counted
day, never more than 15 days back, inside the 30-day event retention). For training,
`scripts/restore_events_for_training.py` rebuilds the table for the whole history from the
archives. The models must be retrained with the features present to use them.

If the table is missing or more than 7 days behind the prediction date, the features are
reported as unknown (NaN), not zero — see "Re-measured 11 October 2026" below for why.

Finer physics-grounded signals (isc-clamp, comms-timeout, isc-raise at full resolution) were
tested and gave **no gain** over the coarse categories — the ceiling for count-based features on
54 maintenance events. Further accuracy needs more events or higher-resolution sensor values.

## Re-measured 11 October 2026 (on `petbms-redesign` e3ea158) — read this before Rungs 2-4
Same walk-forward backtest (345 predictions, 52 events; optimistic, the models have seen the
whole history), fault history restored, models signed. The figures in Rungs 3 and 4 above do
not hold on the current code:

| Configuration | False alarms | Strict (<=30d) | Loose | Level acc. | Events missed |
|---|---|---|---|---|---|
| Current trainer, no humact | 51% | 81% | 94% | 59% | 3 (incl. BL2 2026-04-20) |
| **Current trainer + humact, all components (adopted)** | 53% | 85% | 96% | 57% | 2 |
| Same, humact excluded for BL2 | 51% | 85% | 94% | 58% | 3 (loses BL2 2026-04-20) |
| Label window 10/21 + humact except BL2 | 35% | 78% | 96% | 70% | 2 |

- **The false-alarm cut in Rungs 3-4 (41% -> 36%) came from the prototype's 10/21 label
  window**, not from event-grouped CV or the features. Production has used 20/35 since
  2026-07-03. Event-grouped CV gives results identical to the current trainer and was not
  promoted.
- **10/21 halves false alarms but four events lose their <=30-day warning** (BL1 Target 1
  2025-05-07 and 2025-12-29, FOILS 2025-11-23, ION SOURCE 2025-05-09: last alarm 36-55 days
  ahead instead of 3-18). Decision 2026-10-11 (Theo): keep 20/35.
- **Excluding BL2 from humact is only right at 10/21.** At 20/35 it loses a real BL2 event, so
  no component is excluded.
- **Never deploy humact-trained models where `daily_category` is not being kept current.**
  Measured with the previous code (missing table read as zero activity): detection fell from
  96% to 87% and seven events were missed, because a falsely quiet machine let the override
  stand alarms down. With unknown reported as NaN, detection stays at 96% (strict 78%).

**Where training happens.** The cloud API never runs the predictor; predictions are made on
the facility PC. So Rung 2's "upload to the Render disk" does nothing. To adopt this: on the
facility PC, with `MODEL_HMAC_KEY` set, run `scripts/restore_events_for_training.py`, then
train against the training DB (the `python -c` line in Rung 2, with that DB path) and restart
the monitor. Unsigned models are refused.

## What was deliberately left out
Cycle-constant recalibration (Ion 46→59, BL2 56→73): evaluated, improves MAE but carries its
own ~2pp detection tradeoff and the override already does the work. Excluded to avoid trading
detection on a safety system.
