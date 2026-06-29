import hashlib
import pickle
import sqlite3
import numpy as np
from datetime import date, timedelta
from pathlib import Path
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.feature_selection import SelectKBest, f_classif, VarianceThreshold
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import precision_score, recall_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.utils.class_weight import compute_sample_weight


def _write_checksum(path: Path):
    sha256 = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    Path(path).with_suffix('.sha256').write_text(sha256)


POSITIVE_WINDOW = 10
# Gap between positive and negative zones — labels in 11-21 days before maintenance are ambiguous
NEGATIVE_THRESHOLD = 21
# Realistic gate for a dataset with 14-11 maintenance events per component
MIN_PRECISION = 0.25
MIN_RECALL = 0.5
COMPONENTS = ['ION SOURCE', 'FOILS', 'BL1 Target 1', 'BL2 Target 1']
FOILS_LABELS = ('BL1 Foil 1', 'BL1 Foil 2', 'BL1 Foil 3',
                 'BL2 Foil 1', 'BL2 Foil 2', 'BL2 Foil 3')


def _get_maint_dates(conn, component: str):
    """Return sorted list of maintenance dates, aggregating all foil labels for FOILS."""
    if component == 'FOILS':
        ph = ','.join('?' * len(FOILS_LABELS))
        rows = conn.execute(
            f"SELECT DISTINCT date(timestamp) FROM maintenance_events "
            f"WHERE component_label IN ({ph}) ORDER BY timestamp",
            list(FOILS_LABELS)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT DISTINCT date(timestamp) FROM maintenance_events "
            "WHERE component_label=? ORDER BY timestamp",
            [component]
        ).fetchall()
    return [date.fromisoformat(r[0]) for r in rows]


def build_training_data(component: str, db_path: str, features_fn):
    conn = sqlite3.connect(db_path, timeout=30)
    maint_dates = _get_maint_dates(conn, component)
    all_dates = conn.execute(
        "SELECT DISTINCT date FROM beam_daily ORDER BY date"
    ).fetchall()
    conn.close()

    if not maint_dates:
        return None, None, None, None, None

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
        if len(vals) > 0 and nan_count / len(vals) > 0.5:
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


def _make_pipeline(n_features: int = 72):
    k = min(10, max(3, n_features - 12))
    return Pipeline([
        ('imputer', SimpleImputer(strategy='median')),
        ('variance', VarianceThreshold()),
        ('selector', SelectKBest(f_classif, k=k)),
        ('gbm', GradientBoostingClassifier(
            n_estimators=50, max_depth=2, learning_rate=0.1, min_samples_leaf=5
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
    n = len(X)
    n_pos = int(np.sum(y == 1))
    n_neg = int(np.sum(y == 0))
    print(f"[{component}] Training data: {n} samples (pos={n_pos}, neg={n_neg})")

    # CV pass: collect metrics AND out-of-fold probabilities simultaneously.
    # Using OOF probs from all folds covers the full training timeline for calibration,
    # which is strictly better than holding out a fixed 30% tail.
    n_splits = min(3, max(2, n_pos // 5))
    tscv = TimeSeriesSplit(n_splits=n_splits)
    precisions, recalls = [], []
    oof_probs = np.zeros(n)
    oof_days  = np.zeros(n)
    oof_mask  = np.zeros(n, dtype=bool)

    for tr, te in tscv.split(X):
        if len(np.unique(y[tr])) < 2 or len(np.unique(y[te])) < 1:
            continue
        fold_pipeline = _make_pipeline(X.shape[1])
        w = compute_sample_weight('balanced', y[tr])
        fold_pipeline.fit(X[tr], y[tr], gbm__sample_weight=w)
        pred  = fold_pipeline.predict(X[te])
        probs = fold_pipeline.predict_proba(X[te])[:, 1]
        precisions.append(precision_score(y[te], pred, zero_division=0))
        recalls.append(recall_score(y[te], pred, zero_division=0))
        oof_probs[te] = probs
        oof_days[te]  = days_arr[te]
        oof_mask[te]  = True

    if not precisions:
        print(f"[{component}] CV failed — counter-only mode")
        return False

    avg_p, avg_r = np.mean(precisions), np.mean(recalls)
    print(f"[{component}] CV: precision={avg_p:.2f}, recall={avg_r:.2f}")

    low_confidence = avg_p < MIN_PRECISION or avg_r < MIN_RECALL
    if low_confidence:
        print(f"[{component}] Quality gate FAILED (need p>={MIN_PRECISION}, r>={MIN_RECALL}) — saving as low-confidence")

    # Calibrate isotonic regressor on all OOF probabilities.
    if oof_mask.sum() >= 5:
        iso = IsotonicRegression(increasing=False, out_of_bounds='clip')
        iso.fit(oof_probs[oof_mask], oof_days[oof_mask].astype(float))
    else:
        # Very few OOF samples (unusual) — fall back to the 70/30 split approach.
        print(f"[{component}] Warning: only {oof_mask.sum()} OOF samples; using 70/30 calibration fallback")
        cal_start = int(n * 0.7)
        tmp = _make_pipeline(X.shape[1])
        w_tmp = compute_sample_weight('balanced', y[:cal_start])
        tmp.fit(X[:cal_start], y[:cal_start], gbm__sample_weight=w_tmp)
        iso = IsotonicRegression(increasing=False, out_of_bounds='clip')
        iso.fit(tmp.predict_proba(X[cal_start:])[:, 1], days_arr[cal_start:].astype(float))

    # Train the production model on ALL available data.
    # The calibrator was fitted on OOF probabilities from fold-trained models —
    # this is the same trade-off CalibratedClassifierCV makes internally and is acceptable.
    pipeline = _make_pipeline(X.shape[1])
    w_all = compute_sample_weight('balanced', y)
    pipeline.fit(X, y, gbm__sample_weight=w_all)

    warning = (
        f"Low-confidence model — only {n_pos} positive training samples from limited maintenance history. "
        f"CV precision {avg_p:.0%} / recall {avg_r:.0%} "
        f"(minimum required: {MIN_PRECISION:.0%} / {MIN_RECALL:.0%}). "
        f"Calendar counter is the primary signal; ML adds supplementary pattern detection only."
    ) if low_confidence else None

    safe = component.lower().replace(' ', '_')
    model_dir = Path(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    model_pkl = model_dir / f'{safe}_model.pkl'
    cal_pkl   = model_dir / f'{safe}_days_calibrator.pkl'
    if model_pkl.resolve().parent != model_dir.resolve():
        raise ValueError(f"Unsafe model path for component '{component}'")
    with open(model_pkl, 'wb') as f:
        pickle.dump({
            'model': pipeline,
            'feature_names': feature_names,
            'meta': {
                'low_confidence': low_confidence,
                'cv_precision': round(float(avg_p), 3),
                'cv_recall': round(float(avg_r), 3),
                'n_pos': int(n_pos),
                'training_samples': n,
                'trained_at': date.today().isoformat(),
                'warning': warning,
            },
        }, f)
    _write_checksum(model_pkl)
    with open(cal_pkl, 'wb') as f:
        pickle.dump(iso, f)
    _write_checksum(cal_pkl)

    print(f"[{component}] Model saved to {model_dir}")
    return True
