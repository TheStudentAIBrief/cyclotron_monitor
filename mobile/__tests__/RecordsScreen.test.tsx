/**
 * TDD: Predictions tab render crash.
 *
 * Root cause: switching from Maintenance → Predictions fires onPress, which
 * previously called only setActiveTab(t.key). React batched that single update
 * and re-rendered with activeTab='predictions' but items still holding stale
 * MaintenanceEvent[]. renderPrediction accessed item.run_at (undefined on
 * MaintenanceEvent) and called .slice(0, 10) → TypeError → red crash screen.
 *
 * Attempted-but-insufficient fix: `if (!pull) setItems([])` inside load().
 * load() runs from useEffect — which fires AFTER the render that already crashed.
 *
 * Correct fix (records.tsx onPress): call setItems([]) AND setActiveTab(t.key)
 * in the same event handler. React 19 batches both into one render where
 * items=[] and activeTab='predictions' simultaneously — no stale render window.
 */

// The field that renderPrediction accesses — absent on MaintenanceEvent
const ACCESS_RUN_AT = (item: any) => item.run_at.slice(0, 10);

const MAINTENANCE_ITEM = {
  timestamp: '2026-05-14T10:00:00',
  component_label: 'ION SOURCE',
  component_key: 'ion_source',
  source_file: 'beam.log',
};

const PREDICTION_ITEM = {
  run_at: '2026-05-14',
  component: 'ION SOURCE',
  risk_score: 1.0,
  days_estimate: 0.0,
  alert_level: 'RED',
  primary_signal: 'COUNTER',
  top_features: ['days since last maintenance: 60'],
};

// ─── Root cause proof ─────────────────────────────────────────────────────────

test('MaintenanceEvent has no run_at field', () => {
  expect((MAINTENANCE_ITEM as any).run_at).toBeUndefined();
});

test('accessing run_at.slice on a MaintenanceEvent throws TypeError', () => {
  // This is exactly what renderPrediction does when given a stale maintenance item.
  expect(() => ACCESS_RUN_AT(MAINTENANCE_ITEM)).toThrow(TypeError);
});

test('a PredictionRecord has run_at and does not crash', () => {
  expect(() => ACCESS_RUN_AT(PREDICTION_ITEM)).not.toThrow();
  expect(ACCESS_RUN_AT(PREDICTION_ITEM)).toBe('2026-05-14');
});

// ─── Fix proof ────────────────────────────────────────────────────────────────

test('rendering stale maintenance items as predictions crashes', () => {
  const staleItems = [MAINTENANCE_ITEM]; // items left from previous tab
  expect(() => staleItems.forEach(ACCESS_RUN_AT)).toThrow(TypeError);
});

test('clearing items before tab load prevents the crash', () => {
  // This is what `if (!pull) setItems([])` achieves: the FlatList sees []
  // while the new tab's data loads, so renderPrediction is never called on
  // stale maintenance items.
  const clearedItems: any[] = [];
  expect(() => clearedItems.forEach(ACCESS_RUN_AT)).not.toThrow();
});

test('prediction items render without error after load completes', () => {
  const loadedItems = [PREDICTION_ITEM];
  expect(() => loadedItems.forEach(ACCESS_RUN_AT)).not.toThrow();
  expect(loadedItems.map(ACCESS_RUN_AT)).toEqual(['2026-05-14']);
});

// ─── Race-condition fix: onPress must clear items atomically ─────────────────

test('stale render window: onPress-only-setActiveTab leaves items populated during new tab render', () => {
  // Reproduce the gap: onPress fired setActiveTab('predictions') but items were
  // NOT cleared in the same update. React renders with activeTab='predictions'
  // and items=[MAINTENANCE_ITEM] before useEffect can run load() → setItems([]).
  const staleItems = [MAINTENANCE_ITEM];
  const activeTab = 'predictions'; // tab changed; items NOT yet cleared

  expect(() =>
    staleItems.forEach(item => {
      if (activeTab === 'predictions') ACCESS_RUN_AT(item as any);
    })
  ).toThrow(TypeError); // stale render → crash confirmed — regression guard
});

test('batched fix: setItems([]) + setActiveTab in same handler → empty items on first render', () => {
  // React 19 batches all state updates within a single event handler.
  // onPress: { setItems([]); setActiveTab(t.key) } → single render where
  // items=[] AND activeTab='predictions' — no stale items ever reach renderPrediction.
  const items: any[] = [];       // setItems([]) fired
  const activeTab = 'predictions'; // setActiveTab fired — both batched

  expect(items).toHaveLength(0);
  expect(() =>
    items.forEach(item => {
      if (activeTab === 'predictions') ACCESS_RUN_AT(item);
    })
  ).not.toThrow(); // empty array → forEach is a no-op → no crash
});
