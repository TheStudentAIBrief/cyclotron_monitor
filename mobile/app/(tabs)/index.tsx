import { useCallback, useEffect, useState } from 'react';
import {
  View, Text, FlatList, RefreshControl, StyleSheet, ActivityIndicator,
} from 'react-native';
import { getDashboard, DashboardData, ComponentData } from '../../services/api';
import ComponentCard from '../../components/ComponentCard';

export default function DashboardScreen() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async (pull = false) => {
    if (pull) setRefreshing(true);
    try {
      const d = await getDashboard();
      setData(d);
      setError('');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load dashboard');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // Auto-refresh every 60 seconds
  useEffect(() => {
    const id = setInterval(() => load(), 60_000);
    return () => clearInterval(id);
  }, [load]);

  const staleThresholdMs = 2 * 60 * 60 * 1000; // 2 hours
  const isStale = data
    ? Date.now() - new Date(data.generated_at).getTime() > staleThresholdMs
    : false;

  if (loading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator size="large" color="#4a9eff" />
        <Text style={styles.loadingText}>Loading predictions…</Text>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      {isStale && (
        <View style={styles.staleBanner}>
          <Text style={styles.staleText}>
            ⚠ Data may be stale — cyclotron sync not recent
          </Text>
        </View>
      )}
      {error ? (
        <View style={styles.errorBanner}>
          <Text style={styles.errorText}>{error}</Text>
        </View>
      ) : null}

      <FlatList
        data={data?.components ?? []}
        keyExtractor={(item: ComponentData) => item.name}
        renderItem={({ item }) => <ComponentCard data={item} />}
        contentContainerStyle={styles.list}
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
            <Text style={styles.subtitle}>
              Predictions as of {new Date(data.generated_at).toLocaleString()}
            </Text>
          ) : null
        }
        ListEmptyComponent={
          !error ? (
            <Text style={styles.empty}>
              No predictions yet.{'\n'}Run: python main.py predict
            </Text>
          ) : null
        }
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#1a1a2e' },
  centered: {
    flex: 1,
    backgroundColor: '#1a1a2e',
    justifyContent: 'center',
    alignItems: 'center',
  },
  loadingText: { color: '#666', marginTop: 12, fontSize: 13 },
  staleBanner: {
    backgroundColor: '#7a3500',
    paddingVertical: 8,
    paddingHorizontal: 16,
  },
  staleText: { color: '#ffb347', fontSize: 12, textAlign: 'center' },
  errorBanner: {
    backgroundColor: '#5a1515',
    paddingVertical: 8,
    paddingHorizontal: 16,
  },
  errorText: { color: '#ff6b6b', fontSize: 12, textAlign: 'center' },
  list: { padding: 16, paddingTop: 8 },
  subtitle: {
    color: '#555',
    fontSize: 11,
    textAlign: 'center',
    marginBottom: 12,
  },
  empty: {
    color: '#666',
    textAlign: 'center',
    marginTop: 60,
    fontSize: 14,
    lineHeight: 22,
  },
});
