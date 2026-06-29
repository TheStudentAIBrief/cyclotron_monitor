import hashlib
import hmac
import logging
import pickle
import numpy as np
from dataclasses import dataclass, field
from pathlib import Path

_model_cache: dict[str, tuple[float, object]] = {}  # path → (mtime, deserialized obj)
_log = logging.getLogger('cyclotron.predictor')


def _load_verified(path: Path) -> object:
    """Load and deserialize a verified pickle, caching by file modification time.

    The cache is invalidated automatically when the file changes on disk (e.g.
    after a retrain).  SHA-256 is verified on every cache miss so integrity is
    always checked before deserializing, but the expensive disk read and
    deserialization are skipped on subsequent calls while the file is unchanged.
    """
    key = str(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None
    cached = _model_cache.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    obj = pickle.loads(_verify_checksum(path))
    _model_cache[key] = (mtime, obj)
    return obj


def _verify_checksum(path: Path) -> bytes:
    """Read path bytes, verify SHA-256 against sidecar, return bytes for immediate deserialization.

    Returning bytes and using pickle.loads() closes the TOCTOU window that exists when
    _verify_checksum() and pickle.load() each open the file independently.
    Error messages are opaque to prevent model path disclosure in forwarded log streams.
    """
    sha_path = path.with_suffix('.sha256')
    if not sha_path.exists():
        raise RuntimeError(
            "Model integrity file missing. Re-run 'python main.py train' to regenerate."
        )
    expected = sha_path.read_text().strip()
    data = path.read_bytes()  # read once — shared between hash verification and deserialization
    actual = hashlib.sha256(data).hexdigest()
    if not hmac.compare_digest(actual, expected):
        raise RuntimeError(
            "Model integrity check failed. File may have been tampered with. "
            "Re-run 'python main.py train'."
        )
    return data


@dataclass
class PredictionResult:
    component: str
    risk_score: float
    days_estimate: float
    alert_level: str
    primary_signal: str
    top_reasons: list
    last_maintenance: str
    counter_days: float
    warning: str = None
    trained_at: str = None


def _alert_level(days: float) -> str:
    if days <= 3:
        return 'RED'
    if days <= 7:
        return 'ORANGE'
    if days <= 14:
        return 'YELLOW'
    return 'GREEN'


def _reason(name: str, value) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return f"Signal: {name}"
    if 'IS_CUR' in name and 'slope' in name:
        return f"Ion source current trend: {value:+.4f}/day"
    if 'fault_is_10802' in name:
        return f"Ion source self-check failing {int(value)}x this period"
    if 'fault_is_10804' in name:
        return f"Ion source open-circuit warning {int(value)}x"
    if 'efficiency_ratio' in name:
        return f"Beam efficiency: {value:.2f} (beam out / source in)"
    if 'efficiency_slope' in name:
        return f"Beam efficiency trend: {value:+.4f}/day"
    if 'valve' in name:
        return f"BL2 pneumatic valve cycling {int(value)}x/week (normal: <42)"
    if 'fault_11001' in name:
        return f"Lifetime counter overrun warnings: {int(value)}x"
    if 'counter_days' in name:
        return f"Lifetime counter: ~{max(0, int(value))} days remaining"
    if '_slope' in name:
        param = name.split('_')[1]
        return f"{param} trend: {value:+.4f}/day"
    return f"{name.replace('_', ' ')}: {value:.3g}"


def predict(component: str, features: dict, model_dir: str,
            counter_days: float, last_maintenance: str) -> PredictionResult:
    safe = component.lower().replace(' ', '_')
    model_path = Path(model_dir) / f'{safe}_model.pkl'
    cal_path = Path(model_dir) / f'{safe}_days_calibrator.pkl'

    model_dir_resolved = Path(model_dir).resolve()
    if model_path.resolve().parent != model_dir_resolved:
        raise ValueError(f"Unsafe model path for component '{component}'")
    if cal_path.resolve().parent != model_dir_resolved:
        raise ValueError(f"Unsafe calibrator path for component '{component}'")

    counter_risk = max(0.0, min(1.0, (14.0 - counter_days) / 14.0))

    if not model_path.exists():
        if 'TRANSFER' in component.upper():
            no_model_warning = (
                "No digital sensor data available for this component. "
                "The cyclotron has no embedded sensors that track physical transfer line wear. "
                "Prediction is based solely on the 2025 paper PPM log — provide the 2026 PPM log to reset the counter."
            )
        else:
            no_model_warning = None
        days_est = max(0.0, counter_days)
        return PredictionResult(
            component=component,
            risk_score=round(counter_risk, 3),
            days_estimate=round(days_est, 1),
            alert_level=_alert_level(days_est),
            primary_signal='COUNTER_ONLY',
            top_reasons=[f"Lifetime counter: ~{int(days_est)} days remaining"],
            last_maintenance=last_maintenance or 'Unknown',
            counter_days=counter_days,
            warning=no_model_warning,
        )

    saved = _load_verified(model_path)
    model = saved['model']
    feature_names = saved['feature_names']
    meta = saved.get('meta', {})
    model_warning = meta.get('warning', None)
    model_trained_at = meta.get('trained_at', None)

    days_cal = _load_verified(cal_path)

    X = np.array([[features.get(n, np.nan) for n in feature_names]])
    model_risk = float(model.predict_proba(X)[0, 1])
    model_days = float(days_cal.predict([model_risk])[0])

    final_risk = max(counter_risk, model_risk)
    final_days = max(0.0, min(counter_days, model_days))

    if counter_risk > model_risk:
        signal = 'COUNTER'
    elif model_risk > counter_risk:
        signal = 'MODEL'
    else:
        signal = 'BOTH'

    try:
        gbm = model.named_steps['gbm']
        importances = gbm.feature_importances_
        # Map importances back through VarianceThreshold → SelectKBest to original names
        var_mask = model.named_steps['variance'].get_support()
        sel_mask = model.named_steps['selector'].get_support()
        var_indices = np.where(var_mask)[0]
        original_indices = var_indices[np.where(sel_mask)[0]]
        top_idx = np.argsort(importances)[::-1][:3]
        reasons = [_reason(feature_names[original_indices[i]], features.get(feature_names[original_indices[i]])) for i in top_idx]
    except Exception as _e:
        _log.warning('Feature importance mapping failed for %s: %s', component, _e)
        reasons = [f"Model risk score: {model_risk:.0%}"]

    return PredictionResult(
        component=component,
        risk_score=round(final_risk, 3),
        days_estimate=round(final_days, 1),
        alert_level=_alert_level(final_days),
        primary_signal=signal,
        top_reasons=reasons[:3],
        last_maintenance=last_maintenance or 'Unknown',
        counter_days=counter_days,
        warning=model_warning,
        trained_at=model_trained_at,
    )
