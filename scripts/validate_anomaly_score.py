"""One-off empirical validation: does models/anomaly.py's score_anomaly actually
correlate with real upcoming maintenance events on the real database, using the
same walk-forward checkpoints as backtest.py? Not part of the app - a check run
once to decide whether the anomaly score is worth wiring into production.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from models.anomaly import compute_anomaly_score

DB = 'data/cyclotron.db'
COMPONENTS = ['ION SOURCE', 'FOILS', 'BL1 Target 1', 'BL2 Target 1']
EVAL_WINDOW = 90
MIN_HIST = 30


def _load_data():
    import sqlite3
    conn = sqlite3.connect(DB, timeout=30)
    beam_dates = [date.fromisoformat(r[0]) for r in
                  conn.execute("SELECT DISTINCT date FROM beam_daily ORDER BY date").fetchall()]
    from models.counter import FOILS_LABELS
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


def main():
    beam_dates, maint = _load_data()
    start_date = beam_dates[0] + timedelta(days=MIN_HIST)
    end_date = beam_dates[-1] - timedelta(days=EVAL_WINDOW)
    checkpoints = [d for d in beam_dates if start_date <= d <= end_date and (d - start_date).days % 7 == 0]

    print(f"Validating anomaly score: {checkpoints[0]} to {checkpoints[-1]}, {len(checkpoints)} checkpoints x {len(COMPONENTS)} components")

    rows = []
    for i, cp in enumerate(checkpoints):
        if i % 10 == 0:
            print(f"  [{100*i//len(checkpoints):3d}%] {cp} ...", flush=True)
        for comp in COMPONENTS:
            score = compute_anomaly_score(comp, cp, DB)
            future = [m for m in maint[comp] if m > cp]
            actual_days = (min(future) - cp).days if future else None
            rows.append({'date': cp, 'comp': comp, 'anomaly_score': score, 'actual_days': actual_days})

    print(f"\n{'='*70}\nRESULTS ({len(rows)} predictions)\n{'='*70}")

    import numpy as np
    scored = [r for r in rows if r['actual_days'] is not None]
    scores = np.array([r['anomaly_score'] for r in scored])
    days = np.array([r['actual_days'] for r in scored])

    # Spearman correlation: does higher anomaly score associate with fewer days remaining?
    from scipy.stats import spearmanr
    rho, pval = spearmanr(scores, days)
    print(f"Spearman correlation(anomaly_score, actual_days_until_maintenance): rho={rho:.3f} p={pval:.4f}")
    print("(negative rho = higher anomaly score associates with SOONER maintenance, as hypothesized)")

    # AUC-style check: does a high anomaly score (>0.7) predict maintenance within 30 days better than chance?
    near = days <= 30
    high_anomaly = scores > 0.7
    if near.sum() > 0 and (~near).sum() > 0:
        precision = high_anomaly[near].sum() / max(1, high_anomaly.sum())
        recall = high_anomaly[near].sum() / near.sum()
        base_rate = near.mean()
        print(f"\nHigh anomaly (>0.7) as a 'maintenance within 30d' flag:")
        print(f"  precision={precision:.2f} recall={recall:.2f} base_rate={base_rate:.2f} "
              f"(precision should exceed base_rate to show real signal)")

    # Per-component breakdown
    print(f"\n{'Component':<16} {'N':>4} {'MeanAnomaly':>12} {'Spearman rho':>13}")
    for comp in COMPONENTS:
        r = [x for x in scored if x['comp'] == comp]
        if not r:
            continue
        s = np.array([x['anomaly_score'] for x in r])
        d = np.array([x['actual_days'] for x in r])
        rho_c, _ = spearmanr(s, d)
        print(f"  {comp:<14} {len(r):>4} {s.mean():>12.3f} {rho_c:>13.3f}")


if __name__ == '__main__':
    main()
