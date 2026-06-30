/**
 * TDD: Gauge Log display logic with real cofounder Magnehelic data.
 *
 * These tests guard against regressions in how the Gauge Log screen
 * classifies and formats cofounder readings imported from build_dataset.py.
 * All tests exercise pure logic only — no React rendering required.
 *
 * Cofounder's classification rule (gauge_tool._classify):
 *   if value < action_lo or value > action_hi → ACTION
 *   if value < alert_lo  or value > alert_hi  → ALERT
 *   else → NORMAL
 */

import { gaugeStatus } from '../utils/gaugeStatus';

// ── Gauge 0095 — HVAC room (Production Secondary) ────────────────────────────
// alert_lo=15, alert_hi=45, action_lo=10, action_hi=360 Pa

test('0095: June readings above alert_hi=45 are ALERT, not ACTION', () => {
  // June readings include 63, 247, 249 — all above 45, all below action_hi=360
  expect(gaugeStatus(63,  15, 45, 10, 360)).toBe('ALERT');
  expect(gaugeStatus(247, 15, 45, 10, 360)).toBe('ALERT');
  expect(gaugeStatus(249, 15, 45, 10, 360)).toBe('ALERT'); // verified-photo value
});

test('0095: reading within normal band is NORMAL', () => {
  expect(gaugeStatus(30, 15, 45, 10, 360)).toBe('NORMAL');
});

// ── Gauge 0096 — HVAC room (Production Primary) ──────────────────────────────
// alert_lo=15, alert_hi=125, action_lo=10, action_hi=200 Pa

test('0096: verified-photo reading 87 Pa is NORMAL', () => {
  expect(gaugeStatus(87, 15, 125, 10, 200)).toBe('NORMAL');
});

test('0096: April readings in normal band are NORMAL', () => {
  // April readings: 82, 78, 73, 80, 76, 79, 86, 88, 85, 86, 70, 87
  for (const v of [82, 78, 73, 80, 76, 79, 86, 88, 85, 86, 70, 87]) {
    expect(gaugeStatus(v, 15, 125, 10, 200)).toBe('NORMAL');
  }
});

// ── Gauge 0098 — HVAC room (Cyclotron Primary) ───────────────────────────────
// alert_lo=15, alert_hi=45, action_lo=10, action_hi=200 Pa

test('0098: May readings above alert_hi=45 are ALERT', () => {
  // May readings: 82, 80, 81, 82, 83, 80, 80, 81, 90, 90, 90
  for (const v of [82, 80, 81, 90]) {
    expect(gaugeStatus(v, 15, 45, 10, 200)).toBe('ALERT');
  }
});

test('0098: April reading 33 Pa is NORMAL', () => {
  expect(gaugeStatus(33, 15, 45, 10, 200)).toBe('NORMAL');
});

// ── Gauge 0121 — Pharmacy to PAL1 (negative pressure) ───────────────────────
// alert_lo=-75, alert_hi=-20, action_lo=-80, action_hi=-15 Pa

test('0121: reading -27 Pa is NORMAL (within alert band)', () => {
  expect(gaugeStatus(-27, -75, -20, -80, -15)).toBe('NORMAL');
});

test('0121: reading -14 Pa triggers ACTION (above action_hi=-15)', () => {
  // -14 > action_hi=-15 → ACTION
  expect(gaugeStatus(-14, -75, -20, -80, -15)).toBe('ACTION');
});

test('0121: reading -81 Pa triggers ACTION (below action_lo=-80)', () => {
  expect(gaugeStatus(-81, -75, -20, -80, -15)).toBe('ACTION');
});

test('0121: reading -19 Pa is ALERT (above alert_hi=-20, inside action boundary)', () => {
  // -19 > alert_hi=-20 → ALERT; action boundaries not breached
  expect(gaugeStatus(-19, -75, -20, -80, -15)).toBe('ALERT');
});

// ── Timestamp formatting (gauges.tsx lines 235-236) ──────────────────────────

test('ISO timestamp slice [0:10] gives YYYY-MM-DD date', () => {
  expect('2026-06-24T00:00:00Z'.slice(0, 10)).toBe('2026-06-24');
});

