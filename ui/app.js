'use strict';

// Escape HTML special characters before inserting text into innerHTML contexts.
function escHtml(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// Validate alert_level and primary_signal before using as CSS class names.
var _VALID_LEVELS  = new Set(['GREEN', 'YELLOW', 'ORANGE', 'RED']);
var _VALID_SIGNALS = new Set(['COUNTER', 'MODEL', 'BOTH', 'COUNTER_ONLY']);

var COLORS = { GREEN: '#2ecc71', YELLOW: '#f39c12', ORANGE: '#e67e22', RED: '#e74c3c' };
var ICONS  = { GREEN: '&#10003;', YELLOW: '&#9888;', ORANGE: '&#9670;', RED: '&#9888;' };
var LABELS = {
  GREEN:  'All good — no action needed',
  YELLOW: 'Plan maintenance soon',
  ORANGE: 'Schedule this week',
  RED:    'Replace now',
};
var SIGNAL_LABELS = {
  COUNTER:      'COUNTER',
  MODEL:        'ML MODEL',
  BOTH:         'COUNTER + ML',
  COUNTER_ONLY: 'COUNTER ONLY',
};

// ── Info popup content ────────────────────────────────────────────────────────
// Keys are whitelisted — only values in this table can be shown.
// textContent is used for rendering so these strings never enter innerHTML.
var INFO = {
  'signal.COUNTER': {
    title: 'Signal: Counter',
    body: 'The hardware µAh lifetime counter is the primary predictor. Burn rate is calculated from the most recent 30 days of 11001 overrun warning events using actual elapsed wall-clock time between the first and last warning. Remaining days = (9999 − current_reading) ÷ daily_burn_rate.',
    source: 'Source: event code 11001 (lifetime counter overrun) in hyperion .log files'
  },
  'signal.MODEL': {
    title: 'Signal: ML Model',
    body: 'The Gradient Boosting machine learning model is the primary predictor — it estimates a shorter time to maintenance than the hardware counter. The model is trained on historical beam parameters (current, voltage, efficiency trends) and fault event counts. Its raw probability is converted to days via an isotonic regression calibrator fitted on cross-validation out-of-fold predictions.',
    source: 'Source: GradientBoostingClassifier + IsotonicRegression, retrained on the full historical dataset'
  },
  'signal.BOTH': {
    title: 'Signal: Counter + ML Model',
    body: 'Both the hardware counter and the ML model agree closely on the risk level. The system always uses the most pessimistic of the two signals: days_estimate = min(counter_days, model_days) and risk_score = max(counter_risk, model_risk).',
    source: 'Source: µAh lifetime counter (11001 events) fused with GradientBoostingClassifier output'
  },
  'signal.COUNTER_ONLY': {
    title: 'Signal: Counter Only',
    body: 'No ML model is available for this component. The prediction uses either: (1) the hardware µAh lifetime counter projection if 11001 overrun warnings are present in the logs, or (2) the calendar fallback: avg_cycle_days − days_since_last_maintenance, using the lower-median of historical inter-maintenance intervals.',
    source: 'Source: 11001 events in hyperion log, or maintenance_events table (calendar fallback)'
  },
  'level.RED': {
    title: 'Alert Level: RED — Replace Now',
    body: 'Fewer than 3 days are estimated until maintenance is required. Immediate action is needed. Inspect the component and arrange replacement as soon as possible to avoid an unplanned beam outage.',
    source: 'Threshold: days_estimate ≤ 3'
  },
  'level.ORANGE': {
    title: 'Alert Level: ORANGE — Schedule This Week',
    body: 'Estimated 4–7 days until maintenance is required. A replacement should be scheduled and parts ordered within this week.',
    source: 'Threshold: 3 < days_estimate ≤ 7'
  },
  'level.YELLOW': {
    title: 'Alert Level: YELLOW — Plan Maintenance',
    body: 'Estimated 8–14 days until maintenance is required. No immediate action is needed, but maintenance should be planned and scheduled in the near future.',
    source: 'Threshold: 7 < days_estimate ≤ 14'
  },
  'level.GREEN': {
    title: 'Alert Level: GREEN — All Good',
    body: 'More than 14 days are estimated until maintenance is required. The component is operating within its normal service window.',
    source: 'Threshold: days_estimate > 14'
  },
  'meta.life_pct': {
    title: 'Life Used (%)',
    body: 'Percentage of the component\'s average service life that has been consumed. Calculated as: (avg_cycle − counter_days) ÷ avg_cycle × 100, capped at 100% when overdue. This is a visual indicator only — the days estimate and alert level are the authoritative prediction.',
    source: 'Source: empirical median cycle lengths derived from the maintenance_events history in the database'
  },
  'meta.days_remaining': {
    title: 'Days Remaining',
    body: 'Estimated days until this component requires maintenance. Computed as the minimum of: (1) the hardware µAh counter projection, and (2) the ML model\'s calibrated days estimate. Taking the minimum means the system is always conservative — the tighter of the two signals wins.',
    source: 'Formula: min(counter_days, model_days). Counter: 11001 events. Model: GradientBoostingClassifier → IsotonicRegression'
  },
  'meta.last_maint': {
    title: 'Last Replacement Date',
    body: 'Date of the most recent maintenance event recorded for this component. Used to compute the calendar-based days estimate (days_since = today − last_replacement) and as input to the ML model features.',
    source: 'Source: maintenance_events table, parsed from PPM entries in hyperion .log files'
  },
  'model.counter_days': {
    title: 'Counter Days (secondary signal)',
    body: 'The hardware µAh lifetime counter projects this many days remaining. The ML model is currently giving a shorter (more pessimistic) estimate and is overriding it. The counter value is shown here for context so you can judge whether the ML alarm is plausible.',
    source: 'Source: 11001 lifetime counter overrun events, 30-day burn rate window'
  },
  'model.trained_at': {
    title: 'Model Training Date',
    body: 'The date on which the ML model was last retrained on the full historical dataset. Models should be retrained periodically as new maintenance events are logged to keep predictions accurate. Retrain with: python main.py train',
    source: 'Source: model .pkl metadata field (written at training time by trainer.py)'
  },
  'model.risk': {
    title: 'ML Risk Score',
    body: 'The Gradient Boosting model\'s calibrated probability that maintenance will be required within the next 10 days, expressed as a percentage. Calibrated via isotonic regression on cross-validation out-of-fold predictions to be more reliable than raw classifier output. Values near 100% indicate high model confidence that maintenance is imminent.',
    source: 'Source: GradientBoostingClassifier.predict_proba() → IsotonicRegression calibrator'
  },
};

// ── Popup show/hide ───────────────────────────────────────────────────────────
var _popup = document.getElementById('info-popup');

function _showInfo(key) {
  var info = INFO[key];
  if (!info) return;
  // Use textContent throughout — INFO values are trusted constants, but belt-and-suspenders.
  document.getElementById('popup-title').textContent  = info.title;
  document.getElementById('popup-body').textContent   = info.body;
  document.getElementById('popup-source').textContent = info.source;
  _popup.style.display = 'flex';
}

function _hideInfo() {
  _popup.style.display = 'none';
}

document.getElementById('popup-close').addEventListener('click', _hideInfo);

// Click outside the box closes it.
_popup.addEventListener('click', function(e) {
  if (e.target === _popup) _hideInfo();
});

// Escape key closes it.
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') _hideInfo();
});

