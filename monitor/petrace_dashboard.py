"""
Compute a ComponentData-shaped dashboard from PETrace batch statistics.
Pure function — no DB access, fully testable without FastAPI.
"""
from datetime import datetime, date, timezone

FOIL_LIFE_MUAH = 3000.0  # µAh, based on observed foil 1 (3239) and foil 2 (2652) lifespans

N_TREND = 20  # real batches used for slope estimation

# Threshold constants shared between classification and pct_life_used scaling
_BEAM_GREEN_FLOOR = 70.0   # µA — below here is not GREEN
_BEAM_RED_THRESH  = 30.0   # µA — at/below here is RED
_RF_GREEN_FLOOR   = 0.97   # — below here is not GREEN
_RF_RED_THRESH    = 0.90   # — at/below here is RED
_VAC_GREEN_CEIL   = 3e-5   # mbar — above here is not GREEN
_VAC_RED_THRESH   = 5e-4   # mbar — at/above here is RED


def _level_foil(pct: float) -> str:
    if pct >= 90: return 'RED'
    if pct >= 70: return 'ORANGE'
    if pct >= 50: return 'YELLOW'
    return 'GREEN'


def _level_beam(uA: float) -> str:
    if uA >= 70: return 'GREEN'
    if uA >= 50: return 'YELLOW'
    if uA >= 30: return 'ORANGE'
    return 'RED'


def _level_rf(eff: float) -> str:
    if eff >= 0.97: return 'GREEN'
    if eff >= 0.95: return 'YELLOW'
    if eff >= 0.90: return 'ORANGE'
    return 'RED'


def _level_vac(P: float) -> str:
    if P < 3e-5: return 'GREEN'
    if P < 1e-4: return 'YELLOW'
    if P < 5e-4: return 'ORANGE'
    return 'RED'


def _linear_slope(xs: list, ys: list) -> float:
    """Least-squares slope dy/dx. Returns 0.0 when <2 points or x has no variance."""
    n = len(xs)
    if n < 2:
        return 0.0
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    den = sum((x - x_mean) ** 2 for x in xs)
    return num / den if den else 0.0


def _batches_per_day(batches: list) -> float:
    """Estimate how many batches occur per calendar day from the list's date span."""
    dates = []
    for b in batches:
        d = b.get('batch_date', '')
        if d:
            try:
                dates.append(date.fromisoformat(str(d)[:10]))
            except (ValueError, TypeError):
                pass
    if len(dates) < 2:
        return 1.0  # conservative default when dates unavailable
    span = (max(dates) - min(dates)).days
    return len(batches) / max(1, span)


def _trend(trend_batches: list, metric: str, current: float,
           lower_thresholds: list = None, upper_thresholds: list = None):
    """Return (slope_per_day, days_to_next_threshold) for a performance metric.

    lower_thresholds: thresholds breached when value drops below them (beam, RF).
    upper_thresholds: thresholds breached when value rises above them (vacuum).
    Returns (0.0, None) when <2 batches or slope is zero.
    """
    if len(trend_batches) < 2:
        return 0.0, None
    xs = [b['batch_no'] for b in trend_batches]
    ys = [b.get(metric) or 0 for b in trend_batches]
    slope_per_batch = _linear_slope(xs, ys)
    if slope_per_batch == 0.0:
        return 0.0, None
    bpd = _batches_per_day(trend_batches)
    slope_per_day = slope_per_batch * bpd
    days_est = None
    if lower_thresholds and slope_per_day < 0:
        for thresh in sorted(lower_thresholds, reverse=True):
            if thresh < current:
                days_est = round((current - thresh) / (-slope_per_day), 1)
                break
    elif upper_thresholds and slope_per_day > 0:
        for thresh in sorted(upper_thresholds):
            if thresh > current:
                days_est = round((thresh - current) / slope_per_day, 1)
                break
    return slope_per_day, days_est


def _trend_reason(slope: float, days_est, unit: str) -> str | None:
    """Build a human-readable trend string, or None when slope is zero."""
    if slope == 0.0:
        return None
    s = f'Trend: {slope:+.3g} {unit}/day'
    if days_est is not None:
        s += f' → est. {days_est:.0f}d to next threshold'
    return s


