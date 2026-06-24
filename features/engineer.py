import sqlite3
import numpy as np
from datetime import date, timedelta

SOFTWARE_UPDATE_DATE = date(2026, 5, 15)
VALVE_CHANNEL = 'DO_BL2_TSU3_VALVE6'

COMPONENT_PARAMS = {
    'ION SOURCE':    ['AI_IS_CUR', 'AI_IS_VOLT', 'AI_BIAS_VOLT', 'AI_BIAS_CUR', 'AI_BOP_CUR'],
    'FOILS':         ['AI_BL1_FOIL_CUR', 'AI_BL2_FOIL_CUR', 'AI_BL1_COL_CUR', 'AI_BL2_COL_CUR'],
    'BL1 Target 1':  ['AI_BL1_TARG_CUR', 'AI_BL1_FOIL_CUR', 'AI_BOP_CUR'],
    'BL2 Target 1':  ['AI_BL2_TARG_CUR', 'AI_BL2_FOIL_CUR', 'AI_BOP_CUR'],
}
IS_FAULT_CODES = ('10802', '10804', '10808', '10809')
BL_FAULT_CODES = ('10401', '10f01')
COMPONENT_KEYS = {
    'ION SOURCE': 'isc_amphrs',
    'FOILS': 'bl1_foil1_uamphrs',
    'BL1 Target 1': 'bl1_targ1_uamphrs',
    'BL2 Target 1': 'bl2_targ1_uamphrs',
}
AVG_CYCLES = {'ION SOURCE': 46, 'FOILS': 78, 'BL1 Target 1': 51, 'BL2 Target 1': 56}


def _slope(values):
    if len(values) < 2:
        return np.nan
    x = np.arange(len(values), dtype=float)
    return float(np.polyfit(x, values, 1)[0])


def _query_daily_means(conn, params, start: date, end: date) -> dict:
    ph = ','.join('?' * len(params))
    rows = conn.execute(
        f"SELECT date, param, mean FROM beam_daily "
        f"WHERE date >= ? AND date < ? AND param IN ({ph})",
        [start.isoformat(), end.isoformat()] + list(params)
    ).fetchall()
    result = {}
    for d, param, val in rows:
        result.setdefault(param, {})[d] = val
    return result


def build_features(target_date: date, component: str, db_path: str) -> dict:
    conn = sqlite3.connect(db_path)
    features = {}
    params = COMPONENT_PARAMS.get(component, [])

    for w in (7, 14, 30):
        start = target_date - timedelta(days=w)
        daily = _query_daily_means(conn, params, start, target_date)
        for param in params:
            vals_dict = daily.get(param, {})
            vals = [v for v in vals_dict.values() if v is not None]
            if len(vals) < 7:
                features[f'{param}_{w}d_mean'] = np.nan
                features[f'{param}_{w}d_std'] = np.nan
                features[f'{param}_{w}d_slope'] = np.nan
            else:
                arr = sorted(vals_dict.items())
                y = np.array([v for _, v in arr], dtype=float)
                features[f'{param}_{w}d_mean'] = float(np.nanmean(y))
                features[f'{param}_{w}d_std'] = float(np.nanstd(y))
                features[f'{param}_{w}d_slope'] = _slope(y)

    for code in IS_FAULT_CODES:
        for w, label in ((7, '7d'), (14, '14d')):
            start = (target_date - timedelta(days=w)).isoformat()
            cnt = conn.execute(
                "SELECT COUNT(*) FROM events WHERE date(timestamp)>=? AND date(timestamp)<? AND code=?",
                [start, target_date.isoformat(), code]
            ).fetchone()[0]
            features[f'fault_is_{code}_{label}'] = int(cnt)

    for code in BL_FAULT_CODES:
        for w, label in ((7, '7d'), (14, '14d')):
            start = (target_date - timedelta(days=w)).isoformat()
            cnt = conn.execute(
                "SELECT COUNT(*) FROM events WHERE date(timestamp)>=? AND date(timestamp)<? AND code=?",
                [start, target_date.isoformat(), code]
            ).fetchone()[0]
            features[f'fault_bl_{code}_{label}'] = int(cnt)

    start14 = (target_date - timedelta(days=14)).isoformat()
    cnt11001 = conn.execute(
        "SELECT COUNT(*) FROM events WHERE date(timestamp)>=? AND date(timestamp)<? AND code='11001'",
        [start14, target_date.isoformat()]
    ).fetchone()[0]
    features['fault_11001_14d'] = int(cnt11001)

    row = conn.execute(
        "SELECT MAX(date(timestamp)) FROM maintenance_events WHERE component_label=?",
        [component]
    ).fetchone()
    last_maint = row[0] if row and row[0] else None
    days_since = (target_date - date.fromisoformat(last_maint)).days if last_maint else None
    features['days_since_last_maintenance'] = days_since if days_since is not None else np.nan

    avg_cycle = AVG_CYCLES.get(component, 60)
    features['counter_days_remaining'] = float(avg_cycle - days_since) if days_since is not None else float(avg_cycle)

    if component == 'ION SOURCE':
        bop = features.get('AI_BOP_CUR_14d_mean', np.nan)
        isc = features.get('AI_IS_CUR_14d_mean', np.nan)
        if not (np.isnan(bop) or np.isnan(isc)) and isc != 0:
            features['efficiency_ratio'] = bop / isc
            start14_d = target_date - timedelta(days=14)
            daily14 = _query_daily_means(conn, ['AI_BOP_CUR', 'AI_IS_CUR'], start14_d, target_date)
            all_dates = sorted(set(daily14.get('AI_BOP_CUR', {}).keys()) &
                               set(daily14.get('AI_IS_CUR', {}).keys()))
            ratios = [daily14['AI_BOP_CUR'][d] / daily14['AI_IS_CUR'][d]
                      for d in all_dates
                      if daily14['AI_IS_CUR'].get(d) and daily14['AI_IS_CUR'][d] != 0]
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
            "SELECT COUNT(*) FROM events WHERE date(timestamp)>=? AND date(timestamp)<? "
            "AND message LIKE ?",
            [start7, target_date.isoformat(), f'%{VALVE_CHANNEL}%']
        ).fetchone()[0]
        features['valve_bl2_tsu3_toggles_7d'] = int(cnt_valve)
    else:
        features['valve_bl2_tsu3_toggles_7d'] = 0

    features['post_v51_software'] = 1 if target_date >= SOFTWARE_UPDATE_DATE else 0

    conn.close()
    return features
