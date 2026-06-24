# Cyclotron Maintenance Prediction Monitor — Design Spec
**Date:** 2026-06-24  
**System:** Siemens Eclipse RDS-111 cyclotron (View-5.1_2025-p.5)  
**Data source:** `C:\Users\theol\cyclotron_data\raw\` — 1,317 log files, 2022-03-21 to 2026-06-23  

---

## 1. Goals

**Primary:** Predict maintenance needs for each major cyclotron component 7 days in advance, displayed as a visual progress-bar dashboard understandable to non-technical staff.

**Secondary:** Discover patterns in beam parameter data that precede or correlate with maintenance events — output as a standalone HTML report.

**Non-goals:** Real-time beam optimisation, production yield prediction, remote machine control.

---

## 2. Confirmed Maintenance Events (Ground Truth)

80 maintenance resets extracted from `setlifetime {*:0}` commands in hyper/MI logs. These are the training labels.

### Ion Source (`isc_amphrs`) — 14 events
| Date | Days since previous |
|---|---|
| 2024-04-12 | — (first in dataset) |
| 2024-11-17 | 219 (data gap — exclude from cycle analysis) |
| 2024-12-23 | 36 |
| 2025-02-20 | 59 |
| 2025-03-12 | 20 |
| 2025-04-05 | 24 |
| 2025-05-09 | 34 |
| 2025-07-12 | 64 |
| 2025-07-28 | 16 |
| 2025-09-26 | 60 |
| 2025-11-23 | 58 |
| 2025-12-29 | 36 |
| 2026-03-15 | 75 |
| 2026-05-22 | 68 |

Average cycle (excluding 219-day outlier): **~46 days**. Short cycles (16, 20, 24 days) represent premature failures — exactly what the ML model must catch.

### All 6 Foils (BL1+BL2 Foils 1/2/3) — 6–7 events each
All 6 foils are always replaced on the same day. Treated as one component "FOILS" for prediction.

| Date | Days since previous |
|---|---|
| 2024-12-18 | — |
| 2025-02-20 | 64 (partial — only foil 2) |
| 2025-04-05 | 44 |
| 2025-07-18 | 104 |
| 2025-10-16 | 90 |
| 2025-11-23 | 38 |
| 2026-04-01 | 129 |

Average cycle: **~78 days**

### BL1 Target 1 — 11 events
| Date | Days since previous |
|---|---|
| 2024-11-17 | — |
| 2024-12-30 | 43 |
| 2025-02-09 | 41 |
| 2025-02-20 | 11 |
| 2025-03-16 | 24 |
| 2025-05-07 | 52 |
| 2025-07-29 | 83 |
| 2025-08-27 | 29 |
| 2025-11-23 | 88 |
| 2025-12-29 | 36 |
| 2026-04-10 | 102 |

Average cycle: **~51 days**

### BL2 Target 1 — 8 events
Dates: 2024-11-26, 2024-12-01, 2025-02-06, 2025-03-26, 2025-07-07, 2025-08-04, 2025-12-29, 2026-04-20. Average cycle: ~56 days.

### Minor components
- BL1 Targets 2/3/4, BL2 Targets 2/3: 1–2 events each — statistical projection only, no ML model.
- Diffusion Pump 2: 1 event (2024-11-06) — statistical only.

### Compressed Air Valve (BL2 TSU3 VALVE6) — 1 confirmed event
- 2026-01-08: "BL2 not unloading — compressed air valve replaced. Liquid in compressed air line caused rust."
- Not tracked by lifetime counter. Detected via IO channel toggle rate in hyper logs.
- Normal: 2–6 `DO_BL2_TSU3_VALVE6` toggle events/day.
- Jan 7 (day before failure): 22 toggle events — early warning detectable 1 day ahead.
- Jan 8 (failure day): 103+ toggle events with rapid ON→OFF cycling in <10-second intervals.
- Detection method: anomaly-only (no ML model due to 1 event). Dashboard shows CAUTION if >10 toggles/day, WARNING if >30.

---

### Real-World Maintenance Record Validation (2026)

Cross-reference of extracted log dates against physical maintenance record provided:

| Physical record (DD/MM/YYYY) | Description | Log extraction | Match |
|---|---|---|---|
| 08/01/2026 | BL2 compressed air valve replaced | Jan 8 hyper shows 103+ rapid VALVE6 toggles; Jan 7 shows early chattering (22 events) | ✅ Confirmed in logs |
| 16/03/2026 | Ion source rebuild | Log shows setlifetime reset 2026-03-15 (1-day offset — timezone/paperwork timing) | ✅ Confirmed (±1 day) |
| 15/05/2026 | Software update | MI_* log format begins on this exact date; QEI RF errors spike post-update | ✅ Confirmed in logs |
| 22/05/2026 | Ion source rebuild ("possible breakdown") | Log shows setlifetime reset 2026-05-22 (exact match) | ✅ Exact match |

**Key insight from validation:** The 1-day offset on the March ion source is consistent — `setlifetime` is reset during the rebuild (Mar 15 evening/night), and the maintenance record is written up the next working day (Mar 16). Label matching should allow ±1 day tolerance.

**April events** (foil/target replacements on 2026-04-01, 2026-04-10, 2026-04-20) do not appear in the provided maintenance record — consistent with these being routine consumable changes not captured in the high-level breakdown log.

---

## 3. Project Structure

```
C:\Users\theol\cyclotron_monitor\
├── parsers\
│   ├── beam_parser.py        # beam_ CSV -> daily stats DataFrame
│   ├── hyper_parser.py       # hyper_/MI_ logs -> events DataFrame
│   └── maintenance_labels.py # setlifetime resets -> maintenance table
├── features\
│   └── engineer.py           # rolling stats, slopes, fault rates, ratios
├── models\
│   ├── trainer.py            # train per-component GBM + calibration
│   ├── predictor.py          # load model, compute risk score + days estimate
│   └── counter.py            # µAh-based lifetime projection
├── monitor\
│   ├── watcher.py            # watchdog on log directory
│   └── dashboard_writer.py   # write dashboard.json + ALERT.txt
├── ui\
│   ├── index.html            # visual progress bar dashboard
│   └── patterns.html         # pattern discovery report (generated)
├── data\
│   ├── cyclotron.db          # SQLite feature store
│   └── models\               # saved .pkl model files
├── tests\
│   ├── fixtures\             # sample log snippets for unit tests
│   ├── test_parsers.py
│   ├── test_features.py
│   ├── test_predictor.py
│   └── test_monitor.py
├── requirements.txt
└── main.py                   # CLI: train | monitor | predict | patterns
```

All raw log data stays in `C:\Users\theol\cyclotron_data\raw\` — the monitor project only reads from there, never writes.

---

## 4. Data Schemas

### 4.1 beam_parser.py

**Input:** `beam_YYMMDD.log` or `MI_*_beam_YYMMDD.log`

**Format handling:**
- Skip lines 1–4 (header block ending with the column-name line)
- Column-name line contains `DATE,TIME,AI_TANK_HI_PRES,...`
- Data rows: `MM/DD/YYYY,HH:MM:SS.s,v1,v2,...` OR `,HH:MM:SS.s,v1,v2,...` (date carries forward when blank)
- All 22 numeric columns parsed as float64; errors → NaN

**Output:** `parse_beam_file(path: str) -> pd.DataFrame`
```
columns: timestamp (datetime64), AI_TANK_HI_PRES, AI_ISGAS_FLOW,
         SW_RF_FREQ, AO_RF_AMPL, AI_DEE_VOLT, AI_RFFWD_PWR,
         AI_RFREF_PWR, AI_MMA_CUR, AI_MMT_CUR, AO_MMO_CUR,
         AI_IS_CUR, AI_IS_VOLT, AI_BIAS_VOLT, AI_BIAS_CUR,
         AI_BL1_FOIL_CUR, AI_BL1_TARG_CUR, AI_BL1_COL_CUR,
         AI_BL2_FOIL_CUR, AI_BL2_TARG_CUR, AI_BL2_COL_CUR, AI_BOP_CUR
