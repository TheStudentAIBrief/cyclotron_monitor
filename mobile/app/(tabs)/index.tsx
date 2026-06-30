import { useCallback, useEffect, useState } from 'react';
import {
  View, Text, FlatList, RefreshControl, StyleSheet,
  ActivityIndicator, TouchableOpacity,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import {
  getDashboard, getPETraceDashboard, DashboardData, ComponentData,
} from '../../services/api';
import ComponentCard from '../../components/ComponentCard';

type View = null | 'main' | 'petrace';

const LEVEL_COLOR: Record<string, string> = {
  RED: '#e74c3c', ORANGE: '#e67e22', YELLOW: '#f39c12', GREEN: '#2ecc71',
};
const LEVELS = ['RED', 'ORANGE', 'YELLOW', 'GREEN'] as const;

// ── Selector screen ───────────────────────────────────────────────────────────

function SelectorScreen({ onSelect }: { onSelect: (v: 'main' | 'petrace') => void }) {
  return (
    <View style={sel.container}>
      <Text style={sel.heading}>Select Cyclotron</Text>
      <Text style={sel.sub}>Choose a cyclotron to view its maintenance dashboard</Text>

      <TouchableOpacity style={sel.card} onPress={() => onSelect('main')} activeOpacity={0.8}>
        <View style={sel.iconWrap}>
          <Ionicons name="pulse" size={32} color="#4a9eff" />
        </View>
        <View style={sel.textWrap}>
          <Text style={sel.cardTitle}>IBA Cyclone 18/9</Text>
          <Text style={sel.cardDesc}>ML maintenance predictions · beam log analysis</Text>
        </View>
        <Ionicons name="chevron-forward" size={20} color="#555" />
      </TouchableOpacity>

      <TouchableOpacity style={sel.card} onPress={() => onSelect('petrace')} activeOpacity={0.8}>
        <View style={sel.iconWrap}>
          <Ionicons name="radio-outline" size={32} color="#cc88ff" />
        </View>
        <View style={sel.textWrap}>
          <Text style={sel.cardTitle}>PETrace 800</Text>
          <Text style={sel.cardDesc}>Foil life · beam current · RF &amp; vacuum health</Text>
        </View>
        <Ionicons name="chevron-forward" size={20} color="#555" />
      </TouchableOpacity>
    </View>
  );
}

const sel = StyleSheet.create({
  container: {
    flex: 1, backgroundColor: '#1a1a2e',
    padding: 20, justifyContent: 'center',
  },
  heading: {
    color: '#e0e0e0', fontSize: 22, fontWeight: '700',
    textAlign: 'center', marginBottom: 6,
  },
  sub: {
    color: '#555', fontSize: 13, textAlign: 'center', marginBottom: 32,
  },
  card: {
    backgroundColor: '#16213e',
    borderWidth: 1, borderColor: '#2a2a5a',
    borderRadius: 14, padding: 18, marginBottom: 16,
    flexDirection: 'row', alignItems: 'center', gap: 14,
  },
  iconWrap: {
    width: 52, height: 52, borderRadius: 12,
    backgroundColor: '#0d0d1f',
    alignItems: 'center', justifyContent: 'center',
  },
  textWrap: { flex: 1 },
  cardTitle: { color: '#e0e0e0', fontSize: 16, fontWeight: '700', marginBottom: 4 },
  cardDesc:  { color: '#666', fontSize: 13, lineHeight: 18 },
});

// ── Shared dashboard view ─────────────────────────────────────────────────────

function DashboardView({
  fetchFn,
  title,
  subtitle,
  onBack,
}: {
  fetchFn: () => Promise<DashboardData>;
  title: string;
  subtitle: string;
  onBack: () => void;
}) {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async (pull = false) => {
    if (pull) setRefreshing(true);
    try {
      const d = await fetchFn();
      setData(d);
      setError('');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load dashboard');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [fetchFn]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    const id = setInterval(() => load(), 60_000);
    return () => clearInterval(id);
  }, [load]);

  const staleThresholdMs = 2 * 60 * 60 * 1000;
  const isStale = data
    ? Date.now() - new Date(data.generated_at).getTime() > staleThresholdMs
    : false;

  if (loading) {
    return (
      <View style={dash.centered}>
        <ActivityIndicator size="large" color="#4a9eff" />
        <Text style={dash.loadingText}>Loading predictions…</Text>
      </View>
    );
  }

  return (
    <View style={dash.container}>
      <TouchableOpacity style={dash.backRow} onPress={onBack} activeOpacity={0.7}>
        <Ionicons name="chevron-back" size={18} color="#4a9eff" />
        <Text style={dash.backLabel}>All Cyclotrons</Text>
      </TouchableOpacity>

      <View style={dash.titleRow}>
        <Text style={dash.title}>{title}</Text>
        <Text style={dash.subtitleText}>{subtitle}</Text>
      </View>

      {isStale && (
        <View style={dash.staleBanner}>
          <Text style={dash.staleText}>⚠ Data may be stale — sync not recent</Text>
        </View>
      )}
      {error ? (
        <View style={dash.errorBanner}>
          <Text style={dash.errorText}>{error}</Text>
        </View>
      ) : null}

      <FlatList
        data={data?.components ?? []}
        keyExtractor={(item: ComponentData) => item.name}
        renderItem={({ item }) => <ComponentCard data={item} />}
        contentContainerStyle={dash.list}
        ItemSeparatorComponent={() => <View style={{ height: 12 }} />}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => load(true)}
            tintColor="#4a9eff"
          />
        }
        ListHeaderComponent={
          data ? (
            <View>
              <View style={dash.chips}>
                {LEVELS.map(level => {
                  const count = data.components.filter(c => c.alert_level === level).length;
                  return (
                    <View key={level} style={dash.chip}>
                      <Text style={[dash.chipNumber, { color: LEVEL_COLOR[level] }]}>{count}</Text>
                      <Text style={dash.chipLabel}>{level}</Text>
                    </View>
                  );
                })}
              </View>
              <Text style={dash.generatedAt}>
                As of {new Date(data.generated_at).toLocaleString()}
              </Text>
            </View>
          ) : null
        }
        ListEmptyComponent={
          !error ? (
            <Text style={dash.empty}>
              No predictions yet.{'\n'}Run: python main.py predict
            </Text>
          ) : null
        }
      />
    </View>
  );
}

