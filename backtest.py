"""
Walk-forward backtest: treat the historical dataset as a live feed.

At each weekly checkpoint, make predictions using only data available up to
that date, then score against what actually happened.

Data-leakage note
-----------------
The ML model was trained on the FULL dataset (all dates).  Predictions for
dates inside the training window are therefore OPTIMISTIC — the model has
already seen those patterns.  Counter-based predictions (TRANSFER LINES,
and counter override when counter_days < model_days) are leakage-free.
Results are labelled accordingly.
"""

import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from features.engineer import build_features
from models.counter import get_counter_days, FOILS_LABELS
from models.predictor import predict

# ── Constants ─────────────────────────────────────────────────────────────────
COMPONENTS = ['ION SOURCE', 'FOILS', 'BL1 Target 1', 'BL2 Target 1', 'TRANSFER LINES']

DB      = 'data/cyclotron.db'
MODELS  = 'data/models'

EVAL_WINDOW = 90   # days after a prediction to look for maintenance
MIN_HIST    = 30   # days of beam history required before first checkpoint


# ── Load master data once ─────────────────────────────────────────────────────
def _load_data():
    conn = sqlite3.connect(DB, timeout=30)
    beam_dates = [date.fromisoformat(r[0]) for r in
                  conn.execute("SELECT DISTINCT date FROM beam_daily ORDER BY date").fetchall()]
    maint = {}
    for comp in COMPONENTS:
        if comp == 'FOILS':
            ph = ','.join('?' * len(FOILS_LABELS))
            rows = conn.execute(
                f"SELECT DISTINCT date(timestamp) FROM maintenance_events "
                f"WHERE component_label IN ({ph}) ORDER BY timestamp",
                list(FOILS_LABELS)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT date(timestamp) FROM maintenance_events "
                "WHERE component_label=? ORDER BY timestamp", [comp]
            ).fetchall()
        maint[comp] = sorted({date.fromisoformat(r[0]) for r in rows})
    conn.close()
    return beam_dates, maint


# ── Counter projection as-of a specific date ─────────────────────────────────
def _counter_as_of(comp: str, as_of: date, maint: dict) -> float:
    """Delegate to production counter logic at a specific as-of date.

    Uses get_counter_days(as_of=...) so the backtest exercises exactly the same
    code path as the live system — including the elapsed-time µAh rate formula
    and the all-six-foils FOILS check.  maint is kept in the signature only for
    call-site compatibility; the DB is queried directly inside get_counter_days.
    """
    days, _ = get_counter_days(comp, DB, as_of=as_of)
    return days


# ── Alert level from days ─────────────────────────────────────────────────────
def _level(days):
    if days is None:  return 'N/A'
    if days <= 3:     return 'RED'
    if days <= 7:     return 'ORANGE'
    if days <= 14:    return 'YELLOW'
    return 'GREEN'


# ── Main backtest ─────────────────────────────────────────────────────────────
def run():
    beam_dates, maint = _load_data()

    # Weekly checkpoints; skip first MIN_HIST days (rolling windows need history)
    # End EVAL_WINDOW days before last beam date so outcomes are observable
    start_date = beam_dates[0] + timedelta(days=MIN_HIST)
    end_date   = beam_dates[-1] - timedelta(days=EVAL_WINDOW)
    checkpoints = [d for d in beam_dates
                   if start_date <= d <= end_date and
                      (d - start_date).days % 7 == 0]

    print(f"Backtest: {checkpoints[0]} to {checkpoints[-1]}")
    print(f"Checkpoints: {len(checkpoints)}  |  Components: {len(COMPONENTS)}")
    print(f"Eval window: {EVAL_WINDOW} days  |  Weekly stride")
    print()

    rows = []
    for i, cp in enumerate(checkpoints):
        if i % 20 == 0:
            pct = 100 * i // len(checkpoints)
            print(f"  [{pct:3d}%] {cp} ...", flush=True)
        for comp in COMPONENTS:
            feats        = build_features(cp, comp, DB)
            counter_days = _counter_as_of(comp, cp, maint)
            past         = [m for m in maint[comp] if m <= cp]
            last_maint   = past[-1].isoformat() if past else 'Unknown'

            pred = predict(comp, feats, MODELS, counter_days, last_maint)

            future = [m for m in maint[comp] if m > cp]
            actual_days = (min(future) - cp).days if future else None

            rows.append({
                'date':        cp,
                'comp':        comp,
                'signal':      pred.primary_signal,
                'pred_days':   round(pred.days_estimate, 1),
                'pred_level':  pred.alert_level,
                'actual_days': actual_days,
                'actual_level': _level(actual_days),
                'error':       round(pred.days_estimate - actual_days, 1)
                               if actual_days is not None else None,
                'abs_error':   round(abs(pred.days_estimate - actual_days), 1)
                               if actual_days is not None else None,
            })

    print(f"  [100%] done — {len(rows)} predictions\n")
    return rows, maint, beam_dates