```

**Daily aggregation:** `aggregate_daily(df: pd.DataFrame) -> pd.DataFrame`
For each numeric column: `{col}_mean`, `{col}_std`, `{col}_min`, `{col}_max`, `{col}_p10`, `{col}_p90`
Index: `date` (date object). Rows with >50% NaN in any parameter are flagged with `data_quality: 'sparse'`.

### 4.2 hyper_parser.py

**Input:** `hyper_YYMMDD.log` or `MI_*_hyper_YYMMDD.log` or `MI_*_ui_YYMMDD.log`

**Output: events table** — `parse_hyper_file(path: str) -> pd.DataFrame`
```
columns: timestamp (datetime64), severity (str: debug/info/verbose/
         warning/error/note), code (str or None, e.g. "10802"),
         function (str, e.g. "periodicCheckISC"), message (str),
         source_file (str)
```

**Lifetime warning extraction:** `extract_lifetime_warnings(df: pd.DataFrame) -> pd.DataFrame`
Filters rows where `code == "11001"`. Parses counter value from message:
```
columns: timestamp, component (str, e.g. "BL1 foil 2"), 
         counter_uah (float), threshold_uah (float)
```

**Valve toggle extraction:** `extract_valve_toggles(df: pd.DataFrame, channel: str) -> pd.DataFrame`
Filters `archSync: IO Channel {channel} set to ON|OFF` messages and counts state-change events per day.
```
columns: date (date), channel (str), toggle_count (int)
```
Called with `channel="DO_BL2_TSU3_VALVE6"` for compressed air valve anomaly detection.

### 4.3 maintenance_labels.py

**Output:** `extract_maintenance_events(log_dir: str) -> pd.DataFrame`
```
columns: timestamp (datetime64), component_key (str, e.g. "isc_amphrs"),
         component_label (str, e.g. "ION SOURCE"), source_file (str)
