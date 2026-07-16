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

To make it work live you must **populate `daily_category`** — run
`python scripts/aggregate_daily_categories.py --db <db>` alongside the beam_daily aggregation
(inference needs only the last ~14 days, within retention; verified to reproduce the training
counts exactly). Training uses the full restored history. Then retrain (Rung 2/3) — the models
must be retrained with the features present to use them.

Finer physics-grounded signals (isc-clamp, comms-timeout, isc-raise at full resolution) were
tested and gave **no gain** over the coarse categories — the ceiling for count-based features on
54 maintenance events. Further accuracy needs more events or higher-resolution sensor values.

## What was deliberately left out
Cycle-constant recalibration (Ion 46→59, BL2 56→73): evaluated, improves MAE but carries its
own ~2pp detection tradeoff and the override already does the work. Excluded to avoid trading
detection on a safety system.
