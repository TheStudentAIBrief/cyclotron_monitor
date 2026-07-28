"""Property-based tests for the safety-critical fusion/override logic.

These functions decide whether a real maintenance alarm can be suppressed, so
their contracts must hold for ALL inputs, not just the hand-picked cases in
test_blend.py / test_predictor.py. Hypothesis fuzzes the input space to try to
break the invariants the code comments promise.

Covers the catalogue gaps: "no property-based/random testing" (UQ software-
testing fails) on exactly the code where an undetected edge case = a suppressed
alarm on a nuclear installation.
"""
from hypothesis import given, strategies as st
from models.blend import blend_days_and_risk, _DAYS_THRESHOLD
from models.predictor import (
    _alert_level, _apply_model_override,
    _OVERRIDE_MAX_RISK, _OVERRIDE_MIN_MODEL_DAYS,
)

days = st.floats(min_value=-10.0, max_value=500.0, allow_nan=False, allow_infinity=False)
risk = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
_SEV = {'GREEN': 0, 'YELLOW': 1, 'ORANGE': 2, 'RED': 3}


# ---- blend_days_and_risk ----
@given(days, risk, days, risk)
def test_blend_risk_never_suppressed(cd, cr, md, mr):
    """final_risk is always the max of the two — the detection-critical property."""
    fd, fr = blend_days_and_risk(cd, cr, md, mr)
    assert fr == max(cr, mr)


@given(days, risk, days, risk)
def test_blend_concern_preserved(cd, cr, md, mr):
    """If either signal is concerned (days<=14) the blended days stays concerned;
    if neither is, it does not. A concerned signal can never be smoothed away."""
    fd, _ = blend_days_and_risk(cd, cr, md, mr)
    either_concerned = min(cd, md) <= _DAYS_THRESHOLD
    assert (fd <= _DAYS_THRESHOLD) == either_concerned


@given(days, risk, days, risk)
def test_blend_days_bounded(cd, cr, md, mr):
    """Blended days never extrapolates above either input estimate, and is clamped
    at 0 (days-remaining is never negative)."""
    fd, _ = blend_days_and_risk(cd, cr, md, mr)
    assert 0.0 <= fd <= max(0.0, max(cd, md))
    if min(cd, md) >= 0.0:
        assert fd >= min(cd, md)


# ---- _apply_model_override ----
@given(days, risk, days, risk)
def test_override_only_downgrades(fd, fr, mr, md):
    """Contract: override can only INCREASE days and DECREASE risk — never the
    reverse. Holds for every input, so it can never manufacture or worsen urgency."""
    od, orisk, _ = _apply_model_override(fd, fr, mr, md, 'CAL')
    assert od >= fd - 1e-9
    assert orisk <= fr + 1e-9


@given(days, risk, days, risk)
def test_override_gated_on_confident_health(fd, fr, mr, md):
    """Override fires ONLY when the model is confidently healthy AND the calendar
    would have alarmed. A faulting model (risk>=0.25 or days<=28) is never
    downgraded — detection preserved."""
    od, orisk, sig = _apply_model_override(fd, fr, mr, md, 'CAL')
    fired = sig == 'MODEL_OVERRIDE'
    if fired:
        assert mr < _OVERRIDE_MAX_RISK and md > _OVERRIDE_MIN_MODEL_DAYS
        assert _alert_level(fd, risk=fr) in ('RED', 'ORANGE')
    else:
        # not fired -> inputs returned unchanged
        assert (od, orisk) == (fd, fr)


@given(days, risk, days, risk)
def test_override_never_downgrades_faulting_component(fd, fr, mr, md):
    """A component the model flags as faulty (risk at/above the gate) is untouched."""
    if mr >= _OVERRIDE_MAX_RISK:
        od, orisk, sig = _apply_model_override(fd, fr, mr, md, 'CAL')
        assert (od, orisk, sig) == (fd, fr, 'CAL')


# ---- _alert_level ----
@given(days, risk)
def test_alert_risk_only_escalates(d, r):
    """Supplying risk can only raise the alert level, never lower the days-based one."""
    assert _SEV[_alert_level(d, risk=r)] >= _SEV[_alert_level(d, risk=None)]


@given(days, days, risk)
def test_alert_monotonic_in_days(d1, d2, r):
    """Fewer days remaining is never LESS severe than more days."""
    lo, hi = min(d1, d2), max(d1, d2)
    assert _SEV[_alert_level(lo, risk=r)] >= _SEV[_alert_level(hi, risk=r)]
