# Model Governance — Cyclotron Predictor

Closes the "no ownership / no accountability / no retraining triggers" gaps from the
IAIS questions-catalogue audit (2026-07-28). Scope: the predictive-maintenance model
stack in this repo, not the whole platform.

## 1. Accountability (who owns what)

| Artifact | Owner | Backup / reviewer |
|---|---|---|
| ML models (`data/models/*.pkl`), training pipeline (`models/trainer.py`) | Theo (IAIS) | — |
| Feature engineering (`features/engineer.py`) | Theo | — |
| Counter / blend / override logic (`models/counter.py`, `blend.py`, `predictor.py`) | Theo | — |
| Backtest & accuracy claims (`backtest.py`, `docs/accuracy-improvements.md`) | Theo | — |
| Gauge OCR + mobile UX (`mobile/`, gauge-photo path) | Cofounder (Will, wm37432) | Theo |
| Deployment (Render), secrets, model signing (`MODEL_HMAC_KEY`) | Theo | — |
| OT-data custody (PetLabs Pretoria, NNR installation) | PetLabs site owner | Theo |

RACI is deliberately flat — two-person team. "Owner" = accountable for changes and for
signing off a retrain/deploy. Update this table before adding contributors.

## 2. What the model is (and is not) approved to do

- **Approved:** advisory maintenance-timing + risk chips (RED/ORANGE/YELLOW/GREEN) per
  component, shown to trained operators. Decision support only.
- **NOT approved:** autonomous action, interlock control, or any write-back to the
  cyclotron control system. The predictor is read-only w.r.t. OT.
- The gated override (`_apply_model_override`) may only ever **downgrade** a calendar
  alarm when the model is confidently healthy; it can never suppress a real detection
  (safety contract enforced by `tests/test_property_safety.py`).

## 3. Retraining triggers (measurable)

Retraining is a manual runbook (`docs/accuracy-improvements.md` Rung 2). Execute it when
ANY trigger fires — record which one in the commit:

| Trigger | Threshold | Rationale |
|---|---|---|
| New maintenance events | ≥3 new service events for any component since last train | Labels are the binding constraint; each event materially moves a 6–14-event model |
| Calendar drift | Component's dynamic avg-cycle shifts >15% vs the trained value | Usage regime changed; counter baseline stale |
| Feature-source gap | `daily_category` or `beam_daily` ingestion stalls >30 days | Inference runs on stale/NaN features |
| Backtest regression | Re-run backtest shows loose-detection <90% or FA >45% | Below the shipped PR#5 envelope |
| Software regime change | New `SOFTWARE_UPDATE_DATE` boundary crossed | Sensor semantics shift pre/post firmware |

**Note:** timing accuracy is at its data-limited ceiling (~40–54 total events; 10 methods
tried, incl. Bayesian network/partial-pooling 2026-07-28 — no gain). Retraining maintains
the model against drift; it does not raise the accuracy ceiling. The only levers that do:
more maintenance history, or the PETrace service log.

## 4. Validity caveats (offline vs online)

- Backtest metrics (`backtest.py`) are **offline point estimates** on ~40–54 events;
  treat them as directional, not guaranteed online performance.
- **The cloud (Render) does not run the predictor** — it serves the last dashboard POSTed
  by an on-prem `monitor/watcher.py`. There is currently no live on-prem bridge, so online
  task-success is **not measured**. Any "live" claim must name where `watcher.py` ran.
- Model precision is genuinely weak (event-grouped-CV precision 0.03–0.44); most alert
  quality comes from the counter/blend/override, not classifier skill.

## 5. Incident / rollback

- Models are integrity-checked on load (SHA-256, HMAC when `MODEL_HMAC_KEY` set). A failed
  check refuses to load rather than run a tampered model.
- Rollback = restore the previous `data/models/*.pkl` + `.sha256` sidecar and restart the
  on-prem watcher. Cloud reflects it on the next dashboard sync.
- Never commit `data/cyclotron.db`, `data/models/*.pkl`, `data/.credentials.json`,
  `data/tls/*`, `config.json`.
