import { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator, RefreshControl, ScrollView, StyleSheet, Text, View,
} from 'react-native';
import { getPETraceSummary, getPETraceBatches, PETraceSummary, PETraceBatch } from '../../services/api';

const TRACER_COLORS: Record<number, string> = {
  1: '#4a9eff',
  2: '#7aff7a',
  3: '#ffb347',
  4: '#ff6b6b',
  5: '#cc88ff',
};

function tracerColor(num: number) {
  return TRACER_COLORS[num] ?? '#aaa';
}

function fmtDate(d: string) {
  return d?.slice(0, 10) ?? '—';
}

function fmtDuration(s: number) {
  if (!s) return '—';
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

function BeamBar({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? Math.min(value / max, 1) : 0;
  const col = pct > 0.85 ? '#7aff7a' : pct > 0.6 ? '#ffb347' : '#ff6b6b';
  return (
    <View style={barStyles.track}>
      <View style={[barStyles.fill, { width: `${pct * 100}%` as any, backgroundColor: col }]} />
    </View>
  );
}

const barStyles = StyleSheet.create({
  track: { height: 6, backgroundColor: '#2a2a5a', borderRadius: 3, overflow: 'hidden', flex: 1 },
  fill:  { height: 6, borderRadius: 3 },
});

export default function PETraceScreen() {
  const [summary, setSummary] = useState<PETraceSummary | null>(null);
  const [batches, setBatches] = useState<PETraceBatch[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      const [sum, page1] = await Promise.all([
        getPETraceSummary(),
        getPETraceBatches(1),
      ]);
      setSummary(sum);
      setBatches(page1.items);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load PETrace data');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const onRefresh = useCallback(() => {
    setRefreshing(true);
    load();
  }, [load]);

  const maxPeak = batches.reduce((m, b) => Math.max(m, b.peak_target_uA), 0);

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color="#4a9eff" size="large" />
        <Text style={styles.loadingText}>Loading PETrace data…</Text>
      </View>
    );
  }

  if (error) {
    return (
      <View style={styles.center}>
        <Text style={styles.errorText}>{error}</Text>
      </View>
    );
  }

  return (
    <ScrollView
      style={styles.container}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#4a9eff" />}
    >
      {/* ── Summary cards ── */}
      <View style={styles.cardRow}>
        <View style={styles.card}>
          <Text style={styles.cardLabel}>Batches</Text>
          <Text style={styles.cardValue}>{summary?.batch_count ?? 0}</Text>
        </View>
        <View style={styles.card}>
          <Text style={styles.cardLabel}>Total µAh</Text>
          <Text style={styles.cardValue}>{summary?.total_muAh?.toFixed(0) ?? 0}</Text>
        </View>
        <View style={styles.card}>
          <Text style={styles.cardLabel}>Foil #</Text>
          <Text style={[styles.cardValue, { color: '#ffb347' }]}>{summary?.current_foil ?? '—'}</Text>
        </View>
      </View>

      <View style={styles.cardRow}>
        <View style={[styles.card, { flex: 1 }]}>
          <Text style={styles.cardLabel}>Date range</Text>
          <Text style={styles.cardMeta}>
            {fmtDate(summary?.first_date ?? '')} → {fmtDate(summary?.last_date ?? '')}
          </Text>
        </View>
      </View>

      {/* ── Foil change events ── */}
      {summary && summary.foil_changes.length > 0 && (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Foil Replacements</Text>
          {summary.foil_changes.slice().reverse().map((fc, i) => (
            <View key={i} style={styles.foilRow}>
              <Text style={styles.foilDate}>{fmtDate(fc.batch_date)}</Text>
              <Text style={styles.foilDetail}>
                Foil {fc.old_foil} → <Text style={{ color: '#ffb347', fontWeight: '700' }}>Foil {fc.new_foil}</Text>
                {'  '}
                <Text style={styles.foilBatch}>(batch #{fc.batch_no})</Text>
              </Text>
            </View>
          ))}
        </View>
      )}

      {/* ── Beam current chart (last 20 batches) ── */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Peak Target-I (µA) — last 20 batches</Text>
        <View style={styles.chartGrid}>
          {(summary?.recent_batches ?? []).slice().reverse().map(b => (
            <View key={b.batch_no} style={styles.chartRow}>
              <Text style={styles.chartLabel}>#{b.batch_no}</Text>
              <BeamBar value={b.peak_target_uA} max={maxPeak} />
              <Text style={styles.chartVal}>{b.peak_target_uA.toFixed(0)}</Text>
            </View>
          ))}
        </View>
      </View>

      {/* ── Batch history ── */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Batch History</Text>
        {batches.map(b => (
          <View key={b.batch_no} style={styles.batchCard}>
            <View style={styles.batchHeader}>
              <View style={styles.batchLeft}>
                <Text style={styles.batchNo}>#{b.batch_no}</Text>
                <View style={[styles.tracerPill, { borderColor: tracerColor(b.tracer_num) }]}>
                  <Text style={[styles.tracerPillText, { color: tracerColor(b.tracer_num) }]}>
                    {b.tracer_name || `Tracer ${b.tracer_num}`}
                  </Text>
                </View>
              </View>
              <Text style={styles.batchDate}>{fmtDate(b.batch_date)}</Text>
            </View>

            <View style={styles.batchStats}>
              <View style={styles.statCell}>
                <Text style={styles.statLabel}>Peak I</Text>
                <Text style={styles.statVal}>{b.peak_target_uA.toFixed(1)} µA</Text>
              </View>
              <View style={styles.statCell}>
                <Text style={styles.statLabel}>µAh</Text>
                <Text style={styles.statVal}>{b.total_muAh.toFixed(1)}</Text>
              </View>
              <View style={styles.statCell}>
                <Text style={styles.statLabel}>Duration</Text>
                <Text style={styles.statVal}>{fmtDuration(b.duration_s)}</Text>
              </View>
              <View style={styles.statCell}>
                <Text style={styles.statLabel}>Foil</Text>
                <Text style={[styles.statVal, { color: '#ffb347' }]}>{b.foil_no ?? '—'}</Text>
              </View>
            </View>

            {b.row_count === 0 && (
              <Text style={styles.emptyBatch}>No beam data recorded</Text>
            )}
          </View>
        ))}
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#1a1a2e' },
  center: { flex: 1, backgroundColor: '#1a1a2e', alignItems: 'center', justifyContent: 'center', padding: 20 },
  loadingText: { color: '#555', marginTop: 12 },
  errorText: { color: '#ff6b6b', textAlign: 'center' },

  cardRow: { flexDirection: 'row', padding: 12, gap: 8 },
  card: {
    flex: 1, backgroundColor: '#16213e', borderRadius: 10, padding: 14,
    borderWidth: 1, borderColor: '#2a2a5a',
  },
  cardLabel: { color: '#555', fontSize: 10, fontWeight: '600', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 6 },
  cardValue: { color: '#e0e0e0', fontSize: 24, fontWeight: '700' },
  cardMeta: { color: '#aaa', fontSize: 13, marginTop: 4 },

  section: { paddingHorizontal: 12, paddingBottom: 8 },
  sectionTitle: {
    color: '#555', fontSize: 11, fontWeight: '600',
    textTransform: 'uppercase', letterSpacing: 1, marginBottom: 10, marginTop: 6,
  },

  foilRow: { backgroundColor: '#16213e', borderRadius: 8, padding: 10, marginBottom: 6, flexDirection: 'row', alignItems: 'center', gap: 10 },
  foilDate: { color: '#888', fontSize: 12, minWidth: 80 },
  foilDetail: { color: '#ccc', fontSize: 13, flex: 1 },
  foilBatch: { color: '#555', fontSize: 11 },

  chartGrid: { gap: 5 },
  chartRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  chartLabel: { color: '#555', fontSize: 10, width: 32, textAlign: 'right' },
  chartVal: { color: '#aaa', fontSize: 10, width: 28, textAlign: 'right' },

  batchCard: {
    backgroundColor: '#16213e', borderRadius: 8, padding: 12,
    marginBottom: 8, borderWidth: 1, borderColor: '#2a2a5a',
  },
  batchHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 },
  batchLeft: { flexDirection: 'row', alignItems: 'center', gap: 8, flex: 1 },
  batchNo: { color: '#4a9eff', fontSize: 13, fontWeight: '700', minWidth: 36 },
  tracerPill: {
    borderWidth: 1, borderRadius: 4,
    paddingHorizontal: 6, paddingVertical: 2,
  },
  tracerPillText: { fontSize: 10, fontWeight: '600' },
  batchDate: { color: '#555', fontSize: 11 },
  batchStats: { flexDirection: 'row', gap: 4 },
  statCell: { flex: 1, alignItems: 'center' },
  statLabel: { color: '#555', fontSize: 9, textTransform: 'uppercase', marginBottom: 2 },
  statVal: { color: '#ccc', fontSize: 13, fontWeight: '600' },
  emptyBatch: { color: '#444', fontSize: 11, marginTop: 6, fontStyle: 'italic' },
});
