import { View, Text, StyleSheet } from 'react-native';
import { ComponentData } from '../services/api';
import AlertBadge from './AlertBadge';

const LEVEL_BAR: Record<string, string> = {
  RED: '#e74c3c',
  ORANGE: '#e67e22',
  YELLOW: '#f39c12',
  GREEN: '#2ecc71',
};

export default function ComponentCard({ data }: { data: ComponentData }) {
  const barColor = LEVEL_BAR[data.alert_level] ?? '#2ecc71';
  const pct = Math.min(100, Math.max(0, data.pct_life_used ?? 0));

  return (
    <View style={[styles.card, { borderLeftColor: barColor }]}>
      {/* Header: name + alert badge */}
      <View style={styles.header}>
        <Text style={styles.name}>{data.name}</Text>
        <AlertBadge level={data.alert_level} />
      </View>

      {/* Days remaining */}
      <View style={styles.row}>
        <Text style={styles.label}>Days remaining</Text>
        <Text style={[styles.value, { color: barColor }]}>
          {data.days_estimate != null ? `${data.days_estimate.toFixed(1)} d` : 'N/A'}
        </Text>
      </View>

      {/* Life used progress bar */}
      <View style={styles.barBg}>
        <View style={[styles.barFill, { width: `${pct}%`, backgroundColor: barColor }]} />
      </View>
      <Text style={styles.barLabel}>{pct.toFixed(0)}% of service interval used</Text>

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
  staleBox: {
    backgroundColor: '#3a2800',
    borderRadius: 5,
    padding: 6,
    marginTop: 8,
  },
  staleText: { color: '#cc8833', fontSize: 11 },
});
