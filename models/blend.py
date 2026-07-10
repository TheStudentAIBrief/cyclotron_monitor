"""Asymmetric fusion of the counter-based and ML-model-based signals.

Replaces predictor.py's original final_risk=max(...)/final_days=max(0,min(...))
rule with something that preserves the same detection-critical property (if
EITHER signal is concerned, the combined output stays concerned - never
suppressed) while refining the numeric days-estimate when both signals
already agree, instead of blindly taking the more extreme of the two.

Why not a symmetric weighted average: tested and rejected (see the 2026-07-02
model-accuracy experiments) - averaging smooths out an early confident alarm
from one signal even when the other disagrees, which collapsed detection from
76% to 24% in a real backtest. This design keeps the "either signal can raise
urgency" property that gives good detection, and only touches precision in the
unambiguous case where both signals already point the same direction.

Why "concerned" is defined on DAYS (<=14), not on the risk score: an earlier
version of this function used counter_risk/model_risk >= 0.5 as the "concerned"
gate. counter_risk's formula (max(0, min(1, (14-days)/14))) only reaches 0.5 at
7 days - so every real case between 7 and 14 days (already meaningfully urgent,
already YELLOW-or-worse by predictor._alert_level's own cutoff) fell into
"neither concerned" and got pushed to the calmer estimate. Measured effect on
the real backtest: detection regressed 76%->70% (strict 50%->39%) and false
alarms got WORSE (62%->75%) - the opposite of the intent. Switching the gate to
days<=14 (matching _alert_level's own YELLOW threshold exactly, not a
re-derived risk cutoff) fixes this.
"""

_DAYS_THRESHOLD = 14.0  # matches predictor._alert_level's YELLOW-or-worse cutoff


def blend_days_and_risk(counter_days: float, counter_risk: float,
                        model_days: float, model_risk: float) -> tuple:
    """Return (final_days, final_risk).

    final_risk is always max(counter_risk, model_risk) - never suppressed
    below what either signal alone reports.

    final_days:
    - only counter_days <= 14: counter_days (trust the concerned signal)
    - only model_days <= 14: model_days (trust the concerned signal)
    - both <= 14: average of the two (both agree something's wrong, refine
      the exact number)
    - neither <= 14: the SMALLER (more urgent) of the two, same as the
      original min() rule - preserves detection sensitivity even among
      comfortable numbers, since the smaller one is the more informative one
      to report (closer to becoming actionable).
    """
    final_risk = max(counter_risk, model_risk)

    counter_concerned = counter_days <= _DAYS_THRESHOLD
    model_concerned = model_days <= _DAYS_THRESHOLD

    if counter_concerned and model_concerned:
        final_days = (counter_days + model_days) / 2.0
    elif counter_concerned:
        final_days = counter_days
    elif model_concerned:
        final_days = model_days
    else:
        final_days = min(counter_days, model_days)

    return max(0.0, final_days), final_risk
