"""
TDD tests for the PETrace dashboard computation.
Tests written RED first — compute_petrace_dashboard must not exist yet.

The function takes a list of batch dicts (as returned by the DB) and returns
a dict matching the DashboardData shape: {generated_at, components: [...]}
where each component matches ComponentData (same fields as the main dashboard).

Five components: FOIL BL1, FOIL BL2, BEAM CURRENT, RF SYSTEM, VACUUM
"""
import pytest
from datetime import date, timedelta
from monitor.petrace_dashboard import compute_petrace_dashboard, FOIL_LIFE_MUAH

# ── Helpers ───────────────────────────────────────────────────────────────────

def _batch(batch_no=1, batch_date='2025-05-20', foil_no=3,
           peak_target_uA=84.0, avg_target_uA=70.0,
           total_muAh=60.0, rf_efficiency=0.980,
           peak_vacuum_P=1.0e-5, row_count=100,
           avg_arc_I=45.0):
    return {
        'batch_no': batch_no, 'batch_date': batch_date, 'foil_no': foil_no,
        'peak_target_uA': peak_target_uA, 'avg_target_uA': avg_target_uA,
        'total_muAh': total_muAh, 'rf_efficiency': rf_efficiency,
        'peak_vacuum_P': peak_vacuum_P, 'row_count': row_count,
        'avg_arc_I': avg_arc_I, 'tracer_name': '18F- self-shielded',
        'duration_s': 7200, 'tracer_num': 4,
    }

def _foil3_batches(n_batches=10, muAh_per_batch=60.0):
    """n_batches of foil 3, then n_batches of foil 4, alternating."""
    batches = []
    for i in range(n_batches):
        batches.append(_batch(batch_no=i*2+1, batch_date='2025-05-20', foil_no=3,
                               total_muAh=muAh_per_batch))
        batches.append(_batch(batch_no=i*2+2, batch_date='2025-05-20', foil_no=4,
                               total_muAh=50.0))
    return batches

# ── Structure ──────────────────────────────────────────────────────────────────

def test_returns_generated_at_field():
    result = compute_petrace_dashboard([_batch()])
    assert 'generated_at' in result

def test_returns_5_components():
    result = compute_petrace_dashboard([_batch()])
    assert len(result['components']) == 5

def test_component_names_are_present():
    result = compute_petrace_dashboard([_batch()])
    names = [c['name'] for c in result['components']]
    assert any('Foil' in n for n in names)
    assert any('Beam' in n or 'Target' in n for n in names)
    assert any('RF' in n for n in names)
    assert any('Vacuum' in n for n in names)

def test_all_components_have_required_fields():
    result = compute_petrace_dashboard([_batch()])
    required = {'name', 'alert_level', 'pct_life_used', 'days_estimate',
                 'top_reasons', 'risk_score', 'primary_signal',
                 'last_maintenance', 'counter_days', 'warning',
                 'trained_at', 'model_age_days'}
    for comp in result['components']:
        for field in required:
            assert field in comp, f"Component '{comp.get('name')}' missing '{field}'"

def test_alert_level_values_are_valid():
    result = compute_petrace_dashboard([_batch()])
    valid = {'RED', 'ORANGE', 'YELLOW', 'GREEN'}
    for comp in result['components']:
        assert comp['alert_level'] in valid

# ── Foil health ────────────────────────────────────────────────────────────────

def test_foil_green_when_pct_below_50():
    """Foil with < 50% life used → GREEN."""
    # foil 3 accumulates 10 batches × 60 µAh = 600 µAh (20% of FOIL_LIFE_MUAH ~3000)
    batches = _foil3_batches(n_batches=5, muAh_per_batch=60.0)  # 5 × 60 = 300 µAh
    result = compute_petrace_dashboard(batches)
    foil_comps = [c for c in result['components'] if 'Foil' in c['name'] and 'BL1' in c['name']]
    assert foil_comps, "Expected a 'Foil BL1' component"
    assert foil_comps[0]['alert_level'] == 'GREEN'

def test_foil_yellow_when_pct_50_to_70():
    """50–70% → YELLOW."""
    muAh = FOIL_LIFE_MUAH * 0.60  # 60%
    batches = [_batch(foil_no=3, total_muAh=muAh)]
    result = compute_petrace_dashboard(batches)
    foil1 = next(c for c in result['components'] if 'BL1' in c['name'])
    assert foil1['alert_level'] == 'YELLOW'

def test_foil_orange_when_pct_70_to_90():
    """70–90% → ORANGE. Real foil 3 is at ~81%."""
    muAh = FOIL_LIFE_MUAH * 0.80  # 80%
    batches = [_batch(foil_no=3, total_muAh=muAh)]
    result = compute_petrace_dashboard(batches)
    foil1 = next(c for c in result['components'] if 'BL1' in c['name'])
    assert foil1['alert_level'] == 'ORANGE'

def test_foil_red_when_pct_above_90():
    """Above 90% → RED."""
    muAh = FOIL_LIFE_MUAH * 0.92
    batches = [_batch(foil_no=3, total_muAh=muAh)]
    result = compute_petrace_dashboard(batches)
    foil1 = next(c for c in result['components'] if 'BL1' in c['name'])
    assert foil1['alert_level'] == 'RED'

def test_foil_pct_life_used_correct():
    muAh = FOIL_LIFE_MUAH * 0.40
    batches = [_batch(foil_no=3, total_muAh=muAh)]
    result = compute_petrace_dashboard(batches)
    foil1 = next(c for c in result['components'] if 'BL1' in c['name'])
    assert foil1['pct_life_used'] == pytest.approx(40.0, abs=1.0)

