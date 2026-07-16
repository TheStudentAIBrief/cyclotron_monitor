import sqlite3
import numpy as np
from datetime import date, timedelta
from models.counter import dynamic_avg_cycle

SOFTWARE_UPDATE_DATE = date(2026, 5, 15)
VALVE_CHANNEL = 'DO_BL2_TSU3_VALVE6'

COMPONENT_PARAMS = {
    'ION SOURCE':    ['AI_IS_CUR', 'AI_IS_VOLT', 'AI_BIAS_VOLT', 'AI_BIAS_CUR', 'AI_BOP_CUR'],
    'FOILS':         ['AI_BL1_FOIL_CUR', 'AI_BL2_FOIL_CUR', 'AI_BL1_COL_CUR', 'AI_BL2_COL_CUR'],
    'BL1 Target 1':  ['AI_BL1_TARG_CUR', 'AI_BL1_FOIL_CUR', 'AI_BOP_CUR'],
    'BL2 Target 1':  ['AI_BL2_TARG_CUR', 'AI_BL2_FOIL_CUR', 'AI_BOP_CUR'],
}
# IS fault codes: original set + 10807 (high IS current, 2.7x spike) + 10504 (bias PSU off, 2.4x)
# + 10901/11d01/11d03 (QEI RF-controller unlock/ack failures — 7.9x rate in the 14d before
# ION SOURCE maintenance across full archive history, ~99% of all occurrences fall in that window)
IS_FAULT_CODES = ('10802', '10804', '10807', '10808', '10809', '10504',
                   '10901', '11d01', '11d03')
# BL fault codes: original + 10205 (high collimator current on BL1, 1.9x spike)
# + 10206 (stray target current on BL1, water conductivity — 9.7x before FOILS, 6.9x before
# BL1 Target 1) + 10207 (stray foil current on non-active BL2 — 3.6x before FOILS)
BL_FAULT_CODES = ('10401', '10f01', '10205', '10206', '10207')
# Vacuum fault codes: 12072 (high tank pressure, 1.5x spike pre-maintenance)
VACUUM_FAULT_CODES = ('12072',)

# Real vendor warning-message prefixes (events table, code 11001) — see
# models/counter.py's COMPONENT_KEYS for the full explanation and confirmation
# source. NOT the same as the setlifetime{} command keys used by
# parsers/maintenance_labels.py, which is a different vocabulary/purpose.
COMPONENT_KEYS = {
    'ION SOURCE': 'ion source Amp-hrs',
    'FOILS': 'BL1 foil 1 uAmp-hrs',
    'BL1 Target 1': 'BL1 target 1 uAmp-hrs',
    'BL2 Target 1': 'BL2 target 1 uAmp-hrs',  # unverified, see models/counter.py comment
}
# All 6 foils are always replaced together; stored as separate labels in the DB
FOILS_LABELS = ('BL1 Foil 1', 'BL1 Foil 2', 'BL1 Foil 3',
                 'BL2 Foil 1', 'BL2 Foil 2', 'BL2 Foil 3')
# Minimum beam readings required per window — cyclotron runs ~5-6 days/week so
# a 7-calendar-day window yields ~5 readings on average; requiring 7 would NaN 86% of samples
MIN_READINGS = {7: 3, 14: 5, 30: 7}

# petrace_batches: per-batch production log, not daily-aggregated like beam_daily, and far
# sparser (42 distinct dates across ~339 days, bursty — median gap 2d but mean gap 8.2d).
# total_muAh (beam throughput) and rf_efficiency both showed a significant pre-maintenance
# drop for BL2 Target 1 (d=-0.67 / -0.48) and FOILS (d=-0.58 for total_muAh).
# Coverage is 2024-06-16..2025-05-20 only — ingestion has not produced newer rows since,
# so these features will be NaN for any target_date after that until ingestion resumes.
PETRACE_COLS = ('total_muAh', 'rf_efficiency')
PETRACE_MIN_READINGS = {7: 1, 14: 2, 30: 3}

