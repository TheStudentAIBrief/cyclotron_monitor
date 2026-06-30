/**
 * TDD: gaugeStatus() mirrors cofounder's gauge_tool._classify() exactly.
 *
 * Cofounder's Python:
 *   if value < action_lo or value > action_hi: return "ACTION"
 *   if value < alert_lo  or value > alert_hi:  return "ALERT"
 *   return "NORMAL"
 *
 * These tests guard the import pipeline: imported CSV rows must classify
 * identically to the cofounder's web app so the Gauge Log shows matching
 * NORMAL / ALERT / ACTION badges.
 */

import { gaugeStatus } from '../utils/gaugeStatus';

// Reference thresholds matching cofounder's gauge schema:
// alert_lo=50, alert_hi=150, action_lo=20, action_hi=300 (Pa)
const LO   = 50;
const HI   = 150;
const ALO  = 20;
const AHI  = 300;

// ── ACTION ───────────────────────────────────────────────────────────────────

test('ACTION: value exceeds action_hi', () => {
  expect(gaugeStatus(350, LO, HI, ALO, AHI)).toBe('ACTION');
});

test('ACTION: value below action_lo', () => {
  expect(gaugeStatus(10, LO, HI, ALO, AHI)).toBe('ACTION');
});

test('ACTION: value exactly at action_hi boundary is not ACTION', () => {
  expect(gaugeStatus(300, LO, HI, ALO, AHI)).not.toBe('ACTION');
});

// ── ALERT ────────────────────────────────────────────────────────────────────

test('ALERT: value exceeds alert_hi but within action_hi', () => {
  expect(gaugeStatus(200, LO, HI, ALO, AHI)).toBe('ALERT');
});

test('ALERT: value below alert_lo but above action_lo', () => {
  expect(gaugeStatus(30, LO, HI, ALO, AHI)).toBe('ALERT');
});

// ── NORMAL ───────────────────────────────────────────────────────────────────

test('NORMAL: value within all thresholds', () => {
  expect(gaugeStatus(100, LO, HI, ALO, AHI)).toBe('NORMAL');
});

test('NORMAL: value exactly at alert_lo boundary', () => {
  expect(gaugeStatus(50, LO, HI, ALO, AHI)).toBe('NORMAL');
});

test('NORMAL: value exactly at alert_hi boundary', () => {
  expect(gaugeStatus(150, LO, HI, ALO, AHI)).toBe('NORMAL');
});

test('NORMAL: no thresholds set (manual entry, no cofounder data)', () => {
  expect(gaugeStatus(100, null, null, null, null)).toBe('NORMAL');
});

// ── UNKNOWN ──────────────────────────────────────────────────────────────────

test('UNKNOWN: null value (OCR failed to extract reading)', () => {
  expect(gaugeStatus(null, LO, HI, ALO, AHI)).toBe('UNKNOWN');
});

test('UNKNOWN: undefined value', () => {
  expect(gaugeStatus(undefined as unknown as null, LO, HI, ALO, AHI)).toBe('UNKNOWN');
});