```
Deduplication: one event per (date, component_key) — `cmdProc` lines excluded, only `cmdAddToQueue` counted.

---

## 5. Feature Engineering

**`engineer.py`** — `build_features(date: date, component: str, db_path: str) -> dict`

Input: a target date and component name. Reads from SQLite `cyclotron.db`.
Output: flat dict of features for that component on that date.

### 5.1 Per-component beam parameter sets

| Component | Parameters monitored |
|---|---|
| ION SOURCE | `AI_IS_CUR`, `AI_IS_VOLT`, `AI_BIAS_VOLT`, `AI_BIAS_CUR`, `AI_BOP_CUR` |
| FOILS | `AI_BL1_FOIL_CUR`, `AI_BL2_FOIL_CUR`, `AI_BL1_COL_CUR`, `AI_BL2_COL_CUR` |
| BL1 TARGET 1 | `AI_BL1_TARG_CUR`, `AI_BL1_FOIL_CUR`, `AI_BOP_CUR` |
| BL2 TARGET 1 | `AI_BL2_TARG_CUR`, `AI_BL2_FOIL_CUR`, `AI_BOP_CUR` |

### 5.2 Rolling statistics (for each monitored parameter)
Computed at 3 window sizes: **7, 14, 30 days** prior to the target date.

For each window:
- `{param}_{w}d_mean` — rolling mean of daily means
- `{param}_{w}d_std` — rolling std of daily means (stability measure)
- `{param}_{w}d_slope` — linear regression slope of daily means over window (µ/day)

### 5.3 Fault rate features
From hyper logs, daily count of each relevant fault code. Rolled at 7 and 14 days.
**Group by subsystem** — using a global `total_errors` count is unreliable because different subsystems produce different error volumes (e.g., QEI RF controller errors spiked after the May 15 software update, unrelated to ion source status).

**Ion source subsystem (IS-specific codes only):**
- `fault_is_10802_7d` — `periodicCheckISC` failures (IS current below threshold)
- `fault_is_10804_7d` — `checkISwarnings: Ion Source appears to be open`
- `fault_is_10808_7d` — IS arc/ignition failures
- `fault_is_10809_7d` — IS gas flow anomalies

**Beamline/tuning subsystem:**
- `fault_bl_10401_7d` — beam tuning failures (foil/alignment)
- `fault_bl_10f01_7d` — extractor foil position errors

**Lifetime overrun:**
- `fault_11001_14d` — lifetime counter overrun warnings (code 11001) — fired hourly once counter >9999

**Why no `total_errors` feature:** validated against May 18 2026 logs — 10,617 "errors" that day were all QEI RF controller comms failures (`qeiSend32bitValue`, `qeiGenUnlock`), unrelated to the ion source that failed 4 days later. A global count would generate false positives.

### 5.4 Lifetime counter features
- `counter_current_uah` — most recent counter reading from warning 11001. If no warning 11001 has fired yet (counter below 9999 threshold), this is `NaN` and the counter-based signal falls back to the historical average rate: `counter_days_remaining = avg_cycle_days - days_since_last_maintenance`.
- `counter_daily_rate_uah` — µAh/day accumulation rate. Computed as slope of counter values from warning 11001 messages over the last 14 days. If fewer than 2 warning 11001 readings exist, falls back to `9999 / avg_cycle_days` (historical average rate).
- `counter_days_remaining` — `(9999 - counter_current_uah) / counter_daily_rate_uah` when counter data exists; `avg_cycle_days - days_since_last_maintenance` otherwise. Negative values mean overdue.
- `days_since_last_maintenance` — calendar days since last setlifetime reset for this component

### 5.5 Efficiency ratios (ion source only)
- `efficiency_ratio` — `AI_BOP_CUR_mean / AI_IS_CUR_mean` (how much beam current per ion source amp)
- `efficiency_slope_14d` — slope of efficiency ratio over 14 days (negative slope = degrading)

### 5.6 Compressed air valve anomaly feature
Extracted from hyper logs, not from beam data. Applied to BL2 valve health monitoring only.
- `valve_bl2_tsu3_toggles_7d` — count of `DO_BL2_TSU3_VALVE6` ON+OFF events over 7 days
- Baseline (normal): 2–6 events/day, 14–42/week
- CAUTION threshold: >70 toggles/7d (>10/day average)
- WARNING threshold: >210 toggles/7d (>30/day average)

These thresholds reflect the observed failure pattern: Jan 7 (day-before-failure) showed 22 events vs. normal 2–6. The valve toggle count more than triples before failure.

### 5.7 Software version boundary feature
- `post_v51_software` — binary 0/1. Set to 1 for all dates on or after 2026-05-15 (MI_* log format begins).
- Included as a feature to let the model distinguish potential distributional shift in log verbosity. This date also marks an "efficacy not yet established" note in the maintenance record — model should not be trusted for predictions immediately after a software update without re-validation.

### 5.8 Feature completeness
If fewer than 7 days of beam data exist in a window, that window's features are set to `NaN`. The predictor falls back to counter-only mode when NaN features exceed 30% of the feature vector.

---

## 6. SQLite Feature Store (`cyclotron.db`)

Four tables:

```sql
CREATE TABLE beam_daily (
    date TEXT NOT NULL,
    param TEXT NOT NULL,
    mean REAL, std REAL, min REAL, max REAL, p10 REAL, p90 REAL,
    data_quality TEXT DEFAULT 'ok',
    PRIMARY KEY (date, param)
);