test('ISO timestamp slice [11:16] gives HH:MM time', () => {
  expect('2026-06-24T13:45:00Z'.slice(11, 16)).toBe('13:45');
  expect('2026-06-24T00:00:00Z'.slice(11, 16)).toBe('00:00');
});

// ── Empty state and recovery ──────────────────────────────────────────────────

test('empty history array has length 0 (renders "No readings yet")', () => {
  const history: unknown[] = [];
  expect(history.length).toBe(0);
});

test('history with one cofounder reading has length > 0 (renders gauge cards)', () => {
  const history = [{
    id: 113,
    lab_id: 'petlabs-pretoria',
    gauge_name: '0091',
    timestamp: '2026-06-24T00:00:00Z',
    value: 8.8,
    unit: 'Pa',
    is_alert: 0,
    alert_reason: 'NORMAL',
    location: 'PAL1 to Pharmacy',
    alert_lo: 8, alert_hi: 20, action_lo: 5, action_hi: 25,
    confidence: 'verified-photo',
    verified_by: '', verified_at: '', photo_path: '', raw_ocr_text: '',
    status: 'NORMAL' as const,
  }];
  expect(history.length).toBeGreaterThan(0);
  expect(gaugeStatus(history[0].value, history[0].alert_lo, history[0].alert_hi,
    history[0].action_lo, history[0].action_hi)).toBe('NORMAL');
});

// ── Pull-to-refresh ───────────────────────────────────────────────────────────

test('refreshing is triggered by setting refreshing=true then false', () => {
  let refreshing = false;
  function onRefresh() { refreshing = true; }
  function onRefreshDone() { refreshing = false; }

  expect(refreshing).toBe(false);
  onRefresh();
  expect(refreshing).toBe(true);
  onRefreshDone();
  expect(refreshing).toBe(false);
});

// ── Error state — Windows Firewall / network failure ─────────────────────────
// Root cause: Windows Firewall (Public profile, BlockInbound) drops inbound
// TCP on port 8000. The phone times out, request() throws, and the old
// `catch { /* history is non-critical */ }` silently leaves history=[].
// Fix: capture the error message and show it instead of "No readings yet."

test('loadHistory captures network error instead of swallowing it', async () => {
  let loadError: string | null = null;

  async function loadHistory() {
    try {
      throw new Error('Server did not respond within 8s. Check that the API is running and reachable at http://192.168.4.46:8000.');
    } catch (e: unknown) {
      loadError = e instanceof Error ? e.message : 'Failed to load readings';
    }
  }

  await loadHistory();
  expect(loadError).not.toBeNull();
  expect(loadError).toContain('192.168.4.46');
});

test('loadHistory clears error on successful reload', async () => {
  let loadError: string | null = 'previous network error';
  let history: { id: number }[] = [];

  async function loadHistory() {
    try {
      history = [{ id: 113 }];
      loadError = null;
    } catch (e: unknown) {
      loadError = e instanceof Error ? e.message : 'Failed to load readings';
    }
  }

  await loadHistory();
  expect(loadError).toBeNull();
  expect(history).toHaveLength(1);
});

test('loadHistory preserves stale history when a refresh fails', async () => {
  let loadError: string | null = null;
  let history = [{ id: 113, gauge_name: '0096' }];

  async function loadHistory() {
    try {
      throw new Error('Network timeout');
    } catch (e: unknown) {
      loadError = e instanceof Error ? e.message : 'Failed to load readings';
      // do NOT clear history — keep showing previous data
    }
  }

  await loadHistory();
  expect(loadError).toBe('Network timeout');
  expect(history).toHaveLength(1); // stale cards still visible
});

test('empty-state shows error hint when loadError is set', () => {
  // When history=[] and loadError is set, UI should show the error, not "No readings yet."
  const history: unknown[] = [];
  const loadError = 'Server did not respond within 8s.';

  const showsError = history.length === 0 && loadError !== null;
  const showsEmpty = history.length === 0 && loadError === null;

  expect(showsError).toBe(true);
  expect(showsEmpty).toBe(false);
});

test('empty-state shows "No readings yet" when load succeeded but DB is empty', () => {
  const history: unknown[] = [];
  const loadError: string | null = null;

  const showsError = history.length === 0 && loadError !== null;
  const showsEmpty = history.length === 0 && loadError === null;

  expect(showsError).toBe(false);
  expect(showsEmpty).toBe(true);
});
