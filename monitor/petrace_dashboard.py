"""
Compute a ComponentData-shaped dashboard from PETrace batch statistics.
Pure function — no DB access, fully testable without FastAPI.
"""
from datetime import datetime, timezone

FOIL_LIFE_MUAH = 3000.0  # µAh, based on observed foil 1 (3239) and foil 2 (2652) lifespans


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


def _comp(name, alert_level, pct_life_used, days_estimate, top_reasons,
          risk_score, last_maintenance=None, counter_days=None):
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
        'warning': None,
        'trained_at': None,
        'model_age_days': None,
    }


def compute_petrace_dashboard(batches: list) -> dict:
    """
    batches: list of dicts from petrace_batches table (or test fixtures).
    Returns {generated_at, components} in the same shape as /api/dashboard.
    """
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')

    # Only real batches (have beam data) count for beam/RF/vacuum
    real = [b for b in batches if (b.get('row_count') or 0) > 0]
    recent_real = sorted(real, key=lambda b: b.get('batch_no', 0), reverse=True)[:10]

    # ── Foil tracking ──────────────────────────────────────────────────────────
    # Sum µAh and find last batch_no per foil
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

    # Two most recently active foils = BL1, BL2
    active = sorted(foil_last_no.items(), key=lambda x: x[1], reverse=True)[:2]
    # If only one active foil, pad with None
    while len(active) < 2:
        active.append((None, 0))

    bl1_foil, bl2_foil = active[0][0], active[1][0]
    bl1_muAh = foil_muAh.get(bl1_foil, 0.0) if bl1_foil is not None else 0.0
    bl2_muAh = foil_muAh.get(bl2_foil, 0.0) if bl2_foil is not None else 0.0
    bl1_date = foil_last_date.get(bl1_foil) if bl1_foil is not None else None
    bl2_date = foil_last_date.get(bl2_foil) if bl2_foil is not None else None
    bl1_pct = (bl1_muAh / FOIL_LIFE_MUAH) * 100
    bl2_pct = (bl2_muAh / FOIL_LIFE_MUAH) * 100

    # ── Beam / RF / Vacuum ────────────────────────────────────────────────────
    avg_beam = (sum(b.get('peak_target_uA', 0) or 0 for b in recent_real)
                / len(recent_real)) if recent_real else 0.0
    avg_rf = (sum(b.get('rf_efficiency', 0) or 0 for b in recent_real)
              / len(recent_real)) if recent_real else 0.0
    avg_vac = (sum(b.get('peak_vacuum_P', 0) or 0 for b in recent_real)
               / len(recent_real)) if recent_real else 0.0

    components = [
        _comp(
            name=f'Foil BL1 (#{bl1_foil})' if bl1_foil is not None else 'Foil BL1',
            alert_level=_level_foil(bl1_pct),
            pct_life_used=bl1_pct,
            days_estimate=None,
            top_reasons=[f'{bl1_muAh:.0f} / {FOIL_LIFE_MUAH:.0f} µAh used ({bl1_pct:.0f}%)'],
            risk_score=bl1_pct / 100,
            last_maintenance=bl1_date,
        ),
        _comp(
            name=f'Foil BL2 (#{bl2_foil})' if bl2_foil is not None else 'Foil BL2',
            alert_level=_level_foil(bl2_pct),
            pct_life_used=bl2_pct,
            days_estimate=None,
            top_reasons=[f'{bl2_muAh:.0f} / {FOIL_LIFE_MUAH:.0f} µAh used ({bl2_pct:.0f}%)'],
            risk_score=bl2_pct / 100,
            last_maintenance=bl2_date,
        ),
        _comp(
            name='Beam Current',
            alert_level=_level_beam(avg_beam),
            pct_life_used=0.0,
            days_estimate=None,
            top_reasons=[f'Avg peak Target-I: {avg_beam:.1f} µA (last {len(recent_real)} batches)'],
            risk_score=max(0.0, 1.0 - avg_beam / 84.0),
        ),
        _comp(
            name='RF System',
            alert_level=_level_rf(avg_rf),
            pct_life_used=0.0,
            days_estimate=None,
            top_reasons=[f'Avg RF efficiency: {avg_rf:.1%} (last {len(recent_real)} batches)'],
            risk_score=max(0.0, 1.0 - avg_rf),
        ),
        _comp(
            name='Vacuum System',
            alert_level=_level_vac(avg_vac),
            pct_life_used=0.0,
            days_estimate=None,
            top_reasons=[f'Avg peak vacuum: {avg_vac:.2e} mbar (last {len(recent_real)} batches)'],
            risk_score=min(1.0, avg_vac / 5e-4),
        ),
    ]

    return {'generated_at': now, 'components': components}
