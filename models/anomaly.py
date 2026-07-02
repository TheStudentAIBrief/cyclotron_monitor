"""Unsupervised operational-anomaly scoring, independent of the supervised
GradientBoostingClassifier in trainer.py/predictor.py.

Why this exists: the supervised models need labeled maintenance events, and
most components have only 1-14 of those in the entire history — too few to
train or validate a classifier at all for the sparsest ones. An IsolationForest
needs no labels: it learns what "normal" looks like for a component from its
own beam-parameter history, and scores how unusual the current reading is
relative to that baseline. This gives every monitored component a signal,
including ones the supervised path can never cover.
"""
from datetime import date, timedelta

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer

from features.engineer import COMPONENT_PARAMS, build_features

_MIN_HISTORY = 30  # fewer historical points than this -> not enough to fit a meaningful forest
_LOOKBACK_DAYS = 180
_NEUTRAL_SCORE = 0.5


def score_anomaly(historical_features: list, current_features: dict) -> float:
    """Fit an IsolationForest on historical_features and score current_features
    against it. Returns a score in [0, 1] where higher = more anomalous
    relative to the historical distribution. Returns 0.5 (neutral - not enough
    history to say anything) when historical_features has fewer than
    _MIN_HISTORY rows.
    """
    if len(historical_features) < _MIN_HISTORY:
        return _NEUTRAL_SCORE

    feature_names = sorted(historical_features[0].keys())
    X_hist = np.array([[row.get(n, np.nan) for n in feature_names] for row in historical_features])
    x_cur = np.array([[current_features.get(n, np.nan) for n in feature_names]])

    # Drop columns that are entirely NaN across the historical set explicitly
    # (e.g. a param this component doesn't track, or a feature with no data
    # in this window) - SimpleImputer would silently drop these anyway and
    # warn about it; doing it ourselves avoids the warning and makes the
    # behavior an intentional decision rather than an implicit side effect.
    has_data = ~np.all(np.isnan(X_hist), axis=0)
    if not has_data.any():
        return _NEUTRAL_SCORE
    X_hist = X_hist[:, has_data]
    x_cur = x_cur[:, has_data]

    imputer = SimpleImputer(strategy='mean')
    X_hist_imputed = imputer.fit_transform(X_hist)
    x_cur_imputed = imputer.transform(x_cur)

    forest = IsolationForest(n_estimators=100, contamination='auto', random_state=42)
    forest.fit(X_hist_imputed)

    # decision_function: higher = more normal, lower/negative = more anomalous.
    # Convert to a [0, 1] anomaly score via a sigmoid centered on the historical
    # median, scaled by the historical spread. A min-max normalization against
    # the historical range saturates at 1.0 for any point beyond that range —
    # indistinguishable "very anomalous" from "extremely anomalous" — whereas
    # the sigmoid keeps monotonically increasing (if only by a shrinking
    # amount) for arbitrarily extreme inputs, preserving relative ranking.
    hist_scores = forest.decision_function(X_hist_imputed)
    cur_score = forest.decision_function(x_cur_imputed)[0]

    median_hist = np.median(hist_scores)
    spread = np.std(hist_scores)
    if spread < 1e-9:
        return _NEUTRAL_SCORE
    z = (median_hist - cur_score) / spread  # positive when cur_score is more anomalous than typical
    normalized = 1.0 / (1.0 + np.exp(-z))
    return float(np.clip(normalized, 0.0, 1.0))


def compute_anomaly_score(component: str, target_date: date, db_path: str,
                          lookback_days: int = _LOOKBACK_DAYS) -> float:
    """As-of-target_date anomaly score for a component: builds a history of
    daily feature vectors strictly before target_date, fits an IsolationForest
    on that history, and scores target_date's own features against it. Never
    reads data dated on/after target_date - safe for walk-forward backtesting.
    """
    if component not in COMPONENT_PARAMS:
        return _NEUTRAL_SCORE

    historical_features = []
    d = target_date - timedelta(days=lookback_days)
    while d < target_date:
        feats = build_features(d, component, db_path)
        # Only count days where the component actually had readings (mean
        # exists for at least one tracked param) - skip down-days entirely
        # rather than polluting the baseline with all-NaN rows.
        if any(not np.isnan(feats.get(f'{p}_14d_mean', np.nan)) for p in COMPONENT_PARAMS[component]):
            historical_features.append(feats)
        d += timedelta(days=1)

    current_features = build_features(target_date, component, db_path)
    return score_anomaly(historical_features, current_features)
