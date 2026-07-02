export interface BeamTrendRow {
  date: string;
  param: string;
  mean: number | null;
  min?: number | null;
  max?: number | null;
}

export interface GaugeHistoryRow {
  gauge_name: string;
  timestamp: string;
  value: number | null;
  unit: string;
  is_alert?: number | boolean;
  photo_path?: string;
}

/**
 * Collapse the flat beam_daily rows (as returned by GET /api/dashboard,
 * pre-sorted date DESC / param ASC by the backend) into one "latest value"
 * entry per param, in first-seen order.
 */
export function summarizeBeamTrend(
  rows: BeamTrendRow[],
): { param: string; latest: number | null }[] {
  const seen = new Set<string>();
  const result: { param: string; latest: number | null }[] = [];
  for (const row of rows) {
    if (seen.has(row.param)) continue;
    seen.add(row.param);
    result.push({ param: row.param, latest: row.mean });
  }
  return result;
}

/** Recent gauge readings, most-recent-first, capped for the dashboard card. */
export function recentGaugeHistory(rows: GaugeHistoryRow[], limit = 5): GaugeHistoryRow[] {
  return rows.slice(0, limit);
}
