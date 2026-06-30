import { getAccessToken, refreshAccessToken, logout } from './auth';
import Config from '../constants/Config';

// ─── Response types ──────────────────────────────────────────────────────────

export interface ComponentData {
  name: string;
  alert_level: 'RED' | 'ORANGE' | 'YELLOW' | 'GREEN';
  days_estimate: number | null;
  pct_life_used: number;
  last_maintenance: string | null;
  top_reasons: string[];
  counter_days: number | null;
  primary_signal: 'COUNTER' | 'MODEL' | 'BOTH' | 'COUNTER_ONLY';
  risk_score: number;
  warning: string | null;
  trained_at: string | null;
  model_age_days: number | null;
}

export interface DashboardData {
  generated_at: string;
  components: ComponentData[];
}

export interface GaugeReading {
  id: number;
  lab_id: string;
  gauge_name: string;
  timestamp: string;
  value: number | null;
  unit: string;
  is_alert: number;
  alert_reason: string;
  photo_path: string;
  raw_ocr_text: string;
  // cofounder fields (present on imported readings)
  location: string;
  alert_lo: number | null;
  alert_hi: number | null;
  action_lo: number | null;
  action_hi: number | null;
  confidence: string;
  verified_by: string;
  verified_at: string;
  status: 'NORMAL' | 'ALERT' | 'ACTION' | 'UNKNOWN';
}

export interface MaintenanceEvent {
  timestamp: string;
  component_label: string;
  component_key: string;
  source_file: string | null;
}

export interface PredictionRecord {
  run_at: string;
  component: string;
  risk_score: number;
  days_estimate: number | null;
  alert_level: string;
  primary_signal: string;
  top_features: string[];
}

export interface FaultEvent {
  timestamp: string;
  severity: string;
  code: string;
  function: string;
  message: string;
  source_file: string;
}

export interface Paged<T> {
  page: number;
  per_page: number;
  items: T[];
}

// ─── Fetch with timeout ───────────────────────────────────────────────────────
// Hermes (React Native 0.76) does not reliably implement AbortSignal.timeout(),
// so use an explicit AbortController + setTimeout. Without this, an unreachable
// API host makes fetch() hang on the OS TCP connect timeout (tens of seconds),
// which freezes the loading screens and shows "taking longer than expected".
async function fetchWithTimeout(url: string, options: RequestInit, timeout = Config.API_TIMEOUT_MS): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } catch (e: unknown) {
    if (e instanceof Error && e.name === 'AbortError') {
      throw new Error(
        `Server did not respond within ${Math.round(timeout / 1000)}s. ` +
        `Check that the API is running and reachable at ${Config.API_URL}.`,
      );
    }
    throw new Error(`Cannot reach server at ${Config.API_URL}. Check your network connection.`);
  } finally {
    clearTimeout(timer);
  }
}

// ─── Core request ─────────────────────────────────────────────────────────────

async function request<T>(path: string, options: RequestInit = {}, timeout = Config.API_TIMEOUT_MS): Promise<T> {
  const token = await getAccessToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(options.headers as Record<string, string> ?? {}),
  };

  let res = await fetchWithTimeout(`${Config.API_URL}${path}`, { ...options, headers }, timeout);

  if (res.status === 401 && token) {
    // Access token expired — attempt refresh then retry once
    try {
      await refreshAccessToken();
      const newToken = await getAccessToken();
      const retryHeaders = { ...headers, Authorization: `Bearer ${newToken ?? ''}` };
      res = await fetchWithTimeout(`${Config.API_URL}${path}`, { ...options, headers: retryHeaders }, timeout);
    } catch {
      await logout();
      throw new Error('Session expired. Please log in again.');
    }
  }

  if (res.status === 401 || res.status === 403) {
    // Refresh failed, or there was never a usable token — force re-login
    // instead of surfacing a raw status. The retry above is not re-handled,
    // so this cannot infinite-loop.
    await logout();
    throw new Error('Session expired. Please log in again.');
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({})) as { detail?: string };
    throw new Error(err.detail ?? `Request failed: ${res.status}`);
  }
  return res.json() as Promise<T>;
}