CREATE TABLE events (
    timestamp TEXT NOT NULL,
    severity TEXT,
    code TEXT,
    function TEXT,
    message TEXT,
    source_file TEXT,
    UNIQUE(timestamp, source_file, code, function)  -- prevents duplicates on re-process
);

CREATE TABLE maintenance_events (
    timestamp TEXT NOT NULL,
    component_key TEXT NOT NULL,
    component_label TEXT NOT NULL,
    source_file TEXT,
    PRIMARY KEY (timestamp, component_key)
);

CREATE TABLE predictions (
    run_at TEXT NOT NULL,
    component TEXT NOT NULL,
    risk_score REAL,
    days_estimate REAL,
    alert_level TEXT,
    primary_signal TEXT,
    top_features TEXT,  -- JSON: [{name, value, importance}]
    PRIMARY KEY (run_at, component)
);
```

---

## 7. Models

### 7.1 One model per component
Four trained models saved to `data/models/`:
- `ion_source_model.pkl`
- `foils_model.pkl`
- `bl1_target1_model.pkl`
- `bl2_target1_model.pkl`

Each `.pkl` contains a `CalibratedClassifierCV` wrapping a `sklearn.pipeline.Pipeline` with:
1. `SimpleImputer(strategy='median')` — handles missing features
2. `GradientBoostingClassifier(n_estimators=100, max_depth=3, learning_rate=0.1, min_samples_leaf=3)`

Class imbalance is handled by passing `sample_weight=compute_sample_weight('balanced', y_train)` to `pipeline.fit()` — **not** via `class_weight` (which `GradientBoostingClassifier` does not support). `CalibratedClassifierCV(pipeline, cv=3, method='isotonic')` wraps the fitted pipeline to convert raw scores to reliable probabilities.

### 7.2 Label construction (`trainer.py`)

For each date `d` in the training set:
- **Positive (label=1):** `d` is within 7 days before a maintenance event for this component
- **Negative (label=0):** `d` is more than 14 days before a maintenance event
- **Excluded:** days 8–14 before a maintenance event (ambiguous zone)

**±1 day tolerance:** The log reset timestamp (`setlifetime {*:0}`) may precede the physical maintenance record by 1 day — work is done and reset logged during the rebuild, but the paperwork is filed the following day. This is consistent with the validated 2026 records (March reset = Mar 15 log, Mar 16 paperwork). Labels are applied with the reset timestamp as authoritative; the ±1 day offset is informational and does not affect label construction.

**"Possible" vs confirmed replacements:** Some maintenance events are precautionary (scheduled replacement near the end of a cycle) and some are emergency (ion source fails mid-production run, short cycle). Both appear identically as `setlifetime` resets. The ML model treats all resets as equivalent labels — the distinction matters for human context but not for the prediction task (predict *any* maintenance need).

### 7.3 Cross-validation

`TimeSeriesSplit(n_splits=5)` — folds respect chronological order.

Metrics reported per fold and overall:
- Precision at label=1 (what fraction of alerts were real?)
- Recall at label=1 (what fraction of actual maintenance events were caught?)
- Average lead time: mean days-before-maintenance when first predicted

**Minimum quality gate:** overall precision ≥ 0.5 AND recall ≥ 0.6.  
If a component's model fails the gate, `predictor.py` uses counter-only mode for that component and logs a warning.

### 7.4 Retraining

`python main.py train` re-runs full training on all historical data. Should be re-run monthly or whenever new maintenance events accumulate.

---

## 8. Prediction (`predictor.py`)

### 8.1 `predict(component: str, features: dict, model_path: str, counter_days: float) -> PredictionResult`

```python
@dataclass
class PredictionResult:
    component: str          # "ION SOURCE"
    risk_score: float       # 0.0-1.0, ensemble probability
    days_estimate: float    # estimated days until maintenance needed
    alert_level: str        # "GREEN" | "YELLOW" | "ORANGE" | "RED"
    primary_signal: str     # "COUNTER" | "MODEL" | "BOTH" | "COUNTER_ONLY"
    top_reasons: list[str]  # plain-English explanations, max 3
    last_maintenance: str   # ISO date string
    counter_days: float     # raw counter projection (may be inf)