# Human-contact / machine-struggle categories mined from the full 30M-event log
# (operator commands, resets, tuning/calibration, interlock aborts, comms
# timeouts, excess-power, ISC clamping, standby/shutdown). Counted per window from
# a precomputed daily_category table — see build_features. Additive; 0 when absent.
HUMACT_CATEGORIES = ('struggle', 'power_state', 'operator_cmd', 'interlock',
                     'mode_change', 'service_manual', 'reset', 'tune_adjust')


def _slope(values):
    if len(values) < 2:
        return np.nan
    x = np.arange(len(values), dtype=float)
    return float(np.polyfit(x, values, 1)[0])


def _query_daily_stats(conn, params, start: date, end: date) -> dict:
    """Return {param: {date: (mean, p10, p90)}} excluding sparse-quality days.

    Sparse days (cyclotron barely running) are excluded because their statistics
    are computed from too few readings and inject noise into rolling-window features.
    IS NOT is used instead of != to handle NULL data_quality values in legacy rows.
    """
    if not params:
        return {}
    ph = ','.join('?' * len(params))
    rows = conn.execute(
        f"SELECT date, param, mean, p10, p90 FROM beam_daily "
        f"WHERE date >= ? AND date < ? AND param IN ({ph})"
        f" AND data_quality IS NOT 'sparse'",
        [start.isoformat(), end.isoformat()] + list(params)
    ).fetchall()
    result: dict = {}
    for d, param, mean_v, p10_v, p90_v in rows:
        result.setdefault(param, {})[d] = (mean_v, p10_v, p90_v)
    return result


def _query_daily_means(conn, params, start: date, end: date) -> dict:
    """Return {param: {date: mean}} — thin wrapper over _query_daily_stats."""
    raw = _query_daily_stats(conn, params, start, end)
    return {param: {d: v[0] for d, v in dates.items()}
            for param, dates in raw.items()}


def _query_petrace_daily(conn, cols, start: date, end: date) -> dict:
    """Return {col: {date: mean}} from petrace_batches, averaging same-date batches.
    A DB with no petrace_batches table (e.g. a lab with no PETrace 800, or a test
    fixture not set up for it) is treated the same as one with no rows in range —
    empty result, features fall back to NaN — rather than a hard crash."""
    ph = ', '.join(cols)
    try:
        rows = conn.execute(
            f"SELECT batch_date, {ph} FROM petrace_batches WHERE batch_date >= ? AND batch_date < ?",
            [start.isoformat(), end.isoformat()]
        ).fetchall()
    except sqlite3.OperationalError:
        return {c: {} for c in cols}
    sums: dict = {c: {} for c in cols}
    counts: dict = {c: {} for c in cols}
    for row in rows:
        d = row[0]
        for i, col in enumerate(cols):
            v = row[i + 1]
            if v is None:
                continue
            sums[col][d] = sums[col].get(d, 0.0) + v
            counts[col][d] = counts[col].get(d, 0) + 1
    return {col: {d: sums[col][d] / counts[col][d] for d in sums[col]} for col in cols}


def _last_maintenance_date(conn, component: str, before: date = None):
    """Return ISO date string of last maintenance <= before, handling FOILS aggregation."""
    cutoff = before.isoformat() if before else '9999-12-31'
    if component == 'FOILS':
        ph = ','.join('?' * len(FOILS_LABELS))
        row = conn.execute(
            f"SELECT MAX(date(timestamp)) FROM maintenance_events "
            f"WHERE component_label IN ({ph}) AND date(timestamp) <= ?",
            list(FOILS_LABELS) + [cutoff]
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT MAX(date(timestamp)) FROM maintenance_events "
            "WHERE component_label=? AND date(timestamp) <= ?",
            [component, cutoff]
        ).fetchone()
    return row[0] if row and row[0] else None


