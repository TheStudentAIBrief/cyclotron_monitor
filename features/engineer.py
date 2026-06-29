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
IS_FAULT_CODES = ('10802', '10804', '10807', '10808', '10809', '10504')
# BL fault codes: original + 10205 (high collimator current on BL1, 1.9x spike)
BL_FAULT_CODES = ('10401', '10f01', '10205')
# Vacuum fault codes: 12072 (high tank pressure, 1.5x spike pre-maintenance)
VACUUM_FAULT_CODES = ('12072',)
COMPONENT_KEYS = {
    'ION SOURCE': 'isc_amphrs',
    'FOILS': 'bl1_foil1_uamphrs',
    'BL1 Target 1': 'bl1_targ1_uamphrs',
    'BL2 Target 1': 'bl2_targ1_uamphrs',
}
# All 6 foils are always replaced together; stored as separate labels in the DB
FOILS_LABELS = ('BL1 Foil 1', 'BL1 Foil 2', 'BL1 Foil 3',
                 'BL2 Foil 1', 'BL2 Foil 2', 'BL2 Foil 3')
# Minimum beam readings required per window — cyclotron runs ~5-6 days/week so
# a 7-calendar-day window yields ~5 readings on average; requiring 7 would NaN 86% of samples
MIN_READINGS = {7: 3, 14: 5, 30: 7}


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
