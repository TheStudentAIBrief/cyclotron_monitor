# Cyclotron Maintenance Prediction Monitor
## Product Overview

**Machine:** GE HealthCare Eclipse RDS-111 (firmware View-5.1_2025-p.5)
**Document date:** June 2026
**Version:** 1.0

---

## 1. What Is This System?

Medical cyclotrons contain several components that wear out over time — and they do so unpredictably. An ion source that should last sixty days may fail after sixteen. A target that was replaced three months ago may need attention again in two weeks. When a component fails without warning during a production run, the consequences compound quickly: the radiopharmaceutical batch in progress may be lost, patient scans must be rescheduled, and emergency maintenance is far more disruptive than a planned replacement.

The **Cyclotron Maintenance Prediction Monitor** is a software system that analyses the cyclotron's own operational log files to predict, up to seven days in advance, when each major component will next require maintenance. It runs on a standard Windows PC, requires no additional sensors or hardware of any kind, and presents its predictions as a simple colour-coded dashboard that any member of staff can read and act on — not just engineers.

| | |
|---|---|
| **Machine** | GE HealthCare Eclipse RDS-111 |
| **Components monitored** | Ion source · Foils (×6) · BL1 Target 1 · BL2 Target 1 · Compressed air valve |
| **Prediction horizon** | Up to 7 days in advance |
| **Data behind the system** | 4 years of operational logs (2022–2026) · 80 confirmed maintenance events |
| **Validation** | Cross-checked against physical maintenance records — 4/4 confirmed |
| **Platform** | Windows PC · Python · no internet or cloud services required |

---

## 2. How It Makes Predictions

The system uses two independent signals and combines them conservatively into a single prediction.

### Signal 1 — Lifetime Counter Projection

The Eclipse cyclotron's control software already tracks component wear using a built-in microamp-hour (µAh) counter. Each component accumulates charge during beam production, and when that counter approaches its limit (9,999 µAh), the machine logs a warning. The monitor reads these counter values from the log files, calculates how quickly charge is accumulating day by day, and projects how many days of remaining capacity each component has before reaching its service threshold.

This signal is straightforward and reliable once the counter is actively warning — but it activates only when the component is already close to its limit. It cannot capture early degradation that shows up in beam behaviour weeks before any threshold is crossed.

### Signal 2 — Pattern Recognition

The second signal is a machine learning model — one trained individually for each major component — that watches how beam parameters evolve over time. Every day, the monitor computes rolling statistics across 7-day, 14-day, and 30-day windows for the parameters most relevant to each component: ion source current and voltage, foil and collector currents, fault code frequency, and beam efficiency ratios, among others. It then compares these current patterns against what the historical data looked like in the weeks preceding past maintenance events.

The model was trained on four years of real operational logs from this machine, with 80 confirmed maintenance events as its labelled examples. It learned, for instance, that ion source current tends to fall gradually in the two weeks before a rebuild is needed, and that self-check fault codes appear with increasing frequency as the ion source degrades. These are patterns a daily inspection might miss but that become clearly visible in the statistical trend.

The model produces a probability score between 0 and 1, calibrated against the historical data and converted to a days-remaining estimate.

### Combining the Two Signals

For each component, both signals are computed independently:

- **Counter risk** — derived from the µAh projection
- **Pattern risk** — derived from the ML model's probability score

The final alert level uses whichever signal is more urgent. The days estimate uses whichever is lower. This "most pessimistic wins" design means neither signal can suppress the other: if the counter projects 20 days of life remaining but the pattern model is already seeing degradation consistent with imminent failure, the alert is raised regardless. Conversely, if a component has recently been replaced and the counter is low, an alert fires even if the parameter trends look normal.

When there is insufficient beam data to run the pattern model (fewer than 7 days of data in the rolling window), the system falls back transparently to counter-only mode and notes this in the prediction output.

---

## 3. What It Monitors

### Ion Source

The ion source generates the proton beam and is the most frequently serviced component on the machine. Its service cycle is also the most variable — ranging from 16 days in a premature failure to over 88 days in a healthy extended run. This variability makes schedule-based maintenance planning unreliable, and early warning is correspondingly valuable.

**Parameters monitored:** ion source current and voltage, bias voltage, bias current, output beam current (BOP), ion source self-check fault rate (error codes 10802 "current below threshold", 10804 "appears open", 10808 "arc/ignition failure", 10809 "gas flow anomaly"), beam efficiency ratio (output beam current per unit ion source current).

