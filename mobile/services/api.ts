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

// ─── Core request ─────────────────────────────────────────────────────────────

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = await getAccessToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(options.headers as Record<string, string> ?? {}),
  };

  let res = await fetch(`${Config.API_URL}${path}`, { ...options, headers });

  if (res.status === 401 && token) {
    // Access token expired — attempt refresh then retry once
    try {
      await refreshAccessToken();
      const newToken = await getAccessToken();
      const retryHeaders = { ...headers, Authorization: `Bearer ${newToken ?? ''}` };
      res = await fetch(`${Config.API_URL}${path}`, { ...options, headers: retryHeaders });
    } catch {
      await logout();
      throw new Error('Session expired. Please log in again.');
    }
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
  });

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

// ─── Push notifications ───────────────────────────────────────────────────────

export const registerPushToken = (token: string, platform: 'ios' | 'android') =>
  request('/api/push/register', {
    method: 'POST',
    body: JSON.stringify({ token, platform }),
  });
