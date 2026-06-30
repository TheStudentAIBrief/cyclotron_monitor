export type GaugeStatus = 'NORMAL' | 'ALERT' | 'ACTION' | 'UNKNOWN';

/**
 * Classify a gauge reading against its thresholds.
 * Mirrors the cofounder's gauge_tool._classify() logic exactly.
 *
 * Priority: ACTION > ALERT > NORMAL
 * If no thresholds are set, returns NORMAL (value is present) or UNKNOWN (null).
 */
export function gaugeStatus(
  value: number | null,
  alertLo: number | null,
  alertHi: number | null,
  actionLo: number | null,
  actionHi: number | null,
): GaugeStatus {
  if (value === null || value === undefined) return 'UNKNOWN';
  if (actionLo !== null && actionLo !== undefined && value < actionLo) return 'ACTION';
  if (actionHi !== null && actionHi !== undefined && value > actionHi) return 'ACTION';
  if (alertLo !== null && alertLo !== undefined && value < alertLo) return 'ALERT';
  if (alertHi !== null && alertHi !== undefined && value > alertHi) return 'ALERT';
  return 'NORMAL';
}

export const STATUS_COLORS: Record<GaugeStatus, { bg: string; text: string; border: string }> = {
  ACTION: { bg: '#6b1d1d', text: '#ff6b6b', border: '#e74c3c' },
  ALERT:  { bg: '#7a5a00', text: '#ffe066', border: '#f39c12' },
  NORMAL: { bg: '#1d4d2e', text: '#7aff7a', border: '#2ecc71' },
  UNKNOWN:{ bg: '#1e2a3a', text: '#6b7a99', border: '#2a2a5a' },
};