def build_features(target_date: date, component: str, db_path: str) -> dict:
    conn = sqlite3.connect(db_path, timeout=30)
    features = {}
    params = COMPONENT_PARAMS.get(component, [])
    # Cache daily stats per window — reused in the efficiency slope calculation
    # to avoid a redundant query for AI_BOP_CUR and AI_IS_CUR.
    _daily_cache: dict = {}
    try:
        for w in (7, 14, 30):
            start = target_date - timedelta(days=w)
            daily = _query_daily_stats(conn, params, start, target_date)
            _daily_cache[w] = daily
            min_req = MIN_READINGS[w]
            for param in params:
                vals_dict = daily.get(param, {})
                # Only count dates where mean is non-null (beam was running and sensor reporting).
                valid_dates = sorted(d for d, v in vals_dict.items() if v[0] is not None)
                n = len(valid_dates)
                if n < min_req:
                    features[f'{param}_{w}d_mean']  = np.nan
                    features[f'{param}_{w}d_std']   = np.nan
                    features[f'{param}_{w}d_slope'] = np.nan
                    features[f'{param}_{w}d_p10']   = np.nan
                    features[f'{param}_{w}d_p90']   = np.nan
                else:
                    y_mean = np.array([vals_dict[d][0] for d in valid_dates], dtype=float)
                    # p10/p90 may be NULL even on valid days (older log format) — filter separately.
                    p10_vals = [vals_dict[d][1] for d in valid_dates if vals_dict[d][1] is not None]
                    p90_vals = [vals_dict[d][2] for d in valid_dates if vals_dict[d][2] is not None]
                    features[f'{param}_{w}d_mean']  = float(np.nanmean(y_mean))
                    features[f'{param}_{w}d_std']   = float(np.nanstd(y_mean))
                    features[f'{param}_{w}d_slope'] = _slope(y_mean)
                    features[f'{param}_{w}d_p10']   = float(np.mean(p10_vals)) if p10_vals else np.nan
                    features[f'{param}_{w}d_p90']   = float(np.mean(p90_vals)) if p90_vals else np.nan

        for w in (7, 14, 30):
            start = target_date - timedelta(days=w)
            petrace_daily = _query_petrace_daily(conn, PETRACE_COLS, start, target_date)
            min_req = PETRACE_MIN_READINGS[w]
            for col in PETRACE_COLS:
                vals_dict = petrace_daily.get(col, {})
                valid_dates = sorted(vals_dict)
                n = len(valid_dates)
                if n < min_req:
                    features[f'petrace_{col}_{w}d_mean']  = np.nan
                    features[f'petrace_{col}_{w}d_std']   = np.nan
                    features[f'petrace_{col}_{w}d_slope'] = np.nan
                else:
                    y = np.array([vals_dict[d] for d in valid_dates], dtype=float)
                    features[f'petrace_{col}_{w}d_mean']  = float(np.mean(y))
                    features[f'petrace_{col}_{w}d_std']   = float(np.std(y))
                    features[f'petrace_{col}_{w}d_slope'] = _slope(y)

        for code in IS_FAULT_CODES:
            for w, label in ((7, '7d'), (14, '14d')):
                start = (target_date - timedelta(days=w)).isoformat()
                cnt = conn.execute(
                    "SELECT COUNT(*) FROM events WHERE code=? AND timestamp>=? AND timestamp<?",
                    [code, start, target_date.isoformat()]
                ).fetchone()[0]
                features[f'fault_is_{code}_{label}'] = int(cnt)

        for code in BL_FAULT_CODES:
            for w, label in ((7, '7d'), (14, '14d')):
                start = (target_date - timedelta(days=w)).isoformat()
                cnt = conn.execute(
                    "SELECT COUNT(*) FROM events WHERE code=? AND timestamp>=? AND timestamp<?",
                    [code, start, target_date.isoformat()]
                ).fetchone()[0]
                features[f'fault_bl_{code}_{label}'] = int(cnt)

        for code in VACUUM_FAULT_CODES:
            for w, label in ((7, '7d'), (14, '14d')):
                start = (target_date - timedelta(days=w)).isoformat()
                cnt = conn.execute(
                    "SELECT COUNT(*) FROM events WHERE code=? AND timestamp>=? AND timestamp<?",
                    [code, start, target_date.isoformat()]
                ).fetchone()[0]
                features[f'fault_vac_{code}_{label}'] = int(cnt)

        start14 = (target_date - timedelta(days=14)).isoformat()
        _comp_key = COMPONENT_KEYS.get(component, '')
        if _comp_key:
            cnt11001 = conn.execute(
                "SELECT COUNT(*) FROM events WHERE code='11001' AND message LIKE ? "
                "AND timestamp>=? AND timestamp<?",
                [f'%{_comp_key}%', start14, target_date.isoformat()]
            ).fetchone()[0]
        else:
            cnt11001 = conn.execute(
                "SELECT COUNT(*) FROM events WHERE code='11001' "
                "AND timestamp>=? AND timestamp<?",
                [start14, target_date.isoformat()]
            ).fetchone()[0]
        features['fault_11001_14d'] = int(cnt11001)

        # Human-contact / machine-struggle category features: operator actions
        # (commands, resets, calibration, tuning) and the machine working harder
        # as parts wear (comms timeouts, excess power, ISC clamping, interlock
        # aborts, standby/shutdown). Read from a precomputed daily_category table
        # (aggregated from the full event log) when present; absent (e.g. a live DB
        # without the aggregation, or a test fixture) -> 0, purely additive.
        for w in (7, 14):
            cstart = (target_date - timedelta(days=w)).isoformat()
            for cat in HUMACT_CATEGORIES:
                try:
                    n = conn.execute(
                        "SELECT COALESCE(SUM(count),0) FROM daily_category "
                        "WHERE category=? AND date>=? AND date<?",
                        [cat, cstart, target_date.isoformat()]
                    ).fetchone()[0]
                except sqlite3.OperationalError:
                    n = 0
                features[f'humact_{cat}_{w}d'] = int(n)

        last_maint = _last_maintenance_date(conn, component, before=target_date)
        days_since = (target_date - date.fromisoformat(last_maint)).days if last_maint else None
        features['days_since_last_maintenance'] = days_since if days_since is not None else np.nan

        # Use the dynamically computed avg cycle (lower-median of historical maintenance intervals)
        # so this feature matches the counter.py production estimate exactly.
        # Falls back to AVG_CYCLES when fewer than 4 maintenance events exist.
        avg_cycle = dynamic_avg_cycle(conn, component, target_date.isoformat())
        features['counter_days_remaining'] = (
            max(0.0, float(avg_cycle - days_since)) if days_since is not None else float(avg_cycle)
        )

        if component == 'ION SOURCE':
            bop = features.get('AI_BOP_CUR_14d_mean', np.nan)
            isc = features.get('AI_IS_CUR_14d_mean', np.nan)
            if not (np.isnan(bop) or np.isnan(isc)) and isc != 0:
                features['efficiency_ratio'] = bop / isc
                # Reuse the cached 14d window — no second DB query needed.
                w14 = _daily_cache[14]
                bop_by_date = w14.get('AI_BOP_CUR', {})
                isc_by_date = w14.get('AI_IS_CUR', {})
                all_dates = sorted(set(bop_by_date) & set(isc_by_date))
                ratios = [bop_by_date[d][0] / isc_by_date[d][0]
                          for d in all_dates
                          if isc_by_date[d][0] and isc_by_date[d][0] != 0
                          and bop_by_date[d][0] is not None]
                features['efficiency_slope_14d'] = _slope(ratios)
            else:
                features['efficiency_ratio'] = np.nan
                features['efficiency_slope_14d'] = np.nan
        else:
            features['efficiency_ratio'] = np.nan
            features['efficiency_slope_14d'] = np.nan

        if component == 'BL2 Target 1':
            start7 = (target_date - timedelta(days=7)).isoformat()
            cnt_valve = conn.execute(
                "SELECT COUNT(*) FROM events WHERE timestamp>=? AND timestamp<? "
                "AND message LIKE ?",
                [start7, target_date.isoformat(), f'%{VALVE_CHANNEL}%']
            ).fetchone()[0]
            features['valve_bl2_tsu3_toggles_7d'] = int(cnt_valve)
        else:
            features['valve_bl2_tsu3_toggles_7d'] = 0

        features['post_v51_software'] = 1 if target_date >= SOFTWARE_UPDATE_DATE else 0

    finally:
        conn.close()
    return features
