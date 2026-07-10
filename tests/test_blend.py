"""TDD for models/blend.py: an asymmetric counter/model fusion rule.

Design rationale (from the earlier weighted-blend experiment): a symmetric
weighted average kills detection (76% -> 24%) because it smooths out an early
confident alarm from one signal even when the other disagrees. The original
max()/min() rule preserves detection but is imprecise. This blend keeps the
"either signal can trigger urgency" property that gives good detection, and
only refines the numeric days-estimate (for more precision) in the case where
BOTH signals already agree something's wrong - never suppressing an alarm
either signal alone would have raised.
"""
from models.blend import blend_days_and_risk

# "Concerned" is defined on DAYS directly (<=14, matching predictor._alert_level's
# YELLOW-or-worse cutoff exactly), not on the risk score. counter_risk's formula
# (max(0, min(1, (14-days)/14))) is 0.5 at only 7 days - using a risk threshold
# meant every case between 7 and 14 days fell into "neither concerned" and got
# suppressed to the calmer estimate, which regressed detection in the real
# backtest (76%->70%) instead of preserving it. Days-based avoids that.
_DAYS_THRESHOLD = 14.0


def test_only_counter_concerned_preserves_counter_days():
    # Counter says urgent (7d, <=14), model says calm (60d) - must not average
    # these away into a falsely-comfortable number; trust the concerned signal.
    days, risk = blend_days_and_risk(counter_days=7.0, counter_risk=0.5, model_days=60.0, model_risk=0.1)
    assert days == 7.0
    assert risk == 0.5


def test_only_model_concerned_preserves_model_days():
    days, risk = blend_days_and_risk(counter_days=60.0, counter_risk=0.1, model_days=5.0, model_risk=0.64)
    assert days == 5.0
    assert risk == 0.64


def test_high_model_risk_alone_counts_as_concerned_even_if_calibrated_days_is_high():
    # Empirically validated (2026-07-03 standalone model eval, real backtest data):
    # model_risk has strong standalone discriminative power (Spearman rho up to
    # -0.82 vs actual days-until-maintenance, p<0.005) that the isotonic days-
    # calibrator's averaged point-estimate doesn't fully carry through - the
    # calibrator is honest but conservative (it reports the TYPICAL days for a
    # risk bucket, which is often >14 even when that bucket's events cluster
    # closer than average). A high raw risk score (>=0.8, where standalone
    # precision for "event within 30d" was 85-95%) should count as concerned on
    # its own, not be masked by a calibrated days number that's merely elevated.
    days, risk = blend_days_and_risk(counter_days=60.0, counter_risk=0.1, model_days=30.0, model_risk=0.85)
    assert days == 30.0  # trusts model_days once model_risk alone marks it concerned
    assert risk == 0.85


def test_both_concerned_averages_for_precision():
    # Both signals independently say "something's wrong" (both <=14 days) -
    # refine the exact number by averaging rather than blindly taking the
    # more extreme one.
    days, risk = blend_days_and_risk(counter_days=6.0, counter_risk=0.57, model_days=10.0, model_risk=0.6)
    assert days == 8.0  # (6+10)/2
    assert risk == 0.6  # still max - risk score itself stays the "most alarming", only days is refined


def test_neither_concerned_takes_the_more_urgent_estimate():
    # Both signals agree things are comfortably calm (>14 days) - still take
    # the more urgent (smaller) of the two, matching the original min() rule's
    # detection-preserving behavior. There is no "safe to relax" case: even
    # among calm numbers, the smaller one is the more informative one to
    # report, since it's closer to becoming actionable.
    days, risk = blend_days_and_risk(counter_days=40.0, counter_risk=0.1, model_days=55.0, model_risk=0.05)
    assert days == 40.0
    assert risk == 0.1


def test_risk_score_is_always_the_max_never_suppressed():
    # The risk score (which drives whether an alert is "concerning" at all in
    # other parts of the app) must never be suppressed below what either
    # signal alone reports - this is the core detection-preserving invariant.
    for counter_risk, model_risk in [(0.9, 0.1), (0.1, 0.9), (0.5, 0.5), (0.0, 0.0), (1.0, 1.0)]:
        _, risk = blend_days_and_risk(counter_days=10.0, counter_risk=counter_risk,
                                       model_days=10.0, model_risk=model_risk)
        assert risk == max(counter_risk, model_risk)


def test_days_never_goes_negative():
    days, _ = blend_days_and_risk(counter_days=-5.0, counter_risk=0.9, model_days=-2.0, model_risk=0.9)
    assert days >= 0.0


def test_boundary_at_days_threshold_is_inclusive_on_concerned_side():
    # A signal at exactly 14 days counts as "concerned" (matches
    # predictor._alert_level's <= semantics elsewhere in this codebase).
    days, risk = blend_days_and_risk(counter_days=14.0, counter_risk=0.3, model_days=14.0, model_risk=0.2)
    # Both at exactly 14 days -> both concerned -> averaged (trivially 14.0 here).
    assert days == 14.0