```

### 8.2 Ensemble logic

```
counter_risk = clamp((14 - counter_days) / 14, 0, 1)   # 1.0 when 0 days remain
model_risk   = gbm_calibrated_probability(features)      # 0.0-1.0
final_risk   = max(counter_risk, model_risk)             # most pessimistic signal wins
```

Days estimate:
```
counter_days (from µAh projection)

model_days: for each training example, record (predicted_probability, actual_days_before_maintenance).
Fit isotonic regression: days = f(probability). At prediction time, invert:
model_days = isotonic_inverse(model_risk). Saved alongside model as model_days_calibrator.pkl.

final_days = min(counter_days, model_days)
```

### 8.3 Alert thresholds

| Level | Condition | Dashboard colour | Action text |
|---|---|---|---|
| GREEN | final_days > 14 | #2ecc71 | "All good" |
| YELLOW | 7 < final_days ≤ 14 | #f39c12 | "Plan maintenance" |
| ORANGE | 3 < final_days ≤ 7 | #e67e22 | "Schedule this week" |
| RED | final_days ≤ 3 or overdue | #e74c3c | "Replace now" |

### 8.4 Plain-English reasons

The top 3 GBM feature importances for the prediction are translated to plain sentences:
- `AI_IS_CUR_14d_slope` negative → "Ion source current dropping (−X% over 2 weeks)"
- `fault_10802_7d` high → "Ion source self-check failing X× this week (avg Y×)"
- `counter_days_remaining` low → "Lifetime counter at X% — approaching limit"

Maximum 3 reasons. No ML jargon in the output.

---

## 9. Dashboard (`ui/index.html`)

Self-contained single HTML file. Reads `data/dashboard.json` via `fetch()`. Auto-refreshes every 60 seconds.

### 9.1 `dashboard.json` schema

```json
{
  "generated_at": "2026-06-24T14:30:00",
  "components": [
    {
      "name": "ION SOURCE",
      "risk_score": 0.72,
      "days_estimate": 8.3,
      "alert_level": "YELLOW",
      "pct_life_used": 62,
      "last_maintenance": "2026-05-22",
      "top_reasons": [
        "Ion source current dropping 8% over the last 2 weeks",
        "Self-check fault occurred 3× this week (avg: 0.4×/week)"
      ],
      "counter_days": 12.1,
      "primary_signal": "BOTH"
    }
  ]
}
```

### 9.2 Visual layout

```
╔══════════════════════════════════════════════════════╗
║  CYCLOTRON HEALTH MONITOR          24 Jun 2026 14:30 ║
╠══════════════════════════════════════════════════════╣
║                                                      ║
║  ION SOURCE                    ⚠ PLAN MAINTENANCE   ║
║  [██████████████░░░░░░]  62%  •  ~8 days remaining  ║
║  Last replaced: 22 May 2026                          ║
║  Why: Ion source current dropping 8% over 2 weeks   ║
║                                                      ║
║  BL1 + BL2 FOILS               ✅ ALL GOOD          ║
║  [████████░░░░░░░░░░░░]  38%  •  ~42 days remaining ║
║  Last replaced: 01 Apr 2026                          ║
║                                                      ║
║  BL1 TARGET 1                  🔴 REPLACE NOW       ║
║  [████████████████████]  OVERDUE  •  Act now        ║
║  Last replaced: 10 Apr 2026                          ║
║                                                      ║
║  BL2 TARGET 1                  ✅ ALL GOOD          ║
║  [████████████░░░░░░░░]  58%  •  ~21 days remaining ║
║  Last replaced: 20 Apr 2026                          ║
╚══════════════════════════════════════════════════════╝
```

Progress bar fills left-to-right as component ages (0% = just replaced, 100% = needs replacement).

`pct_life_used = (days_since_maintenance / avg_cycle_days) * 100`, capped at 100.

### 9.3 Accessibility / simplicity

- No jargon
- Emoji status icons: ✅ 🟡 🟠 🔴
- "Why" text only shown for YELLOW, ORANGE, RED
- Single file — open with double-click, no server needed
- Works in Chrome, Edge, Firefox

---

## 10. Continuous Monitor (`monitor/watcher.py`)

Uses Python `watchdog` library.

**Watched directory:** configurable via `config.json`, defaults to `C:\Users\theol\cyclotron_data\raw\`.

**On new `.log` file detected:**
1. Determine file type (beam_ or hyper_/MI_)
2. Parse incrementally (only new file, not re-parse history)
3. Insert daily stats into `cyclotron.db` (`beam_daily` table)
4. Insert events into `events` table
5. Check for new `setlifetime` resets → insert to `maintenance_events`
6. Call `predictor.predict_all()` → update `predictions` table
7. Write `data/dashboard.json`
8. If any component is RED → write `C:\Users\theol\cyclotron_data\ALERT.txt` (picked up by orchestrator)

**`ALERT.txt` format:**
```
CYCLOTRON ALERT - 2026-06-24 14:30
BL1 TARGET 1: REPLACE NOW (overdue by 3 days)
ION SOURCE: SCHEDULE THIS WEEK (~5 days remaining)
```

**CLI modes (`main.py`):**
- `python main.py train` — parse all historical logs, train all models, save to `data/models/`
- `python main.py predict` — one-shot prediction on latest data, print to console + write dashboard.json
- `python main.py monitor` — start watchdog, run continuously
- `python main.py patterns` — generate `ui/patterns.html` report

---

## 11. Pattern Discovery (`ui/patterns.html`)

Generated HTML report (not live-updating). Contains:

1. **Ion source lifetime distribution** — histogram of days between replacements, with mean/median/std annotated. Shows whether lifetime is consistent or variable.

2. **Pre-maintenance parameter drift** — For each component, plot the average of the 30 days leading up to each maintenance event, aligned to "day 0 = maintenance day". Overlays all events. Shows the typical degradation signature.

3. **Fault code frequency heatmap** — fault code occurrence by week (rows) and fault type (columns). Reveals which fault codes cluster before maintenance events.

4. **Beam parameter correlation matrix** — Pearson correlation of all 22 daily-mean parameters. Reveals which parameters move together.

5. **Feature importance** — GBM feature importances for each model, as a horizontal bar chart. "What the model watches most."

6. **Operating mode analysis** — K-means clustering (k=3) on beam parameters to identify distinct operating states (e.g., conditioning run vs. production run vs. low-power).

All plots rendered as inline SVG via matplotlib, embedded in the HTML file.

---

## 12. Testing Strategy (TDD)

Every function has a failing test written before implementation. Test fixtures (sample log snippets) stored in `tests/fixtures/`.

### Fixtures needed
- `tests/fixtures/beam_sample.log` — 50 rows of a beam_ CSV (mix of date-present and date-inherited rows)
- `tests/fixtures/hyper_sample.log` — 100 lines covering: NORMAL mode, errors, warnings, setlifetime, autoChangeFoil, warning 11001
- `tests/fixtures/hyper_maintenance.log` — log file containing a `setlifetime {"isc_amphrs":0}` reset
- `tests/fixtures/hyper_valve_chattering.log` — snippet with rapid DO_BL2_TSU3_VALVE6 ON/OFF cycling (mirrors Jan 8 failure pattern)

### Key tests
```python
# test_parsers.py
test_beam_parser_handles_date_inheritance()         # empty date col carries forward
test_beam_parser_returns_22_columns()
test_beam_parser_handles_malformed_rows_as_nan()
test_hyper_parser_extracts_error_codes()
test_hyper_parser_extracts_lifetime_warnings_with_counter_value()
test_maintenance_labels_finds_setlifetime_resets()
test_maintenance_labels_deduplicates_cmdproc_lines()
test_hyper_parser_counts_valve_toggles()  # DO_BL2_TSU3_VALVE6 ON/OFF count per day

