import json
import numpy as np
from datetime import datetime, date
from pathlib import Path

AVG_CYCLES = {'ION SOURCE': 46, 'FOILS': 78, 'BL1 Target 1': 51, 'BL2 Target 1': 56}


def write_dashboard(predictions, dashboard_path: str, alert_path: str):
    components = []
    alert_lines = []

    for pred in predictions:
        avg = AVG_CYCLES.get(pred.component, 60)
        try:
            last = date.fromisoformat(pred.last_maintenance)
            days_used = (date.today() - last).days
            pct = min(100, max(0, int(100 * days_used / avg)))
        except Exception:
            pct = 0

        counter_val = pred.counter_days
        if isinstance(counter_val, float) and (np.isinf(counter_val) or np.isnan(counter_val)):
            counter_val = None

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

    with open(dashboard_path, 'w') as f:
        json.dump(dashboard, f, indent=2)

    alert_p = Path(alert_path)
    if alert_lines:
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M')
        alert_p.parent.mkdir(parents=True, exist_ok=True)
        alert_p.write_text(
            f"CYCLOTRON ALERT - {now_str}\n" + '\n'.join(alert_lines) + '\n'
        )
    elif alert_p.exists():
        alert_p.unlink()