**Training data:** 14 confirmed maintenance events | **Average service interval:** ~46 days (range: 16–75 days)

---

### Foils — BL1 and BL2 (all six)

The six stripping foils — three per beamline — are always replaced together. The monitor treats all six as a single component for prediction purposes.

**Parameters monitored:** BL1 and BL2 foil currents, BL1 and BL2 collector currents, beam tuning fault rate (error code 10401).

**Training data:** 7 confirmed maintenance events | **Average service interval:** ~78 days (range: 38–129 days)

---

### BL1 Target 1

The primary target on beamline 1, where the proton beam strikes the target material to produce radioisotopes for clinical use.

**Parameters monitored:** BL1 target current, BL1 foil current, output beam current, beam tuning fault rate.

**Training data:** 11 confirmed maintenance events | **Average service interval:** ~51 days (range: 11–102 days)

---

### BL2 Target 1

The primary target on beamline 2.

**Parameters monitored:** BL2 target current, BL2 foil current, output beam current, beam tuning fault rate.

**Training data:** 8 confirmed maintenance events | **Average service interval:** ~56 days

---

### Compressed Air Valve — BL2 TSU3 VALVE6

This valve is not tracked by any lifetime counter in the cyclotron's own software, so it cannot be predicted by the pattern model. Instead, the monitor counts how many times per day the valve opens and closes (toggle events). Under normal operation, this is 2–6 events per day. In the lead-up to the January 2026 valve failure — where liquid in the compressed air line caused rust and blockage — the toggle rate climbed to 22 events on the day before failure, then exceeded 103 on the day itself.

The monitor raises a **CAUTION** alert when the 7-day average exceeds 10 toggles per day, and a **WARNING** when it exceeds 30. This is an anomaly detector rather than a predictive model: it identifies a component that is actively struggling, not one that will fail weeks later.

---

## 4. The Dashboard

The dashboard is a single HTML file (`ui/index.html`) that opens in any standard web browser — Chrome, Edge, or Firefox — with a double-click. No server, no installation, no login is required.

The dashboard reads a data file (`data/dashboard.json`) that is rewritten automatically each time the cyclotron software produces a new log file, typically once per day. The dashboard itself refreshes in the browser every 60 seconds.

### Visual Layout

```
╔══════════════════════════════════════════════════════╗
║  CYCLOTRON HEALTH MONITOR          26 Jun 2026 08:00 ║
╠══════════════════════════════════════════════════════╣
║                                                      ║
║  ION SOURCE                    ⚠  PLAN MAINTENANCE  ║
║  [██████████████░░░░░░]  62%  ·  ~8 days remaining  ║
║  Last replaced: 22 May 2026                          ║
║  Why: Ion source current dropping 8% over 2 weeks   ║
║       Self-check fault 3× this week (avg 0.4×/week) ║
║                                                      ║
║  BL1 + BL2 FOILS               ✅  ALL GOOD         ║
║  [████████░░░░░░░░░░░░]  38%  ·  ~42 days remaining ║
║  Last replaced: 01 Apr 2026                          ║
║                                                      ║
║  BL1 TARGET 1                  🔴  REPLACE NOW      ║
║  [████████████████████] OVERDUE  ·  Act immediately  ║
║  Last replaced: 10 Apr 2026                          ║
║  Why: Lifetime counter at 100% — threshold reached  ║
║                                                      ║
║  BL2 TARGET 1                  ✅  ALL GOOD         ║
║  [████████████░░░░░░░░]  58%  ·  ~21 days remaining ║
║  Last replaced: 20 Apr 2026                          ║
╚══════════════════════════════════════════════════════╝
```

Each component row shows a progress bar that fills from left to right as the component ages toward its next expected service (0% = just replaced, 100% = due now), alongside the alert status, estimated days remaining, and date of last replacement.

### Alert Levels

| Status | Condition | Recommended action |
|---|---|---|
| ✅ **Green** — All good | More than 14 days remaining | No action required |
| 🟡 **Yellow** — Plan maintenance | 7–14 days remaining | Book parts and schedule a maintenance slot |
| 🟠 **Orange** — Schedule this week | 3–7 days remaining | Confirm parts availability, book within days |
| 🔴 **Red** — Replace now | 3 days or fewer / already overdue | Immediate attention required |

### Plain-English Explanations

The dashboard never shows raw numbers, model scores, error codes, or µAh values. Where an alert is raised (Yellow, Orange, or Red), the top reasons are translated into plain sentences, for example:

- *"Ion source current dropping 8% over the last 2 weeks"*
- *"Self-check fault occurred 3× this week (average: 0.4× per week)"*
- *"Lifetime counter at 87% — approaching service threshold"*

A maximum of three reasons are shown per component. Green components display no additional detail.

---

## 5. Validation: Does It Work?

The system's outputs have been cross-referenced against the facility's own physical maintenance record for 2026. All four logged maintenance events are accounted for:

| Physical record | Description | Evidence in logs | Outcome |
|---|---|---|---|
| 08 Jan 2026 | BL2 compressed air valve replaced | Hyper logs show 22 valve toggle events on Jan 7 (normal: 2–6 per day); 103+ on Jan 8 | ✅ Detected 1 day in advance |
| 16 Mar 2026 | Ion source rebuild | Log shows `setlifetime` reset on Mar 15 — 1-day offset consistent with rebuild-day logging, paperwork filed following morning | ✅ Confirmed (±1 day) |
| 15 May 2026 | Software update (View-5.1_2025-p.5) | MI_* log format begins on exact date; QEI RF error spike consistent with post-update controller communications | ✅ Confirmed in logs |
| 22 May 2026 | Ion source rebuild ("possible breakdown") | Log shows `setlifetime` reset on May 22 — exact match | ✅ Exact match |

In addition to these four cross-validated events, the pattern recognition models were trained on 80 maintenance events spanning four years of data. Training was conducted using time-series cross-validation — five chronological folds, where the model is always tested on data it has never seen and that occurred after its training period. This approach ensures the validation results reflect real predictive performance, not retrospective pattern-fitting.

### Known Limitations

**Compressed air valve — single event:** The alert thresholds for the valve (>10 toggles/day for CAUTION, >30 for WARNING) were calibrated from a single failure event. They represent informed engineering estimates rather than statistically tuned thresholds. A second or third failure event would allow these thresholds to be refined.

**Short ion source cycles:** Premature ion source failures — those occurring after 16–24 days rather than the typical 40–60 — are the highest-value cases to predict and the hardest. The model has been trained on examples of these, but they remain the most challenging given their brevity and the small number in the dataset.

**Post-software-update period:** Following the May 2026 software update, the log format changed and a temporary spike in unrelated error codes occurred. The model includes a feature to account for this transition, but predictions in the weeks immediately following any future major software update should be interpreted with additional caution until the new format has accumulated sufficient operational data.

**Minor components:** BL1 Targets 2/3/4, BL2 Targets 2/3, and Diffusion Pump 2 each have only one or two historical maintenance events — too few to train a reliable ML model. The dashboard shows these components with statistical projections based on average cycle length, clearly distinguished from the ML-based predictions.

---

## 6. System Requirements and Deployment

### Requirements

| | |
|---|---|
| **Operating system** | Windows (any modern version) |
| **Runtime** | Python 3.14 |
| **Libraries** | pandas · scikit-learn · watchdog · matplotlib (all free, open-source) |
| **Data access** | Read-only access to the cyclotron log directory (`cyclotron_data\raw\`) — local or network share |
| **Hardware** | Any standard Windows PC; no GPU or specialist hardware required |
| **Connectivity** | Fully offline — no internet connection, no cloud services |

### Deployment

The system is operated with three commands:

**1. Initial setup (first run only)**
```
python main.py train
```
Ingests all historical log files, trains and saves the four ML models. This takes several minutes on first run. All four models are saved as files on disk and reloaded automatically on subsequent runs.

**2. Ongoing monitoring (normal operating mode)**
```
python main.py monitor
```
Starts watching the log directory. Whenever the cyclotron software writes a new log file (typically once per day), the monitor automatically ingests it, updates predictions, and rewrites the dashboard. This process runs quietly in the background and requires no further interaction.

**3. View the dashboard**
Open `ui/index.html` in any browser. The page refreshes every 60 seconds automatically.

Additionally, whenever any component reaches **Red** status, the system writes an `ALERT.txt` file to the log directory. This plain-text file can be picked up by any existing notification or alerting workflow — email alert, paging system, or scheduled check.

### Retraining

The `python main.py train` command should be re-run approximately monthly, or after any cluster of new maintenance events accumulates. This keeps the models current as the machine's operating patterns evolve over time. Retraining is non-destructive: it overwrites only the model files, and the dashboard continues to operate from the previous models until the new training completes.

---

*Document generated from cyclotron_monitor v1.0 · Data through 2026-06-23 · 1,317 log files · 80 maintenance events*