# test_features.py
test_engineer_computes_rolling_slope_correctly()
test_engineer_returns_nan_when_fewer_than_7_days()
test_engineer_computes_efficiency_ratio()
test_engineer_computes_fault_rates()
test_engineer_computes_valve_toggle_rate()

# test_predictor.py
test_predictor_returns_red_when_counter_at_zero()
test_predictor_returns_green_when_14_plus_days()
test_predictor_uses_counter_only_when_no_model()
test_predictor_plain_english_reasons_contain_no_jargon()
test_predictor_risk_score_between_0_and_1()

# test_monitor.py
test_dashboard_json_schema_matches_spec()
test_alert_txt_written_on_red_component()
test_alert_txt_not_written_when_all_green()
test_watcher_detects_new_log_file()
```

---

## 13. Dependencies

```
# requirements.txt
pandas>=2.0
scikit-learn>=1.4
watchdog>=4.0
matplotlib>=3.8
```

Python 3.14 (already installed). PyTorch already installed but not used in this project.

Install: `pip install pandas scikit-learn watchdog matplotlib`

---

## 14. Data Notes and Caveats

1. **Log gaps:** The dataset has only 8 days of data from March 2022 and 8 days from April 2024, then continuous from October 2024. Training uses October 2024 onwards (complete coverage). Earlier data used only for long-term pattern plots.

2. **Short ion source cycles (16–24 days):** These represent premature failures and are the most valuable training examples for the ML model. They must not be excluded as outliers.

3. **Partial foil resets:** 2024-12-09 contains `setlifetime bl1_foil2_uamphrs: 90000` (not a zero-reset). This is a recalibration, not a replacement. Excluded from training labels.

4. **Counter values not always logged:** `warning 11001` only fires once the counter exceeds the threshold (9999 µAh). Counter values below threshold are not logged directly. `counter_days_remaining` is therefore an extrapolation and should be treated as an estimate.

5. **No 2022 maintenance events:** No `setlifetime` commands appear in 2022 data. This is consistent with the machine being newly installed or the command being added in later firmware (v5.0.4.f2.1 vs v5.1).

6. **Foils always replaced together:** When any one foil is replaced, all 6 are replaced simultaneously. The model treats all foils as one "FOILS" component.

7. **Software update boundary (2026-05-15):** A software update to View-5.1_2025-p.5 on this date changed the log format (MI_* prefix). It also caused a spike in QEI RF controller errors (`qeiSend32bitValue`, `qeiGenUnlock`) in the days immediately following the update — these are communication errors between the cyclotron controller and an RF module, unrelated to ion source health. The `post_v51_software` feature (Section 5.7) flags this boundary. Avoid reading the May 15 ion source prediction (if made) as confirmed until the software is validated — the maintenance record notes "Efficacy not yet established."

8. **Valve failure not in lifetime counter:** The January 8, 2026 compressed air valve failure (BL2 TSU3 VALVE6) is not tracked by any lifetime counter and cannot be predicted by the ML model. The anomaly detector (Section 5.6) catches it via valve toggle rate — but only 1 historical event exists, so no statistical threshold tuning is possible. Treat the CAUTION/WARNING thresholds (>10/day, >30/day) as engineering estimates from the single observed failure.

---

## 15. File Locations Summary

| Purpose | Path |
|---|---|
| Raw log data | `C:\Users\theol\cyclotron_data\raw\` |
| Project root | `C:\Users\theol\cyclotron_monitor\` |
| SQLite feature store | `C:\Users\theol\cyclotron_monitor\data\cyclotron.db` |
| Trained models | `C:\Users\theol\cyclotron_monitor\data\models\*.pkl` |
| Dashboard JSON | `C:\Users\theol\cyclotron_monitor\data\dashboard.json` |
| Visual dashboard | `C:\Users\theol\cyclotron_monitor\ui\index.html` |
| Pattern report | `C:\Users\theol\cyclotron_monitor\ui\patterns.html` |
| Alert file | `C:\Users\theol\cyclotron_data\ALERT.txt` |
| Config | `C:\Users\theol\cyclotron_monitor\config.json` |
