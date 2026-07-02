/**
 * TDD: Dashboard beam-trend and gauge-history widget logic.
 *
 * Mirrors GaugeStatus.test.ts / GaugeLog.test.ts's style — pure logic tests,
 * no React rendering required (see TabLayout.test.tsx for why full render
 * tests of screens under expo-router are out of scope here).
 */
import {
  summarizeBeamTrend, recentGaugeHistory, BeamTrendRow, GaugeHistoryRow,
} from '../utils/dashboardWidgets';

const ROWS: BeamTrendRow[] = [
  { date: '2026-06-30', param: 'Arc-I', mean: 48.0, min: 40, max: 55 },
  { date: '2026-06-30', param: 'Vacuum-P', mean: 7.4e-7, min: 7.0e-7, max: 8.0e-7 },
  { date: '2026-06-29', param: 'Arc-I', mean: 46.0, min: 38, max: 52 },
];

const GAUGE_ROWS: GaugeHistoryRow[] = [
  { gauge_name: 'Vacuum Gauge 1', timestamp: '2026-06-30T10:00:00Z', value: 7.4e-7, unit: 'mbar' },
  { gauge_name: 'Vacuum Gauge 2', timestamp: '2026-06-29T10:00:00Z', value: 48.0, unit: 'mA' },
];

// ── summarizeBeamTrend ──────────────────────────────────────────────────────

test('summarizeBeamTrend returns one entry per param, using the most recent value', () => {
  const summary = summarizeBeamTrend(ROWS);
  expect(summary).toHaveLength(2);
  const arc = summary.find((s) => s.param === 'Arc-I');
  expect(arc?.latest).toBe(48.0); // 2026-06-30 value, not the older 2026-06-29 one
});

test('summarizeBeamTrend returns an empty array for no data (regression guard for the empty-state widget)', () => {
  expect(summarizeBeamTrend([])).toEqual([]);
});

// ── recentGaugeHistory ───────────────────────────────────────────────────────

test('recentGaugeHistory caps the list at the given limit', () => {
  expect(recentGaugeHistory(GAUGE_ROWS, 1)).toEqual([GAUGE_ROWS[0]]);
});

test('recentGaugeHistory returns an empty array for no data (regression guard for the empty-state widget)', () => {
  expect(recentGaugeHistory([])).toEqual([]);
});
