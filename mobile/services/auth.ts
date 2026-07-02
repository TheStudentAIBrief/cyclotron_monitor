import { Platform } from 'react-native';
import * as SecureStore from './secureStorage';
import Config from '../constants/Config';

const ACCESS_KEY = 'petlab_access_token';
const REFRESH_KEY = 'petlab_refresh_token';

// Web (PWA) only: the refresh token is never persisted client-side at all --
// the server mirrors it into an httpOnly, Secure, SameSite=Strict cookie
// (api/auth.py's set_refresh_cookie), which the browser attaches automatically
// to same-origin requests made with `credentials: 'include'`. The access token
// lives in this in-memory variable only, never localStorage, so it doesn't
// survive a page reload -- restoreWebSession() below re-derives it from the
// cookie at app boot instead. This closes the self-pentest finding that a
// stolen access+refresh token pair from localStorage gave durable, replayable
// access to a nuclear-facility monitoring system with no way to revoke it
// short of the token's natural expiry. Native (iOS/Android) is unaffected: it
// keeps using SecureStore/Keychain for both tokens exactly as before.
let _webAccessToken: string | null = null;
const _isWeb = Platform.OS === 'web';

// auth.ts is a plain module, not a React component, so it can't call useAuth()
// directly when api.ts's 401 interceptor forces a logout. RootLayout registers
// a listener here (via setAuthChangeListener) so logout() can flip AuthContext's
// `authed` state to false immediately, instead of leaving the UI on protected
// screens with a cleared token until the next API call fails again.
type AuthChangeListener = () => void;
let authChangeListener: AuthChangeListener | null = null;

export function setAuthChangeListener(listener: AuthChangeListener | null): void {
  authChangeListener = listener;
}

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
    credentials: 'include', // lets the browser store the response's refresh_token cookie
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
  if (_isWeb) {
    _webAccessToken = data.access_token;
    // No client-side refresh-token storage on web -- the server already set
    // it as an httpOnly cookie in the response above.
  } else {
    await Promise.all([
      SecureStore.setItemAsync(ACCESS_KEY, data.access_token),
      SecureStore.setItemAsync(REFRESH_KEY, data.refresh_token),
    ]);
  }
}

export async function getAccessToken(): Promise<string | null> {
  if (_isWeb) return _webAccessToken;
  return SecureStore.getItemAsync(ACCESS_KEY);
}

export async function refreshAccessToken(): Promise<void> {
  const refresh = _isWeb ? null : await SecureStore.getItemAsync(REFRESH_KEY);
  if (!_isWeb && !refresh) throw new Error('No refresh token');
  const res = await timeoutFetch(`${Config.API_URL}/auth/refresh`, {
    method: 'POST',
    // Web sends no Authorization header at all -- the refresh token rides in
    // the httpOnly cookie instead, attached automatically by the browser.
    headers: _isWeb ? {} : { Authorization: `Bearer ${refresh}` },
    credentials: 'include',
  });
  if (!res.ok) throw new Error('Session expired');
  const data = (await res.json().catch(() => null)) as
    | { access_token?: string; refresh_token?: string }
    | null;
  if (!data?.access_token || !data?.refresh_token) {
    throw new Error('Session expired');
  }
  if (_isWeb) {
    _webAccessToken = data.access_token;
  } else {
    await Promise.all([
      SecureStore.setItemAsync(ACCESS_KEY, data.access_token),
      SecureStore.setItemAsync(REFRESH_KEY, data.refresh_token),
    ]);
  }
}

export async function logout(): Promise<void> {
  const token = _isWeb ? _webAccessToken : await SecureStore.getItemAsync(ACCESS_KEY);
  if (token) {
    // Best-effort server-side revocation (fire-and-forget) — the device may be
    // offline, and local logout must not be delayed or blocked by the network.
    timeoutFetch(`${Config.API_URL}/auth/logout`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
      credentials: 'include', // lets the server's Set-Cookie clear the refresh cookie
    }).catch(() => {});
  }
  if (_isWeb) {
    _webAccessToken = null;
  } else {
    await Promise.all([
      SecureStore.deleteItemAsync(ACCESS_KEY),
      SecureStore.deleteItemAsync(REFRESH_KEY),
    ]);
  }
  authChangeListener?.();
}

export async function isLoggedIn(): Promise<boolean> {
  if (_isWeb) return _webAccessToken !== null;
  const token = await SecureStore.getItemAsync(ACCESS_KEY);
  return !!token;
}

// Web only: a page reload always clears the in-memory access token (there's
// nowhere else it's stored), but the httpOnly refresh cookie may still be
// valid. Call once at app boot (see app/_layout.tsx) to silently re-derive an
// access token from that cookie instead of forcing a fresh login every time
// the PWA is reopened/refreshed. Native ignores this entirely -- SecureStore
// already survives an app restart, so isLoggedIn() alone is enough there.
export async function restoreWebSession(): Promise<boolean> {
  if (!_isWeb) return isLoggedIn();
  try {
    await refreshAccessToken();
    return true;
  } catch {
    return false;
  }
}
