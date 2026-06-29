import * as SecureStore from 'expo-secure-store';
import Config from '../constants/Config';

const ACCESS_KEY = 'petlab_access_token';
const REFRESH_KEY = 'petlab_refresh_token';

export async function login(username: string, password: string): Promise<void> {
  const body = new URLSearchParams({ username, password });
  const res = await fetch(`${Config.API_URL}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: body.toString(),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Login failed' }));
    throw new Error((err as { detail?: string }).detail ?? 'Invalid credentials');
  }
  const { access_token, refresh_token } = await res.json();
  await Promise.all([
    SecureStore.setItemAsync(ACCESS_KEY, access_token),
    SecureStore.setItemAsync(REFRESH_KEY, refresh_token),
  ]);
}

export async function getAccessToken(): Promise<string | null> {
  return SecureStore.getItemAsync(ACCESS_KEY);
}

export async function refreshAccessToken(): Promise<void> {
  const refresh = await SecureStore.getItemAsync(REFRESH_KEY);
  if (!refresh) throw new Error('No refresh token');
  const res = await fetch(`${Config.API_URL}/auth/refresh`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${refresh}` },
  });
  if (!res.ok) throw new Error('Session expired');
  const { access_token, refresh_token } = await res.json();
  await Promise.all([
    SecureStore.setItemAsync(ACCESS_KEY, access_token),
    SecureStore.setItemAsync(REFRESH_KEY, refresh_token),
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
