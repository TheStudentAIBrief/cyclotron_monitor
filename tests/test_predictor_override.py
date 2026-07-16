"""Tests for the gated MODEL OVERRIDE (models/predictor._apply_model_override).

Empirical basis: the calendar counter drives ~all false alarms (it reads a component
as overdue whenever it runs past its average cycle). With fault-code history restored,
the ML model sees real pre-maintenance fault telemetry, so its confidence can tell
"just old" from "genuinely faulting". The override downgrades a calendar-driven
RED/ORANGE ONLY when the model is confidently healthy — validated on a leakage-
controlled backtest: false-alarm rate 61%->41%, level accuracy 53%->68%, loose
detection held at 96%, strict detection 81%->80%.

Safety contract (the reason each of these tests exists):
  - The override may only DOWNGRADE, never escalate.
  - It fires only when model_risk is low AND model_days is comfortably far.
  - A faulting component (high model_risk and/or low model_days) is NEVER downgraded,
    so detection is preserved. These tests pin that contract.
"""
from models.predictor import (
    _apply_model_override, _alert_level,
    _OVERRIDE_MAX_RISK, _OVERRIDE_MIN_MODEL_DAYS,
)


def test_override_downgrades_calendar_red_when_model_confidently_healthy():
    # Calendar says 0 days (RED, risk 1.0) but the model is confident-healthy.
    days, risk, signal = _apply_model_override(
        final_days=0.0, final_risk=1.0, model_risk=0.10, model_days=40.0, signal='COUNTER')
    assert signal == 'MODEL_OVERRIDE'
    assert days == 40.0
    assert risk == 0.10
    assert _alert_level(days, risk=risk) == 'GREEN'


def test_override_not_applied_when_model_risk_too_high():
    # Model is NOT confident-healthy -> a faulting component -> keep the alarm.
    days, risk, signal = _apply_model_override(
        final_days=0.0, final_risk=1.0, model_risk=_OVERRIDE_MAX_RISK + 0.01,
        model_days=40.0, signal='COUNTER')
    assert signal == 'COUNTER'
    assert days == 0.0 and risk == 1.0
    assert _alert_level(days, risk=risk) == 'RED'


def test_override_not_applied_when_model_days_too_near():
    # Model agrees maintenance is near -> keep the alarm.
    days, risk, signal = _apply_model_override(
        final_days=0.0, final_risk=1.0, model_risk=0.05,
        model_days=_OVERRIDE_MIN_MODEL_DAYS - 1, signal='COUNTER')
    assert signal == 'COUNTER'
    assert days == 0.0


def test_override_not_applied_when_not_alarming():
    # Already GREEN -> nothing to downgrade.
    days, risk, signal = _apply_model_override(
        final_days=30.0, final_risk=0.1, model_risk=0.05, model_days=40.0, signal='MODEL')
    assert signal == 'MODEL'
    assert days == 30.0 and risk == 0.1


def test_override_only_ever_downgrades_never_escalates():
    # Whatever the inputs, the override must not raise urgency (days can only go up,
    # risk can only go down).
    for fd, fr, mr, md in [(0.0, 1.0, 0.1, 40.0), (5.0, 0.9, 0.2, 35.0),
                            (2.0, 1.0, 0.24, 29.0)]:
        days, risk, signal = _apply_model_override(fd, fr, mr, md, 'COUNTER')
        assert days >= fd
        assert risk <= fr


def test_override_boundary_exactly_at_gate_does_not_fire():
    # Gates are strict inequalities; exactly-at-threshold must NOT downgrade.
    days, risk, signal = _apply_model_override(
        final_days=0.0, final_risk=1.0, model_risk=_OVERRIDE_MAX_RISK,
        model_days=_OVERRIDE_MIN_MODEL_DAYS, signal='COUNTER')
    assert signal == 'COUNTER'
    assert days == 0.0
