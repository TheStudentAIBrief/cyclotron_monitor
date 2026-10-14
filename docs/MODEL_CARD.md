# Model Card — Cyclotron Component Maintenance Predictor

Closes the "no model card / lineage / residual-risk documentation" audit gaps
(IAIS catalogue, 2026-07-28). One card covering the per-component model family.

## Overview
- **Task:** predict whether a cyclotron component needs maintenance within a 20-day
  window (binary), and estimate days-to-maintenance + a risk score, per component.
- **Components modelled:** ION SOURCE, FOILS (6 foils aggregated), BL1 Target 1,
  BL2 Target 1. Others fall back to counter-only.
- **Type:** per-component `GradientBoostingClassifier` (`models/trainer.py`), 50 trees,
  depth 2, `SelectKBest` feature selection, isotonic days-calibration. Blended with an
  amp-hour usage **counter** and a gated downgrade-only **override** (`models/predictor.py`).
- **Machine:** Siemens Eclipse RDS-111 (serial `MI_10150863_105`). PETrace 800 has no
  maintenance labels — telemetry only.

## Data & lineage
- **Labels:** `maintenance_events` table — ~40–54 distinct service dates total (6–14 per
  component). This is the binding constraint on accuracy.
- **Features:** rolling beam-parameter stats (`beam_daily`), fault-code counts (`events`),
  human-contact category counts (`daily_category`), PETrace batch telemetry, amp-hour
  counter phase. Full list: `features/engineer.py`.
- **Label rule:** positive if maintenance within 20 days; negative if >35 days; the 21–35
  gap is dropped as ambiguous (`models/trainer.py` POSITIVE_WINDOW/NEGATIVE_THRESHOLD).
- **Provenance:** Siemens data via USB export; PETrace via email batches. Data custody at
  PetLabs Pretoria (NNR-regulated). Raw DB never leaves the OT boundary in the repo.

## Evaluation
- **Protocol:** walk-forward / event-grouped-CV backtest (`backtest.py`) — no future
  leakage. Metrics: MAE, loose/strict detection, level accuracy, false-alarm rate.
- **Headline (re-measured 11 October 2026, `docs/accuracy-improvements.md`):** backtest of
  345 weekly predictions and 52 service events. False alarms 53%. Alert within 30 days
  before service: 85%. Any alert in the 90 days before: 96%. Two events had no alert.
  The models had seen this history, so real performance will be lower.
- **Honest limits:** event-grouped-CV precision 0.03–0.44 — classifier skill is weak.
  Timing prediction ties/loses to a do-nothing average-cycle baseline out-of-sample:
  the machine is **usage-scheduled**, so sensors carry little surprise-fault signal.
  10 modelling approaches (incl. Bayesian network + hierarchical partial-pooling,
  2026-07-28) converged on the same ~40–54-event wall.

## Intended use & limitations
- **Use:** advisory decision-support for trained operators. Read-only w.r.t. OT.
- **Do NOT:** treat as autonomous control, or trust timing beyond "≈ this component's
  usage cycle". The counter already captures most of the timing signal.
- **Out of scope:** PETrace maintenance timing (no labels); any component not in the
  modelled set (counter-only).

## Residual risks
- Small-sample overfit per component (mitigated by shallow trees + counter blend + the
  downgrade-only override contract).
- Stale features if ingestion stalls (see retraining triggers in `MODEL_GOVERNANCE.md`).
- Pickle model = code-execution surface; mitigated by load-time integrity check
  (`_verify_checksum`, HMAC when `MODEL_HMAC_KEY` set).
- Offline metrics ≠ measured online performance (cloud does not run the predictor).

## Alarm-override contract (tested)
When the model is confident a part is healthy it stands down the calendar alarm. In the
backtest this removed false alarms and left 2 of 52 services with no alert. Override only
ever downgrades a confidently-healthy calendar alarm, never a faulting component. Enforced
by property tests (`tests/test_property_safety.py`, Hypothesis-fuzzed).
