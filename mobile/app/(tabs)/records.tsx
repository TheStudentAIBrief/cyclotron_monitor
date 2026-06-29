import { useCallback, useEffect, useState } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, FlatList, RefreshControl,
  ActivityIndicator, TextInput,
} from 'react-native';
import {
  getMaintenance, getPredictions, getEvents,
  MaintenanceEvent, PredictionRecord, FaultEvent,
} from '../../services/api';

type Tab = 'maintenance' | 'predictions' | 'events';
type AnyRecord = MaintenanceEvent | PredictionRecord | FaultEvent;

const LEVEL_COLOR: Record<string, string> = {
  RED: '#ff6b6b',
  ORANGE: '#ffb347',
  YELLOW: '#ffe066',
  GREEN: '#7aff7a',
};

export default function RecordsScreen() {
  const [activeTab, setActiveTab] = useState<Tab>('maintenance');
  const [items, setItems] = useState<AnyRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');

  const load = useCallback(async (pull = false) => {
    // Clear stale items immediately on non-pull loads (e.g. tab switch).
    // Without this, the FlatList briefly renders items from the previous tab
    // through the new tab's render function — field names differ across types
    // (MaintenanceEvent.timestamp vs PredictionRecord.run_at), causing a crash.
    if (!pull) setItems([]);
    pull ? setRefreshing(true) : setLoading(true);
    setError('');
    const q = search.trim() || undefined;
    try {
      if (activeTab === 'maintenance') {
        const r = await getMaintenance(1, q);
        setItems(r.items);
      } else if (activeTab === 'predictions') {
        const r = await getPredictions(1, q);
        setItems(r.items);
      } else {
        const r = await getEvents(1, q);
        setItems(r.items);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load records');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [activeTab, search]);

  useEffect(() => { load(); }, [load]);

  function renderMaintenance(item: MaintenanceEvent) {
    return (
      <View style={styles.item}>
        <View style={styles.itemRow}>
          <Text style={styles.itemDate}>{item.timestamp.slice(0, 10)}</Text>
          <Text style={styles.itemTag}>MAINTENANCE</Text>
        </View>
        <Text style={styles.itemTitle}>{item.component_label}</Text>
      </View>
    );
  }

  function renderPrediction(item: PredictionRecord) {
    return (
      <View style={styles.item}>
        <View style={styles.itemRow}>
          <Text style={styles.itemDate}>{item.run_at.slice(0, 10)}</Text>
          <Text style={[styles.itemTag, { color: LEVEL_COLOR[item.alert_level] ?? '#ccc' }]}>
            {item.alert_level}
          </Text>
        </View>
        <Text style={styles.itemTitle}>{item.component}</Text>
        <Text style={styles.itemSub}>
          {item.days_estimate != null ? `${item.days_estimate.toFixed(1)} d remaining` : 'N/A'}
          {'  ·  '}Risk: {item.risk_score != null ? (item.risk_score * 100).toFixed(0) + '%' : '—'}
        </Text>
      </View>
    );
  }

  function renderEvent(item: FaultEvent) {
    return (
      <View style={styles.item}>
        <View style={styles.itemRow}>
          <Text style={styles.itemDate}>{item.timestamp.slice(0, 10)}</Text>
          <Text style={[styles.itemTag, { color: item.severity === 'FAULT' ? '#ff6b6b' : '#ffb347' }]}>
            {item.severity} {item.code}
          </Text>
        </View>
        <Text style={styles.itemTitle} numberOfLines={2}>{item.message}</Text>
        <Text style={styles.itemSub}>{item.function}</Text>
      </View>
    );
  }

  function renderItem({ item }: { item: AnyRecord }) {
    if (activeTab === 'maintenance') return renderMaintenance(item as MaintenanceEvent);
    if (activeTab === 'predictions') return renderPrediction(item as PredictionRecord);
    return renderEvent(item as FaultEvent);
  }

  const TABS: { key: Tab; label: string }[] = [
    { key: 'maintenance', label: 'Maintenance' },
    { key: 'predictions', label: 'Predictions' },
    { key: 'events', label: 'Faults' },
  ];

  return (
    <View style={styles.container}>
      {/* Sub-tab bar */}
      <View style={styles.tabBar}>
        {TABS.map(t => (
          <TouchableOpacity
            key={t.key}
            style={[styles.tab, activeTab === t.key && styles.tabActive]}
            onPress={() => { setItems([]); setActiveTab(t.key); }}
            activeOpacity={0.7}
          >
            <Text style={[styles.tabText, activeTab === t.key && styles.tabTextActive]}>
              {t.label}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      {/* Search bar */}
      <View style={styles.searchRow}>
        <TextInput
          style={styles.search}
          placeholder={
            activeTab === 'events'
              ? 'Filter by fault code…'
              : 'Filter by component…'
          }
          placeholderTextColor="#444"
          value={search}
          onChangeText={setSearch}
          returnKeyType="search"
          onSubmitEditing={() => load()}
          autoCorrect={false}
          autoCapitalize="none"
        />
        <TouchableOpacity style={styles.searchBtn} onPress={() => load()}>
          <Text style={styles.searchBtnText}>Go</Text>
        </TouchableOpacity>
      </View>

      {error ? <Text style={styles.error}>{error}</Text> : null}

      {loading ? (
        <ActivityIndicator style={{ marginTop: 50 }} size="large" color="#4a9eff" />
      ) : (
        <FlatList
          data={items}
          keyExtractor={(_, i) => String(i)}
          renderItem={renderItem}
          contentContainerStyle={styles.list}
          ItemSeparatorComponent={() => <View style={{ height: 8 }} />}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={() => load(true)}
              tintColor="#4a9eff"
            />
          }
          ListEmptyComponent={
            <Text style={styles.empty}>No records found.</Text>
          }
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#1a1a2e' },

  tabBar: {
    flexDirection: 'row',
    backgroundColor: '#16213e',
    borderBottomWidth: 1,
    borderBottomColor: '#2a2a5a',
  },
  tab: { flex: 1, paddingVertical: 12, alignItems: 'center' },
  tabActive: { borderBottomWidth: 2, borderBottomColor: '#4a9eff' },
  tabText: { color: '#555', fontSize: 13 },
  tabTextActive: { color: '#4a9eff', fontWeight: '600' },

  searchRow: {
    flexDirection: 'row',
    backgroundColor: '#0d0d1f',
    borderBottomWidth: 1,
    borderBottomColor: '#2a2a5a',
  },
  search: {
    flex: 1,
    color: '#e0e0e0',
    paddingHorizontal: 14,
    paddingVertical: 10,
    fontSize: 13,
  },
  searchBtn: {
    paddingHorizontal: 16,
    justifyContent: 'center',
  },
  searchBtnText: { color: '#4a9eff', fontSize: 13, fontWeight: '600' },

  list: { padding: 14 },
  item: {
    backgroundColor: '#16213e',
    borderRadius: 8,
    padding: 12,
    borderWidth: 1,
    borderColor: '#2a2a5a',
  },
  itemRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 5,
  },
  itemDate: { color: '#555', fontSize: 12 },
  itemTag: { fontSize: 11, fontWeight: '700', color: '#888' },
  itemTitle: { color: '#e0e0e0', fontSize: 14, fontWeight: '600' },
  itemSub: { color: '#888', fontSize: 12, marginTop: 3 },

  error: { color: '#ff6b6b', textAlign: 'center', padding: 16 },
  empty: { color: '#555', textAlign: 'center', marginTop: 50 },
});
