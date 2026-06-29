import json
import os
import tempfile
import numpy as np
from datetime import datetime, date
from pathlib import Path

# Empirical medians computed from maintenance_events history (updated 2026-06-26).
# Used only for the pct_life_used progress bar — counter.py computes the live
# projection dynamically and is the authoritative source for days_estimate.
AVG_CYCLES = {'ION SOURCE': 58, 'FOILS': 77, 'BL1 Target 1': 42, 'BL2 Target 1': 85, 'TRANSFER LINES': 35}


def write_dashboard(predictions, dashboard_path: str, alert_path: str):
    components = []
    alert_lines = []

    for pred in predictions:
        avg = AVG_CYCLES.get(pred.component, 60)
        c = pred.counter_days
        if c is None or (isinstance(c, float) and (np.isnan(c) or np.isinf(c))):
            pct = 0
        else:
            # Life used = fraction of avg cycle consumed; capped at 100% when overdue
            pct = min(100, max(0, int(100 * (avg - c) / avg)))

        counter_val = pred.counter_days
        if isinstance(counter_val, float) and (np.isinf(counter_val) or np.isnan(counter_val)):
            counter_val = None

        trained_at = getattr(pred, 'trained_at', None)
        model_age_days = None
        if trained_at:
            try:
                model_age_days = (date.today() - date.fromisoformat(str(trained_at))).days
            except (ValueError, TypeError):
                pass

        components.append({
            'name': pred.component,
            'risk_score': pred.risk_score,
            'days_estimate': pred.days_estimate,
            'alert_level': pred.alert_level,
            'pct_life_used': pct,
            'last_maintenance': pred.last_maintenance,
            'top_reasons': pred.top_reasons,
            'counter_days': counter_val,
            'primary_signal': pred.primary_signal,
            'warning': getattr(pred, 'warning', None),
            'trained_at': trained_at,
            'model_age_days': model_age_days,
        })

        if pred.alert_level in ('RED', 'ORANGE'):
            days = int(pred.days_estimate)
            if pred.alert_level == 'RED':
                action = "REPLACE NOW"
            else:
                action = f"SCHEDULE THIS WEEK (~{days} days remaining)"
            alert_lines.append(f"{pred.component}: {action}")

    dashboard = {
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'components': components,
    }

    # Atomic write: write to a temp file, then os.replace() to avoid serving partial JSON
    # if serve.py reads the file mid-write.
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=Path(dashboard_path).parent, suffix='.tmp', prefix='dashboard_'
    )
    try:
        with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
            json.dump(dashboard, f, indent=2)
        os.replace(tmp_path, dashboard_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    alert_p = Path(alert_path)
    # Refuse to write to a symlink — prevents an attacker-planted symlink from redirecting
    # alert writes to an arbitrary filesystem location.
    if alert_p.is_symlink():
        import logging
        logging.getLogger('cyclotron.dashboard').warning(
            'alert_path %r is a symlink; refusing to write alert file', alert_path
        )
        return
    if alert_lines:
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M')
        alert_p.parent.mkdir(parents=True, exist_ok=True)
        alert_p.write_text(
            f"CYCLOTRON ALERT - {now_str}\n" + '\n'.join(alert_lines) + '\n'
        )
    elif alert_p.exists():
        alert_p.unlink()