// Event delegation on #cards so it works on dynamically rendered cards.
document.getElementById('cards').addEventListener('click', function(e) {
  var el = e.target;
  while (el && el !== this) {
    if (el.getAttribute && el.getAttribute('data-info')) {
      _showInfo(el.getAttribute('data-info'));
      return;
    }
    el = el.parentNode;
  }
});

// ── Render ────────────────────────────────────────────────────────────────────
function render(data) {
  document.getElementById('updated').textContent =
    'Last updated: ' + new Date(data.generated_at).toLocaleString();

  var ageMs = Date.now() - new Date(data.generated_at).getTime();
  var banner = document.getElementById('stale-banner');
  if (ageMs > 2 * 60 * 60 * 1000) {
    var hours = Math.round(ageMs / 3600000);
    banner.style.display = 'block';
    banner.textContent =
      '⚠ Dashboard is ' + hours + ' hour(s) old — monitoring process may not be running. ' +
      'Run: python main.py predict';
  } else {
    banner.style.display = 'none';
    banner.textContent = '';
  }

  var cards = (data.components || []).map(function(c) {
    // Clamp pct to [0, 100] — guards against CSS injection via style attribute.
    var pct = Number(c.pct_life_used);
    if (!isFinite(pct) || pct < 0) { pct = 0; }
    if (pct > 100) { pct = 100; }

    // Validate level and signal against whitelists before using as CSS class names or info keys.
    var level = _VALID_LEVELS.has(c.alert_level) ? c.alert_level : 'GREEN';
    var sig   = _VALID_SIGNALS.has(c.primary_signal) ? c.primary_signal : 'COUNTER_ONLY';
    var color = COLORS[level] || '#888';
    var icon  = ICONS[level]  || '';
    var label = LABELS[level] || '';

    var daysEst = Number(c.days_estimate) || 0;
    var daysText = daysEst <= 0
      ? 'Overdue by ' + Math.abs(Math.round(daysEst)) + ' day(s)'
      : '~' + Math.round(daysEst) + ' day' + (Math.abs(daysEst) === 1 ? '' : 's') + ' remaining';

    // Signal badge — click to explain the signal source.
    var signalBadge = '<span class="signal-badge info-tip ' + sig + '" data-info="signal.' + sig + '">'
      + (SIGNAL_LABELS[sig] || sig) + '</span>';

    // Status badge — click to explain the alert threshold.
    var statusBadge = '<span class="status info-tip ' + level + '" data-info="level.' + level + '">'
      + icon + ' ' + label + '</span>';

    // Model-meta line — shown for ML-backed components.
    var modelMetaHtml = '';
    if (sig !== 'COUNTER_ONLY') {
      var parts = [];
      if (sig === 'MODEL' && c.counter_days != null && isFinite(Number(c.counter_days))) {
        var cRounded = Math.round(Number(c.counter_days));
        var eRounded = Math.round(daysEst);
        if (cRounded !== eRounded) {
          parts.push('<span class="info-tip" data-info="model.counter_days">Counter: ~' + cRounded + 'd</span>');
        }
      }
      if (c.trained_at) {
        var trainedLabel = 'Trained: ' + escHtml(String(c.trained_at));
        if (c.model_age_days != null && c.model_age_days > 60) {
          trainedLabel = '<span class="stale-model-badge">stale (' + c.model_age_days + 'd)</span> ' + trainedLabel;
        }
        parts.push('<span class="info-tip" data-info="model.trained_at">' + trainedLabel + '</span>');
      }
      var riskPct = Math.round((Number(c.risk_score) || 0) * 100);
      parts.push('<span class="info-tip" data-info="model.risk">Risk: ' + riskPct + '%</span>');
      modelMetaHtml = '<div class="model-meta">' + parts.join(' &nbsp;&#183;&nbsp; ') + '</div>';
    }

    // Escape all dynamic data before inserting into innerHTML.
    var reasons = (c.top_reasons || [])
      .map(function(r) { return '<li>' + escHtml(r) + '</li>'; })
      .join('');

    var warningHtml = c.warning
      ? '<div class="accuracy-note"><strong>&#9888; Accuracy Note</strong>' + escHtml(c.warning) + '</div>'
      : '';

    return '<div class="card">' +
      '<div class="card-header">' +
        '<span class="comp-name">' + escHtml(c.name) + signalBadge + '</span>' +
        statusBadge +
      '</div>' +
      '<div class="bar-track">' +
        '<div class="bar-fill" style="width:' + pct + '%;background:' + color + '"></div>' +
      '</div>' +
      '<div class="meta">' +
        '<span class="info-tip" data-info="meta.life_pct">Life used: ' + pct + '%</span>' +
        ' &nbsp;|&nbsp; ' +
        '<span class="info-tip" data-info="meta.days_remaining">' + escHtml(daysText) + '</span>' +
        ' &nbsp;|&nbsp; ' +
        '<span class="info-tip" data-info="meta.last_maint">Last replacement: ' + escHtml(String(c.last_maintenance || 'Unknown')) + '</span>' +
      '</div>' +
      modelMetaHtml +
      (reasons ? '<ul class="reasons">' + reasons + '</ul>' : '') +
      warningHtml +
      '</div>';
  }).join('');

  document.getElementById('cards').innerHTML = cards;
}

function load() {
  fetch('/api/dashboard.json?_=' + Date.now())
    .then(function(r) {
      if (!r.ok) { throw new Error('HTTP ' + r.status); }
      return r.json();
    })
    .then(render)
    .catch(function() {
      var el = document.getElementById('cards');
      el.textContent = '';
      var div = document.createElement('div');
      div.className = 'error';
      div.textContent = 'Could not load dashboard data. Run: python main.py predict';
      el.appendChild(div);
    });
}

load();
setInterval(load, 60000);