def _foil_muAh_per_day(batches: list, foil_no) -> float | None:
    """Average µAh/day consumption rate for this foil from its batch history."""
    foil_batches = sorted(
        [b for b in batches if b.get('foil_no') == foil_no and (b.get('total_muAh') or 0) > 0],
        key=lambda b: b.get('batch_no', 0),
    )
    if len(foil_batches) < 2:
        return None
    dates = []
    for b in foil_batches:
        d = b.get('batch_date', '')
        if d:
            try:
                dates.append(date.fromisoformat(str(d)[:10]))
            except (ValueError, TypeError):
                pass
    if len(dates) < 2:
        return None
    span = (max(dates) - min(dates)).days
    if span == 0:
        return None
    total = sum(b.get('total_muAh', 0) or 0 for b in foil_batches)
    return total / span


def _comp(name, alert_level, pct_life_used, days_estimate, top_reasons,
          risk_score, last_maintenance=None, counter_days=None, component_type='wear'):
    return {
        'name': name,
        'alert_level': alert_level,
        'pct_life_used': round(pct_life_used, 1),
        'days_estimate': days_estimate,
        'top_reasons': top_reasons,
        'risk_score': round(min(1.0, max(0.0, risk_score)), 3),
        'primary_signal': 'COUNTER',
        'last_maintenance': last_maintenance,
        'counter_days': counter_days,
        'component_type': component_type,
        'warning': None,
        'trained_at': None,
        'model_age_days': None,
    }


