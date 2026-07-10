/**
 * Web refresh-token cookie migration (self-pentest F-04 remediation): on web,
 * the refresh token must never be persisted client-side (the server mirrors it
 * into an httpOnly cookie instead, see api/auth.py's set_refresh_cookie), and
 * the access token lives in memory only, not localStorage. Native is
 * unaffected and keeps using SecureStore for both tokens exactly as before.
 */

function fetchOk(body: Record<string, unknown>) {
  return jest.fn().mockResolvedValue({
    ok: true,
    json: async () => body,
  });
}

describe('auth service on native (Platform.OS=ios)', () => {
  beforeEach(() => {
    jest.resetModules();
    jest.doMock('react-native', () => ({ Platform: { OS: 'ios' } }));
    jest.doMock('expo-secure-store', () => ({
      setItemAsync: jest.fn().mockResolvedValue(undefined),
      getItemAsync: jest.fn().mockResolvedValue(null),
      deleteItemAsync: jest.fn().mockResolvedValue(undefined),
    }));
  });

  it('login stores both tokens via SecureStore', async () => {
    global.fetch = fetchOk({ access_token: 'a', refresh_token: 'r' }) as never;
    const auth = require('../services/auth');
    const SecureStore = require('expo-secure-store');
    await auth.login('u', 'p');
    expect(SecureStore.setItemAsync).toHaveBeenCalledWith('petlab_access_token', 'a');
    expect(SecureStore.setItemAsync).toHaveBeenCalledWith('petlab_refresh_token', 'r');
  });

  it('refreshAccessToken sends the stored refresh token as a Bearer header', async () => {
    const SecureStore = require('expo-secure-store');
    SecureStore.getItemAsync.mockResolvedValue('stored-refresh');
    global.fetch = fetchOk({ access_token: 'new-a', refresh_token: 'new-r' }) as never;
    const auth = require('../services/auth');
    await auth.refreshAccessToken();
    const [, options] = (global.fetch as jest.Mock).mock.calls[0];
    expect(options.headers.Authorization).toBe('Bearer stored-refresh');
  });

  it('refreshAccessToken with no stored refresh token throws before ever calling fetch', async () => {
    const SecureStore = require('expo-secure-store');
    SecureStore.getItemAsync.mockResolvedValue(null);
    global.fetch = jest.fn() as never;
    const auth = require('../services/auth');
    await expect(auth.refreshAccessToken()).rejects.toThrow('No refresh token');
    expect(global.fetch).not.toHaveBeenCalled();
  });
});

describe('auth service on web (Platform.OS=web)', () => {
  beforeEach(() => {
    jest.resetModules();
    jest.doMock('react-native', () => ({ Platform: { OS: 'web' } }));
    // @ts-expect-error -- test-only global shim
    global.localStorage = {
      setItem: jest.fn(),
      getItem: jest.fn(() => null),
      removeItem: jest.fn(),
    };
  });

  it('login stores the access token in memory, never in localStorage', async () => {
    global.fetch = fetchOk({ access_token: 'web-a', refresh_token: 'web-r' }) as never;
    const auth = require('../services/auth');
    await auth.login('u', 'p');
    expect(global.localStorage.setItem).not.toHaveBeenCalled();
    expect(await auth.getAccessToken()).toBe('web-a');
  });

  it('login sends credentials:"include" so the browser stores the refresh cookie', async () => {
    global.fetch = fetchOk({ access_token: 'a', refresh_token: 'r' }) as never;
    const auth = require('../services/auth');
    await auth.login('u', 'p');
    const [url, options] = (global.fetch as jest.Mock).mock.calls[0];
    expect(url).toContain('/auth/login');
    expect(options.credentials).toBe('include');
  });

  it('refreshAccessToken sends no Authorization header, relying on the cookie', async () => {
    global.fetch = fetchOk({ access_token: 'a2', refresh_token: 'r2' }) as never;
    const auth = require('../services/auth');
    await auth.refreshAccessToken();
    const [, options] = (global.fetch as jest.Mock).mock.calls[0];
    expect(options.headers.Authorization).toBeUndefined();
    expect(options.credentials).toBe('include');
  });

  it('restoreWebSession returns true and populates the access token when the cookie refresh succeeds', async () => {
    global.fetch = fetchOk({ access_token: 'restored', refresh_token: 'r' }) as never;
    const auth = require('../services/auth');
    const ok = await auth.restoreWebSession();
    expect(ok).toBe(true);
    expect(await auth.getAccessToken()).toBe('restored');
  });

  it('restoreWebSession returns false when there is no valid session cookie', async () => {
    global.fetch = jest.fn().mockResolvedValue({ ok: false, json: async () => ({}) }) as never;
    const auth = require('../services/auth');
    const ok = await auth.restoreWebSession();
    expect(ok).toBe(false);
    expect(await auth.isLoggedIn()).toBe(false);
  });

  it('logout clears the in-memory access token', async () => {
    global.fetch = fetchOk({ access_token: 'a', refresh_token: 'r' }) as never;
    const auth = require('../services/auth');
    await auth.login('u', 'p');
    expect(await auth.isLoggedIn()).toBe(true);
    await auth.logout();
    expect(await auth.isLoggedIn()).toBe(false);
  });
});
