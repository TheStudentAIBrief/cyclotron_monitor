"""Generate ui/patterns.html — standalone pattern discovery report."""
import sqlite3
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import io
import base64
from datetime import date, datetime, timedelta

COMPONENTS = ['ION SOURCE', 'FOILS', 'BL1 Target 1', 'BL2 Target 1']
COMPONENT_PARAMS = {
    'ION SOURCE': ['AI_IS_CUR', 'AI_IS_VOLT', 'AI_BOP_CUR'],
    'FOILS': ['AI_BL1_FOIL_CUR', 'AI_BL2_FOIL_CUR'],
    'BL1 Target 1': ['AI_BL1_TARG_CUR', 'AI_BOP_CUR'],
    'BL2 Target 1': ['AI_BL2_TARG_CUR', 'AI_BOP_CUR'],
}


def _fig_to_b64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight', dpi=100)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def _plot_lifetime_hist(conn, component) -> str:
    rows = conn.execute(
        "SELECT date(timestamp) FROM maintenance_events WHERE component_label=? ORDER BY timestamp",
        [component]
    ).fetchall()
    if len(rows) < 2:
        return ''
    dates = [date.fromisoformat(r[0]) for r in rows]
    gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
    if not gaps:
        return ''
    fig, ax = plt.subplots(figsize=(6, 3))
    ax.hist(gaps, bins=min(10, len(gaps)), color='#3498db', edgecolor='white')
    ax.axvline(np.mean(gaps), color='#e74c3c', linestyle='--',
               label=f'Mean: {np.mean(gaps):.0f}d')
    ax.set_title(f'{component} — Lifetime Distribution', color='white')
    ax.set_xlabel('Days between replacements', color='white')
    ax.set_facecolor('#16213e')
    fig.patch.set_facecolor('#1a1a2e')
    ax.tick_params(colors='white')
    ax.xaxis.label.set_color('white')
    ax.legend(facecolor='#0f3460', labelcolor='white')
    return _fig_to_b64(fig)


def _plot_pre_maintenance_drift(conn, component, params) -> str:
    maint_rows = conn.execute(
        "SELECT date(timestamp) FROM maintenance_events WHERE component_label=? ORDER BY timestamp",
        [component]
    ).fetchall()
    if not maint_rows:
        return ''
    maint_dates = [date.fromisoformat(r[0]) for r in maint_rows]
    fig, axes = plt.subplots(1, len(params), figsize=(5 * len(params), 3))
    if len(params) == 1:
        axes = [axes]
    for ax, param in zip(axes, params):
        all_series = []
        for md in maint_dates:
            series = []
            for day_offset in range(-29, 1):
                d = (md + timedelta(days=day_offset)).isoformat()
                row = conn.execute(
                    "SELECT mean FROM beam_daily WHERE date=? AND param=?", [d, param]
                ).fetchone()
                series.append(row[0] if row else np.nan)
            all_series.append(series)
        arr = np.array(all_series, dtype=float)
        mean_series = np.nanmean(arr, axis=0)
        ax.plot(range(-29, 1), mean_series, color='#3498db')
        ax.axvline(0, color='#e74c3c', linestyle='--', alpha=0.7)
        ax.set_title(param, color='white', fontsize=9)
        ax.set_facecolor('#16213e')
        ax.tick_params(colors='white')
    fig.patch.set_facecolor('#1a1a2e')
    fig.suptitle(f'{component} — 30-Day Pre-Maintenance Drift', color='white')
    return _fig_to_b64(fig)


def generate_patterns(db_path: str, output_path: str):
    conn = sqlite3.connect(db_path)
    sections = []
    for comp in COMPONENTS:
        params = COMPONENT_PARAMS.get(comp, [])
        hist_b64 = _plot_lifetime_hist(conn, comp)
        drift_b64 = _plot_pre_maintenance_drift(conn, comp, params)
        img_html = ''
        if hist_b64:
            img_html += f'<img src="data:image/png;base64,{hist_b64}" style="max-width:600px">'
        if drift_b64:
            img_html += f'<img src="data:image/png;base64,{drift_b64}" style="max-width:100%">'
        sections.append(f'<h2>{comp}</h2>{img_html or "<p>Insufficient data for this component.</p>"}')
    conn.close()

    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<title>Cyclotron Pattern Report</title>
<style>body{{background:#1a1a2e;color:#eee;font-family:Arial,sans-serif;padding:20px}}
h1{{color:#e0e0e0}}h2{{color:#adb5bd;border-top:1px solid #444;padding-top:12px}}
img{{border-radius:6px;margin:6px}}</style></head><body>
<h1>Cyclotron Pattern Discovery Report</h1>
<p style="color:#888">Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
{''.join(sections)}</body></html>"""

    with open(output_path, 'w') as f:
        f.write(html)
    print(f"Pattern report written to {output_path}")