# ── Metrics ───────────────────────────────────────────────────────────────────
def _metrics(rows, maint, beam_dates):
    # ── 1. Per-component accuracy table ──────────────────────────────────────
    print("=" * 72)
    print("DAYS-REMAINING ACCURACY  (predicted vs actual days to next maintenance)")
    print("=" * 72)
    hdr = f"{'Component':<18} {'N':>4} {'MAE':>6} {'MedAE':>6} {'Bias':>7} {'R²':>6}  {'Signal mix'}"
    print(hdr)
    print("-" * 72)

    for comp in COMPONENTS:
        r = [x for x in rows if x['comp'] == comp and x['abs_error'] is not None]
        if not r:
            print(f"  {comp:<16}    —  (no outcome data)")
            continue
        errs   = [x['error']     for x in r]
        aerrs  = [x['abs_error'] for x in r]
        pdays  = [x['pred_days'] for x in r]
        adays  = [x['actual_days'] for x in r]
        n      = len(r)
        mae    = sum(aerrs) / n
        med    = sorted(aerrs)[n // 2]
        bias   = sum(errs) / n
        # R² = 1 - SS_res / SS_tot
        mean_a = sum(adays) / n
        ss_tot = sum((a - mean_a) ** 2 for a in adays)
        ss_res = sum((p - a) ** 2 for p, a in zip(pdays, adays))
        r2     = 1 - ss_res / ss_tot if ss_tot > 0 else float('nan')
        sigs   = {}
        for x in r:
            sigs[x['signal']] = sigs.get(x['signal'], 0) + 1
        sig_str = '  '.join(f"{k}:{v}" for k, v in sorted(sigs.items()))
        print(f"  {comp:<16} {n:>4} {mae:>6.1f} {med:>6.1f} {bias:>+7.1f} {r2:>6.2f}  {sig_str}")

    # ── 2. Alert level confusion matrix (all components combined) ────────────
    print()
    print("=" * 72)
    print("ALERT LEVEL CONFUSION MATRIX  (rows = predicted, cols = actual)")
    print("=" * 72)
    levels  = ['RED', 'ORANGE', 'YELLOW', 'GREEN']
    valid   = [x for x in rows if x['actual_level'] != 'N/A']
    matrix  = {p: {a: 0 for a in levels} for p in levels}
    for x in valid:
        matrix[x['pred_level']][x['actual_level']] += 1

    print(f"{'Pred\\Actual':<10}", end='')
    for a in levels:
        print(f"  {a:>7}", end='')
    print(f"  {'Total':>6}  {'Precision':>9}")
    print("-" * 72)
    for p in levels:
        row_total = sum(matrix[p].values())
        tp        = matrix[p][p]
        precision = tp / row_total if row_total else 0
        print(f"  {p:<8}", end='')
        for a in levels:
            print(f"  {matrix[p][a]:>7}", end='')
        print(f"  {row_total:>6}  {precision:>9.0%}")
    print("-" * 72)
    col_totals = {a: sum(matrix[p][a] for p in levels) for a in levels}
    print(f"  {'Total':<8}", end='')
    for a in levels:
        print(f"  {col_totals[a]:>7}", end='')
    tp_sum = sum(matrix[l][l] for l in levels)
    print(f"  {sum(col_totals.values()):>6}  {'Accuracy':>9}")
    recalls = []
    print(f"  {'Recall':<8}", end='')
    for a in levels:
        rec = matrix[a][a] / col_totals[a] if col_totals[a] else 0
        recalls.append(rec)
        print(f"  {rec:>7.0%}", end='')
    overall_acc = tp_sum / len(valid) if valid else 0
    print(f"  {'':>6}  {overall_acc:>9.0%}")

    # ── 3. Maintenance event detection — lead time ────────────────────────────
    print()
    print("=" * 72)
    print("MAINTENANCE DETECTION  (how many days warning before each event?)")
    print("=" * 72)
    print(f"{'Component':<18} {'Maint date':>12} {'First ORANGE/RED':>17} {'Lead (d)':>9} {'Signal'}")
    print("-" * 72)

    detected = missed = detected_strict = 0
    lead_times = []
    _STRICT_WINDOW = 30  # must have RED/ORANGE within 30 days of event to count as strict
    for comp in COMPONENTS:
        for maint_date in maint[comp]:
            look_back = [x for x in rows
                         if x['comp'] == comp
                         and x['date'] < maint_date
                         and (maint_date - x['date']).days <= EVAL_WINDOW]
            if not look_back:
                continue
            alerted = [x for x in look_back if x['pred_level'] in ('RED', 'ORANGE')]
            if alerted:
                first = min(alerted, key=lambda x: x['date'])
                lead  = (maint_date - first['date']).days
                lead_times.append(lead)
                detected += 1
                # Strict: at least one RED/ORANGE within 30 days of the event
                if any((maint_date - x['date']).days <= _STRICT_WINDOW for x in alerted):
                    detected_strict += 1
                print(f"  {comp:<16} {maint_date.isoformat():>12} "
                      f"{first['date'].isoformat():>17}  {lead:>8}d  {first['signal']}")
            else:
                missed += 1
                print(f"  {comp:<16} {maint_date.isoformat():>12} "
                      f"{'MISSED':>17}  {'—':>9}")

    total_ev = detected + missed
    det_rate    = detected / total_ev if total_ev else 0
    strict_rate = detected_strict / total_ev if total_ev else 0
    avg_lead    = sum(lead_times) / len(lead_times) if lead_times else 0
    med_lead    = sorted(lead_times)[len(lead_times) // 2] if lead_times else 0
    print("-" * 72)
    print(f"  Detection rate: {detected}/{total_ev} = {det_rate:.0%}   "
          f"Avg lead: {avg_lead:.0f}d   Median lead: {med_lead:.0f}d")
    print(f"  Strict detection (<={_STRICT_WINDOW}d window): "
          f"{detected_strict}/{total_ev} = {strict_rate:.0%}")

    # ── 4. False alarm analysis ────────────────────────────────────────────────
    print()
    print("=" * 72)
    print("FALSE ALARMS  (RED/ORANGE predicted but maintenance > 14 days away)")
    print("=" * 72)
    alarms_all = [x for x in valid if x['pred_level'] in ('RED', 'ORANGE')]
    false_alarms = [x for x in alarms_all if x['actual_days'] is not None and x['actual_days'] > 14]
    fa_rate = len(false_alarms) / len(alarms_all) if alarms_all else 0
    print(f"  Total RED/ORANGE predictions: {len(alarms_all)}")
    print(f"  False alarms (actual > 14d):  {len(false_alarms)}  ({fa_rate:.0%})")
    if false_alarms:
        print(f"\n  Worst false alarms:")
        worst = sorted(false_alarms, key=lambda x: -x['actual_days'])[:5]
        for x in worst:
            print(f"    {x['date']} | {x['comp']:<18} | pred={x['pred_days']}d  actual={x['actual_days']}d  {x['signal']}")

    # ── 5. Snapshot: last 10 checkpoints (most recent predictions) ───────────
    print()
    print("=" * 72)
    print("MOST RECENT PREDICTIONS  (last 3 checkpoint dates)")
    print("=" * 72)
    recent_dates = sorted({x['date'] for x in rows})[-3:]
    print(f"  {'Date':<12} {'Component':<18} {'Pred':>6} {'Act':>6} {'Err':>6}  "
          f"{'PredLvl':<8} {'ActLvl':<8}  {'Signal'}")
    print("-" * 72)
    for d in recent_dates:
        for x in [r for r in rows if r['date'] == d]:
            act  = str(x['actual_days']) if x['actual_days'] is not None else '—'
            err  = f"{x['error']:+.0f}" if x['error'] is not None else '—'
            print(f"  {str(d):<12} {x['comp']:<18} {x['pred_days']:>6.1f} {act:>6} {err:>6}  "
                  f"{x['pred_level']:<8} {x['actual_level']:<8}  {x['signal']}")
        print()

    # ── Summary ───────────────────────────────────────────────────────────────
    all_valid = [x for x in rows if x['abs_error'] is not None]
    overall_mae = sum(x['abs_error'] for x in all_valid) / len(all_valid) if all_valid else 0
    print("=" * 72)
    print(f"OVERALL  MAE={overall_mae:.1f}d   Detection={det_rate:.0%} "
          f"(strict={strict_rate:.0%})   Level accuracy={overall_acc:.0%}   "
          f"False alarm rate={fa_rate:.0%}")
    print("=" * 72)
    print()
    print("Note: ML component predictions (ION SOURCE, FOILS, BL1/BL2) are optimistic")
    print("      — the model was trained on the full dataset (data leakage for in-sample dates).")
    print("      Counter/calendar predictions (TRANSFER LINES) are leakage-free.")


if __name__ == '__main__':
    rows, maint, beam_dates = run()
    _metrics(rows, maint, beam_dates)