def compute_petrace_dashboard(batches: list) -> dict:
    """
    batches: list of dicts from petrace_batches table (or test fixtures).
    Returns {generated_at, components} in the same shape as /api/dashboard.
    """
    now = datetime.now().isoformat(timespec='seconds')

    # Only real batches (have beam data) count for beam/RF/vacuum
    real = [b for b in batches if (b.get('row_count') or 0) > 0]
    recent_real = sorted(real, key=lambda b: b.get('batch_no', 0), reverse=True)[:10]

    # ── Foil tracking ──────────────────────────────────────────────────────────
    foil_muAh: dict = {}
    foil_last_no: dict = {}
    foil_last_date: dict = {}
    for b in sorted(batches, key=lambda b: b.get('batch_no', 0)):
        fn = b.get('foil_no')
        if fn is None:
            continue
        foil_muAh[fn] = foil_muAh.get(fn, 0.0) + (b.get('total_muAh') or 0.0)
        foil_last_no[fn] = b.get('batch_no', 0)
        foil_last_date[fn] = b.get('batch_date', '')

    active = sorted(foil_last_no.items(), key=lambda x: x[1], reverse=True)[:2]
    while len(active) < 2:
        active.append((None, 0))

    bl1_foil, bl2_foil = active[0][0], active[1][0]
    bl1_muAh = foil_muAh.get(bl1_foil, 0.0) if bl1_foil is not None else 0.0
    bl2_muAh = foil_muAh.get(bl2_foil, 0.0) if bl2_foil is not None else 0.0
    bl1_date = foil_last_date.get(bl1_foil) if bl1_foil is not None else None
    bl2_date = foil_last_date.get(bl2_foil) if bl2_foil is not None else None
    bl1_pct = (bl1_muAh / FOIL_LIFE_MUAH) * 100
    bl2_pct = (bl2_muAh / FOIL_LIFE_MUAH) * 100

    # ── Averages over the last 10 real batches ─────────────────────────────────
    avg_beam = (sum(b.get('peak_target_uA', 0) or 0 for b in recent_real)
                / len(recent_real)) if recent_real else 0.0
    avg_rf   = (sum(b.get('rf_efficiency', 0) or 0 for b in recent_real)
                / len(recent_real)) if recent_real else 0.0
    avg_vac  = (sum(b.get('peak_vacuum_P', 0) or 0 for b in recent_real)
                / len(recent_real)) if recent_real else 0.0

    # ── Trend signals over last N_TREND real batches ───────────────────────────
    trend_batches = sorted(real, key=lambda b: b.get('batch_no', 0))[-N_TREND:]

    beam_slope, beam_days = _trend(
        trend_batches, 'peak_target_uA', avg_beam,
        lower_thresholds=[_BEAM_GREEN_FLOOR, 50.0, _BEAM_RED_THRESH],
    )
    rf_slope, rf_days = _trend(
        trend_batches, 'rf_efficiency', avg_rf,
        lower_thresholds=[_RF_GREEN_FLOOR, 0.95, _RF_RED_THRESH],
    )
    vac_slope, vac_days = _trend(
        trend_batches, 'peak_vacuum_P', avg_vac,
        upper_thresholds=[_VAC_GREEN_CEIL, 1e-4, _VAC_RED_THRESH],
    )

    # ── pct_life_used: 0% = at healthy boundary, 100% = at RED threshold ──────
    beam_pct = max(0.0, min(100.0,
        (_BEAM_GREEN_FLOOR - avg_beam) / (_BEAM_GREEN_FLOOR - _BEAM_RED_THRESH) * 100.0))
    rf_pct = max(0.0, min(100.0,
        (_RF_GREEN_FLOOR - avg_rf) / (_RF_GREEN_FLOOR - _RF_RED_THRESH) * 100.0))
    vac_pct = max(0.0, min(100.0,
        (avg_vac - _VAC_GREEN_CEIL) / (_VAC_RED_THRESH - _VAC_GREEN_CEIL) * 100.0))

    # ── Top reasons ────────────────────────────────────────────────────────────
    beam_reasons = [f'Avg peak Target-I: {avg_beam:.1f} µA (last {len(recent_real)} batches)']
    beam_trend = _trend_reason(beam_slope, beam_days, 'µA')
    if beam_trend:
        beam_reasons.append(beam_trend)

    rf_reasons = [f'Avg RF efficiency: {avg_rf:.1%} (last {len(recent_real)} batches)']
    rf_trend = _trend_reason(rf_slope, rf_days, '%eff')
    if rf_trend:
        rf_reasons.append(rf_trend)

    vac_reasons = [f'Avg peak vacuum: {avg_vac:.2e} mbar (last {len(recent_real)} batches)']
    vac_trend = _trend_reason(vac_slope, vac_days, 'mbar')
    if vac_trend:
        vac_reasons.append(vac_trend)

    # ── Foil days remaining: estimate from charge consumption rate ────────────────
    bl1_rate = _foil_muAh_per_day(batches, bl1_foil) if bl1_foil is not None else None
    bl2_rate = _foil_muAh_per_day(batches, bl2_foil) if bl2_foil is not None else None

    def _foil_days(rate, used_muAh):
        if rate is None or rate <= 0:
            return None
        remaining = FOIL_LIFE_MUAH - used_muAh
        return round(max(0.0, remaining / rate), 1)

    bl1_days = _foil_days(bl1_rate, bl1_muAh)
    bl2_days = _foil_days(bl2_rate, bl2_muAh)

    def _foil_reasons(used_muAh, pct, rate, days):
        reasons = [f'{used_muAh:.0f} / {FOIL_LIFE_MUAH:.0f} µAh used ({pct:.0f}%)']
        if rate is not None:
            reasons.append(f'Usage rate: {rate:.1f} µAh/day')
        if days is not None:
            reasons.append(f'Est. {days:.0f}d remaining at current rate')
        return reasons

    components = [
        _comp(
            name=f'Foil BL1 (#{bl1_foil})' if bl1_foil is not None else 'Foil BL1',
            alert_level=_level_foil(bl1_pct),
            pct_life_used=bl1_pct,
            days_estimate=bl1_days,
            top_reasons=_foil_reasons(bl1_muAh, bl1_pct, bl1_rate, bl1_days),
            risk_score=bl1_pct / 100,
            last_maintenance=bl1_date,
            component_type='wear',
        ),
        _comp(
            name=f'Foil BL2 (#{bl2_foil})' if bl2_foil is not None else 'Foil BL2',
            alert_level=_level_foil(bl2_pct),
            pct_life_used=bl2_pct,
            days_estimate=bl2_days,
            top_reasons=_foil_reasons(bl2_muAh, bl2_pct, bl2_rate, bl2_days),
            risk_score=bl2_pct / 100,
            last_maintenance=bl2_date,
            component_type='wear',
        ),
        _comp(
            name='Beam Current',
            alert_level=_level_beam(avg_beam),
            pct_life_used=beam_pct,
            days_estimate=beam_days,
            top_reasons=beam_reasons,
            risk_score=max(0.0, 1.0 - avg_beam / 84.0),
            component_type='performance',
        ),
        _comp(
            name='RF System',
            alert_level=_level_rf(avg_rf),
            pct_life_used=rf_pct,
            days_estimate=rf_days,
            top_reasons=rf_reasons,
            risk_score=max(0.0, 1.0 - avg_rf),
            component_type='performance',
        ),
        _comp(
            name='Vacuum System',
            alert_level=_level_vac(avg_vac),
            pct_life_used=vac_pct,
            days_estimate=vac_days,
            top_reasons=vac_reasons,
            risk_score=min(1.0, avg_vac / 5e-4),
            component_type='performance',
        ),
    ]

    return {'generated_at': now, 'components': components}