// ─── Dashboard ────────────────────────────────────────────────────────────────

export const getDashboard = () =>
  request<DashboardData>('/api/dashboard');

// ─── Gauges ───────────────────────────────────────────────────────────────────

export const submitGaugePhoto = (photo_b64: string, gauge_name: string) =>
  request('/api/gauges/reading', {
    method: 'POST',
    body: JSON.stringify({ photo_b64, gauge_name }),
  }, Config.API_OCR_TIMEOUT_MS);

export const submitManualGauge = (data: {
  gauge_name: string;
  value: number;
  unit: string;
  is_alert: boolean;
  alert_reason: string;
}) =>
  request('/api/gauges', { method: 'POST', body: JSON.stringify(data) });

export const getGauges = (page = 1, gauge_name?: string) => {
  const p = new URLSearchParams({ page: String(page) });
  if (gauge_name) p.set('gauge_name', gauge_name);
  return request<Paged<GaugeReading>>(`/api/gauges?${p}`);
};

export const deleteGauge = (id: number) =>
  request<{ deleted: number }>(`/api/gauges/${id}`, { method: 'DELETE' });

// ─── Records ──────────────────────────────────────────────────────────────────

export const getMaintenance = (page = 1, component?: string) => {
  const p = new URLSearchParams({ page: String(page) });
  if (component) p.set('component', component);
  return request<Paged<MaintenanceEvent>>(`/api/records/maintenance?${p}`);
};

export const getPredictions = (page = 1, component?: string) => {
  const p = new URLSearchParams({ page: String(page) });
  if (component) p.set('component', component);
  return request<Paged<PredictionRecord>>(`/api/records/predictions?${p}`);
};

export const getEvents = (page = 1, code?: string) => {
  const p = new URLSearchParams({ page: String(page) });
  if (code) p.set('code', code);
  return request<Paged<FaultEvent>>(`/api/records/events?${p}`);
};

export const importGaugeCSV = (csvText: string) =>
  request<{ inserted: number; errors: string[] }>('/api/gauges/import-csv', {
    method: 'POST',
    headers: { 'Content-Type': 'multipart/form-data' },
    body: (() => {
      const fd = new FormData();
      fd.append('file', new Blob([csvText], { type: 'text/csv' }), 'gauge_readings.csv');
      return fd;
    })(),
  });

// ─── PETrace ──────────────────────────────────────────────────────────────────

export interface PETraceBatch {
  batch_no: number;
  batch_date: string;
  tracer_num: number;
  tracer_name: string;
  site: string;
  duration_s: number;
  row_count: number;
  foil_no: number | null;
  peak_target_uA: number;
  avg_target_uA: number;
  total_muAh: number;
  avg_arc_I: number;
  avg_vacuum_P: number;
  peak_vacuum_P: number;
  rf_efficiency: number;
}

export interface PETraceSummary {
  batch_count: number;
  last_batch_no: number | null;
  first_date: string;
  last_date: string;
  total_muAh: number;
  current_foil: number | null;
  recent_batches: PETraceBatch[];
  foil_changes: Array<{ batch_no: number; batch_date: string; old_foil: number; new_foil: number }>;
}

export const getPETraceSummary = () =>
  request<PETraceSummary>('/api/petrace/summary');

export const getPETraceBatches = (page = 1) =>
  request<Paged<PETraceBatch>>(`/api/petrace/batches?page=${page}`);

export const getPETraceDashboard = () =>
  request<DashboardData>('/api/petrace/dashboard');

// ─── Ask AI ───────────────────────────────────────────────────────────────────

export const askAI = (question: string) =>
  request<{ answer: string; model: string }>('/api/ask', {
    method: 'POST',
    body: JSON.stringify({ question }),
  }, Config.API_ASK_TIMEOUT_MS);

// ─── Push notifications ───────────────────────────────────────────────────────

export const registerPushToken = (token: string, platform: 'ios' | 'android') =>
  request('/api/push/register', {
    method: 'POST',
    body: JSON.stringify({ token, platform }),
  });
