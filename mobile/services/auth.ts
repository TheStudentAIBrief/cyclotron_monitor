import * as SecureStore from 'expo-secure-store';
import Config from '../constants/Config';

const ACCESS_KEY = 'petlab_access_token';
const REFRESH_KEY = 'petlab_refresh_token';

// Local timeout wrapper. Kept here (rather than imported from api.ts) to avoid
// a circular import, since api.ts already imports from this module. Hermes
// (React Native 0.76) does not reliably implement AbortSignal.timeout(), so use
// an explicit AbortController + setTimeout. Without this, an unreachable API
// host makes fetch() hang on the OS TCP connect timeout, freezing the login
// button on its spinner.
async function timeoutFetch(url: string, options: RequestInit = {}): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), Config.API_TIMEOUT_MS);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } catch (e: unknown) {
    if (e instanceof Error && e.name === 'AbortError') {
      throw new Error(
        `Server did not respond within ${Math.round(Config.API_TIMEOUT_MS / 1000)}s. ` +
        `Check that the monitoring server is running and your network connection.`,
      );
    }
    throw new Error('Cannot reach the monitoring server. Check your network connection.');
  } finally {
    clearTimeout(timer);
  }
}

export async function login(username: string, password: string): Promise<void> {
  const body = new URLSearchParams({ username, password });
  const res = await timeoutFetch(`${Config.API_URL}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: body.toString(),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Login failed' }));
    throw new Error((err as { detail?: string }).detail ?? 'Invalid credentials');
  }
  const data = (await res.json().catch(() => null)) as
    | { access_token?: string; refresh_token?: string }
    | null;
  if (!data?.access_token || !data?.refresh_token) {
    throw new Error('Unexpected response from server.');
  }
  await Promise.all([
    SecureStore.setItemAsync(ACCESS_KEY, data.access_token),
    SecureStore.setItemAsync(REFRESH_KEY, data.refresh_token),
  ]);
}

export async function getAccessToken(): Promise<string | null> {
  return SecureStore.getItemAsync(ACCESS_KEY);
}

export async function refreshAccessToken(): Promise<void> {
  const refresh = await SecureStore.getItemAsync(REFRESH_KEY);
  if (!refresh) throw new Error('No refresh token');
  const res = await timeoutFetch(`${Config.API_URL}/auth/refresh`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${refresh}` },
  });
  if (!res.ok) throw new Error('Session expired');
  const data = (await res.json().catch(() => null)) as
    | { access_token?: string; refresh_token?: string }
    | null;
  if (!data?.access_token || !data?.refresh_token) {
    throw new Error('Session expired');
  }
  await Promise.all([
    SecureStore.setItemAsync(ACCESS_KEY, data.access_token),
    SecureStore.setItemAsync(REFRESH_KEY, data.refresh_token),
  ]);
}

export async function logout(): Promise<void> {
  await Promise.all([
    SecureStore.deleteItemAsync(ACCESS_KEY),
    SecureStore.deleteItemAsync(REFRESH_KEY),
  ]);
}

export async function isLoggedIn(): Promise<boolean> {
  const token = await SecureStore.getItemAsync(ACCESS_KEY);
  return !!token;
}
