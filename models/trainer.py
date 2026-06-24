import pickle
import sqlite3
import numpy as np
from datetime import date, timedelta
from pathlib import Path
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import precision_score, recall_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.utils.class_weight import compute_sample_weight

POSITIVE_WINDOW = 7
NEGATIVE_THRESHOLD = 14
MIN_PRECISION = 0.5
MIN_RECALL = 0.6
COMPONENTS = ['ION SOURCE', 'FOILS', 'BL1 Target 1', 'BL2 Target 1']


def build_training_data(component: str, db_path: str, features_fn):
    conn = sqlite3.connect(db_path)
    maint_rows = conn.execute(
        "SELECT date(timestamp) FROM maintenance_events WHERE component_label=? ORDER BY timestamp",
        [component]
    ).fetchall()
    all_dates = conn.execute(
        "SELECT DISTINCT date FROM beam_daily ORDER BY date"
    ).fetchall()
    conn.close()

    if not maint_rows:
        return None, None, None, None, None

    maint_dates = [date.fromisoformat(r[0]) for r in maint_rows]
    beam_dates = [date.fromisoformat(r[0]) for r in all_dates]

    X_rows, y, days_arr, dates_out = [], [], [], []

    for d in beam_dates:
        future = [m for m in maint_dates if m > d]
        if not future:
            continue
        nearest = min(future)
        days_until = (nearest - d).days
        if days_until <= POSITIVE_WINDOW:
            label = 1
        elif days_until > NEGATIVE_THRESHOLD:
            label = 0
        else:
            continue

        feats = features_fn(d, component, db_path)
        vals = list(feats.values())
        nan_count = sum(1 for v in vals if isinstance(v, float) and np.isnan(v))
        if len(vals) > 0 and nan_count / len(vals) > 0.3:
            continue

        X_rows.append(feats)
        y.append(label)
        days_arr.append(days_until)
        dates_out.append(d)

    if not X_rows or len(set(y)) < 2:
        return None, None, None, None, None

    import pandas as pd
    df = pd.DataFrame(X_rows)
    feature_names = list(df.columns)
    return df.values, np.array(y), np.array(days_arr), feature_names, dates_out


def _make_pipeline():
    return Pipeline([
        ('imputer', SimpleImputer(strategy='median')),
        ('gbm', GradientBoostingClassifier(
            n_estimators=100, max_depth=3, learning_rate=0.1, min_samples_leaf=3
        ))
    ])


def train_component(component: str, db_path: str, model_dir: str, features_fn=None) -> bool:
    from features.engineer import build_features as default_features
    if features_fn is None:
        features_fn = default_features

    result = build_training_data(component, db_path, features_fn)
    if result[0] is None:
        print(f"[{component}] No training data — counter-only mode")
        return False

    X, y, days_arr, feature_names, dates = result

    n_pos = int(np.sum(y == 1))
    n_splits = min(5, max(2, n_pos))
    tscv = TimeSeriesSplit(n_splits=n_splits)
    precisions, recalls = [], []
    for tr, te in tscv.split(X):
        if len(np.unique(y[tr])) < 2 or len(np.unique(y[te])) < 1:
            continue
        pipeline = _make_pipeline()
        w = compute_sample_weight('balanced', y[tr])
        pipeline.fit(X[tr], y[tr], gbm__sample_weight=w)
        pred = pipeline.predict(X[te])
        precisions.append(precision_score(y[te], pred, zero_division=0))
        recalls.append(recall_score(y[te], pred, zero_division=0))

    if not precisions:
        print(f"[{component}] CV failed — counter-only mode")
        return False

    avg_p, avg_r = np.mean(precisions), np.mean(recalls)
    print(f"[{component}] CV: precision={avg_p:.2f}, recall={avg_r:.2f}")

    if avg_p < MIN_PRECISION or avg_r < MIN_RECALL:
        print(f"[{component}] Quality gate FAILED — counter-only mode")
        return False

    n = len(X)
    cal_start = int(n * 0.7)
    pipeline = _make_pipeline()
    w = compute_sample_weight('balanced', y[:cal_start])
    pipeline.fit(X[:cal_start], y[:cal_start], gbm__sample_weight=w)

    calibrated = CalibratedClassifierCV(pipeline, cv='prefit', method='isotonic')
    calibrated.fit(X[cal_start:], y[cal_start:])

    probs = calibrated.predict_proba(X)[:, 1]
    iso = IsotonicRegression(increasing=False, out_of_bounds='clip')
    iso.fit(probs, days_arr.astype(float))

    safe = component.lower().replace(' ', '_')
    model_dir = Path(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    with open(model_dir / f'{safe}_model.pkl', 'wb') as f:
        pickle.dump({'model': calibrated, 'feature_names': feature_names}, f)
    with open(model_dir / f'{safe}_days_calibrator.pkl', 'wb') as f:
        pickle.dump(iso, f)

    print(f"[{component}] Model saved to {model_dir}")
    return True
