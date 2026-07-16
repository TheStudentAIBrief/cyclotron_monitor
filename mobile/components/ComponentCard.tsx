import { View, Text, StyleSheet } from 'react-native';
import { ComponentData } from '../services/api';
import AlertBadge from './AlertBadge';

const LEVEL_BAR: Record<string, string> = {
  RED: '#e74c3c',
  ORANGE: '#e67e22',
  YELLOW: '#f39c12',
  GREEN: '#2ecc71',
};

// Plain-English confidence bands for the model's own risk score, so the operator
// reads how sure the model is — not a bare probability. Highest band first.
const MODEL_CONF = [
  { min: 0.7, label: 'high concern', color: '#e74c3c' },
  { min: 0.4, label: 'elevated', color: '#e67e22' },
  { min: 0.25, label: 'watch', color: '#f39c12' },
  { min: 0, label: 'confident healthy', color: '#2ecc71' },
];
const modelConfidence = (risk: number) =>
  MODEL_CONF.find((c) => risk >= c.min) ?? MODEL_CONF[MODEL_CONF.length - 1];

export default function ComponentCard({ data }: { data: ComponentData }) {
  const barColor = LEVEL_BAR[data.alert_level] ?? '#2ecc71';
  const pct = Math.min(100, Math.max(0, data.pct_life_used ?? 0));
  const isPerformance = data.component_type === 'performance';

  // Performance components: bar grows as the metric degrades (small sliver = healthy, full = at threshold)
  const displayPct = isPerformance ? Math.max(4, pct) : pct;
  const barLabel = isPerformance
    ? `${pct.toFixed(0)}% toward threshold${data.risk_score != null ? ` · risk ${(data.risk_score * 100).toFixed(0)}%` : ''}`
    : `${pct.toFixed(0)}% of service interval used${data.risk_score != null ? ` · risk ${(data.risk_score * 100).toFixed(0)}%` : ''}`;

  const daysLabel = (() => {
    if (data.days_estimate != null) return `${data.days_estimate.toFixed(1)} d`;
    if (data.alert_level === 'GREEN') return 'Stable';
    return 'No trend data';
  })();

  // The model's own read (independent of the calendar counter), shown so the
  // operator can see how close and how confident the model is behind the alert.
  const hasModelRead = data.model_days_estimate != null && data.model_risk != null;
  const conf = hasModelRead ? modelConfidence(data.model_risk as number) : null;
  const isOverride = data.primary_signal === 'MODEL_OVERRIDE';

  return (
    <View style={[styles.card, { borderLeftColor: barColor }]}>
      {/* Header: name + alert badge */}
      <View style={styles.header}>
        <Text style={styles.name}>{data.name}</Text>
        <AlertBadge level={data.alert_level} />
      </View>

      {/* Days remaining */}
      <View style={styles.row}>
        <Text style={styles.label}>{isPerformance ? 'Days to threshold' : 'Days remaining'}</Text>
        <Text style={[styles.value, { color: data.days_estimate != null ? barColor : '#666' }]}>
          {daysLabel}
        </Text>
      </View>

      {/* Life used / health progress bar */}
      <View style={styles.barBg}>
        <View style={[styles.barFill, { width: `${displayPct}%`, backgroundColor: barColor }]} />
      </View>
      <Text style={styles.barLabel}>{barLabel}</Text>

      {/* Last maintenance */}
      <View style={styles.row}>
        <Text style={styles.label}>Last service</Text>
        <Text style={styles.value}>
          {data.last_maintenance ? data.last_maintenance.slice(0, 10) : 'Unknown'}
        </Text>
      </View>

      {/* Signal type */}
      <View style={styles.row}>
        <Text style={styles.label}>Signal</Text>
        <View style={styles.signalPill}>
          <Text style={styles.signalText}>{data.primary_signal ?? '—'}</Text>
        </View>
      </View>

      {/* Model's own read — how close and how confident the ML model is */}
      {hasModelRead && conf && (
        <View style={styles.row}>
          <Text style={styles.label}>Model read</Text>
          <Text style={[styles.value, { color: conf.color }]}>
            ~{Math.round(data.model_days_estimate as number)} d · {conf.label}
          </Text>
        </View>
      )}

      {/* Model stood the calendar alarm down */}
      {isOverride && (
        <View style={styles.overrideBox}>
          <Text style={styles.overrideText}>
            Model stood down the calendar alarm — confident this component is healthy.
          </Text>
        </View>
      )}

      {/* Top reasons */}
      {data.top_reasons?.length > 0 && (
        <View style={styles.reasons}>
          {data.top_reasons.slice(0, 3).map((r, i) => (
            <Text key={i} style={styles.reason}>· {r}</Text>
          ))}
        </View>
      )}

      {/* Warning */}
      {data.warning ? (
        <View style={styles.warningBox}>
          <Text style={styles.warningText}>{data.warning}</Text>
        </View>
      ) : null}

      {/* Model age */}
      {data.model_age_days != null && data.model_age_days > 60 && (
        <View style={styles.staleBox}>
          <Text style={styles.staleText}>Model {data.model_age_days}d old — retrain recommended</Text>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: '#16213e',
    borderRadius: 10,
    padding: 16,
    borderWidth: 1,
    borderColor: '#2a2a5a',
    borderLeftWidth: 4,
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 12,
  },
  name: { color: '#e0e0e0', fontSize: 16, fontWeight: '700', flex: 1, marginRight: 8 },
  row: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginTop: 8,
  },
  label: { color: '#888', fontSize: 13 },
  value: { color: '#ccc', fontSize: 13, fontWeight: '600' },
  signalPill: {
    backgroundColor: '#1e3a5f',
    borderRadius: 4,
    paddingHorizontal: 8,
    paddingVertical: 3,
  },
  signalText: { color: '#aac4e8', fontSize: 11 },
  barBg: {
    backgroundColor: '#0d0d1f',
    height: 6,
    borderRadius: 3,
    marginTop: 12,
    overflow: 'hidden',
  },
  barFill: { height: 6, borderRadius: 3 },
  barLabel: { color: '#666', fontSize: 11, marginTop: 4 },
  reasons: { marginTop: 10 },
  reason: { color: '#aaa', fontSize: 12, marginTop: 3 },
  warningBox: {
    backgroundColor: '#3a2000',
    borderRadius: 5,
    padding: 8,
    marginTop: 10,
  },
  warningText: { color: '#ffb347', fontSize: 12 },
  overrideBox: {
    backgroundColor: '#0e2a1a',
    borderRadius: 5,
    padding: 8,
    marginTop: 10,
  },
  overrideText: { color: '#5fd39a', fontSize: 12 },
  staleBox: {
    backgroundColor: '#3a2800',
    borderRadius: 5,
    padding: 6,
    marginTop: 8,
  },
  staleText: { color: '#cc8833', fontSize: 11 },
});