def test_foil_bl2_tracked_separately():
    """BL2 foil (foil_no=4) tracked independently of BL1."""
    # BL1 (foil 3): 80% life used; BL2 (foil 4): 20% life used
    batches = [
        _batch(foil_no=3, total_muAh=FOIL_LIFE_MUAH * 0.80),
        _batch(foil_no=4, total_muAh=FOIL_LIFE_MUAH * 0.20),
    ]
    result = compute_petrace_dashboard(batches)
    foil1 = next(c for c in result['components'] if 'BL1' in c['name'])
    foil2 = next(c for c in result['components'] if 'BL2' in c['name'])
    assert foil1['alert_level'] == 'ORANGE'
    assert foil2['alert_level'] == 'GREEN'

# ── Beam current ──────────────────────────────────────────────────────────────

def test_beam_green_when_above_70uA():
    batches = [_batch(peak_target_uA=84.0) for _ in range(5)]
    result = compute_petrace_dashboard(batches)
    beam = next(c for c in result['components'] if 'Beam' in c['name'] or 'Target' in c['name'])
    assert beam['alert_level'] == 'GREEN'

def test_beam_yellow_when_50_to_70uA():
    batches = [_batch(peak_target_uA=60.0) for _ in range(5)]
    result = compute_petrace_dashboard(batches)
    beam = next(c for c in result['components'] if 'Beam' in c['name'] or 'Target' in c['name'])
    assert beam['alert_level'] == 'YELLOW'

def test_beam_orange_when_30_to_50uA():
    batches = [_batch(peak_target_uA=40.0) for _ in range(5)]
    result = compute_petrace_dashboard(batches)
    beam = next(c for c in result['components'] if 'Beam' in c['name'] or 'Target' in c['name'])
    assert beam['alert_level'] == 'ORANGE'

def test_beam_red_when_below_30uA():
    batches = [_batch(peak_target_uA=15.0) for _ in range(5)]
    result = compute_petrace_dashboard(batches)
    beam = next(c for c in result['components'] if 'Beam' in c['name'] or 'Target' in c['name'])
    assert beam['alert_level'] == 'RED'

# ── RF system ─────────────────────────────────────────────────────────────────

def test_rf_green_when_above_97pct():
    batches = [_batch(rf_efficiency=0.980) for _ in range(5)]
    result = compute_petrace_dashboard(batches)
    rf = next(c for c in result['components'] if 'RF' in c['name'])
    assert rf['alert_level'] == 'GREEN'

def test_rf_yellow_when_95_to_97pct():
    batches = [_batch(rf_efficiency=0.960) for _ in range(5)]
    result = compute_petrace_dashboard(batches)
    rf = next(c for c in result['components'] if 'RF' in c['name'])
    assert rf['alert_level'] == 'YELLOW'

def test_rf_orange_when_90_to_95pct():
    batches = [_batch(rf_efficiency=0.920) for _ in range(5)]
    result = compute_petrace_dashboard(batches)
    rf = next(c for c in result['components'] if 'RF' in c['name'])
    assert rf['alert_level'] == 'ORANGE'

def test_rf_red_when_below_90pct():
    batches = [_batch(rf_efficiency=0.850) for _ in range(5)]
    result = compute_petrace_dashboard(batches)
    rf = next(c for c in result['components'] if 'RF' in c['name'])
    assert rf['alert_level'] == 'RED'

# ── Vacuum ────────────────────────────────────────────────────────────────────

def test_vacuum_green_when_below_3e5():
    batches = [_batch(peak_vacuum_P=1.0e-5) for _ in range(5)]
    result = compute_petrace_dashboard(batches)
    vac = next(c for c in result['components'] if 'Vacuum' in c['name'])
    assert vac['alert_level'] == 'GREEN'

def test_vacuum_yellow_when_3e5_to_1e4():
    batches = [_batch(peak_vacuum_P=5.0e-5) for _ in range(5)]
    result = compute_petrace_dashboard(batches)
    vac = next(c for c in result['components'] if 'Vacuum' in c['name'])
    assert vac['alert_level'] == 'YELLOW'

def test_vacuum_orange_when_1e4_to_5e4():
    batches = [_batch(peak_vacuum_P=2.0e-4) for _ in range(5)]
    result = compute_petrace_dashboard(batches)
    vac = next(c for c in result['components'] if 'Vacuum' in c['name'])
    assert vac['alert_level'] == 'ORANGE'

def test_vacuum_red_when_above_5e4():
    batches = [_batch(peak_vacuum_P=1.0e-3) for _ in range(5)]
    result = compute_petrace_dashboard(batches)
    vac = next(c for c in result['components'] if 'Vacuum' in c['name'])
    assert vac['alert_level'] == 'RED'

# ── Edge cases ────────────────────────────────────────────────────────────────

def test_empty_batches_returns_unknown_components():
    """No data → all components GREEN/YELLOW at worst, no crash."""
    result = compute_petrace_dashboard([])
    assert len(result['components']) == 5
    for comp in result['components']:
        assert comp['alert_level'] in {'RED', 'ORANGE', 'YELLOW', 'GREEN'}

def test_uses_only_real_batches_for_beam_and_rf():
    """Batches with row_count=0 must not pollute beam/RF averages."""
    real = _batch(peak_target_uA=84.0, rf_efficiency=0.980, row_count=100)
    empty = _batch(peak_target_uA=0.0, rf_efficiency=0.0, row_count=0)
    result = compute_petrace_dashboard([real, empty])
    beam = next(c for c in result['components'] if 'Beam' in c['name'] or 'Target' in c['name'])
    assert beam['alert_level'] == 'GREEN'
    rf = next(c for c in result['components'] if 'RF' in c['name'])
    assert rf['alert_level'] == 'GREEN'