const dash = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#1a1a2e' },
  centered: {
    flex: 1, backgroundColor: '#1a1a2e',
    justifyContent: 'center', alignItems: 'center',
  },
  loadingText: { color: '#666', marginTop: 12, fontSize: 13 },
  backRow: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    paddingHorizontal: 16, paddingTop: 12, paddingBottom: 4,
  },
  backLabel: { color: '#4a9eff', fontSize: 14 },
  titleRow: { paddingHorizontal: 16, paddingBottom: 8 },
  title: { color: '#e0e0e0', fontSize: 18, fontWeight: '700' },
  subtitleText: { color: '#555', fontSize: 12, marginTop: 2 },
  staleBanner: { backgroundColor: '#7a3500', paddingVertical: 8, paddingHorizontal: 16 },
  staleText: { color: '#ffb347', fontSize: 12, textAlign: 'center' },
  errorBanner: { backgroundColor: '#5a1515', paddingVertical: 8, paddingHorizontal: 16 },
  errorText: { color: '#ff6b6b', fontSize: 12, textAlign: 'center' },
  list: { padding: 16, paddingTop: 4 },
  chips: { flexDirection: 'row', gap: 8, marginBottom: 10 },
  chip: {
    flex: 1, backgroundColor: '#16213e',
    borderWidth: 1, borderColor: '#2a2a5a',
    borderRadius: 10, paddingVertical: 10, alignItems: 'center',
  },
  chipNumber: { fontSize: 20, fontWeight: '800' },
  chipLabel: { fontSize: 10, color: '#8aa', marginTop: 2 },
  generatedAt: {
    color: '#555', fontSize: 11, textAlign: 'center', marginBottom: 12,
  },
  empty: {
    color: '#666', textAlign: 'center', marginTop: 60,
    fontSize: 14, lineHeight: 22,
  },
});

// ── Root screen ───────────────────────────────────────────────────────────────

export default function DashboardScreen() {
  const [view, setView] = useState<View>(null);

  if (view === 'main') {
    return (
      <DashboardView
        fetchFn={getDashboard}
        title="IBA Cyclone 18/9"
        subtitle="ML maintenance predictions"
        onBack={() => setView(null)}
      />
    );
  }

  if (view === 'petrace') {
    return (
      <DashboardView
        fetchFn={getPETraceDashboard}
        title="PETrace 800"
        subtitle="Foil life · beam current · RF & vacuum"
        onBack={() => setView(null)}
      />
    );
  }

  return <SelectorScreen onSelect={setView} />;
}
