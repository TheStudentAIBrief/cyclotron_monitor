jest.mock('expo-secure-store', () => ({
  setItemAsync: jest.fn().mockResolvedValue(undefined),
  getItemAsync: jest.fn().mockResolvedValue('native-value'),
  deleteItemAsync: jest.fn().mockResolvedValue(undefined),
}));

describe('secureStorage on native (Platform.OS=ios)', () => {
  beforeEach(() => {
    jest.resetModules();
    jest.doMock('react-native', () => ({ Platform: { OS: 'ios' } }));
  });

  it('setItemAsync delegates to expo-secure-store', async () => {
    const SecureStore = require('expo-secure-store');
    const storage = require('../services/secureStorage');
    await storage.setItemAsync('k', 'v');
    expect(SecureStore.setItemAsync).toHaveBeenCalledWith('k', 'v');
  });

  it('getItemAsync delegates to expo-secure-store', async () => {
    const SecureStore = require('expo-secure-store');
    const storage = require('../services/secureStorage');
    const result = await storage.getItemAsync('k');
    expect(SecureStore.getItemAsync).toHaveBeenCalledWith('k');
    expect(result).toBe('native-value');
  });

  it('deleteItemAsync delegates to expo-secure-store', async () => {
    const SecureStore = require('expo-secure-store');
    const storage = require('../services/secureStorage');
    await storage.deleteItemAsync('k');
    expect(SecureStore.deleteItemAsync).toHaveBeenCalledWith('k');
  });
});

describe('secureStorage on web (Platform.OS=web)', () => {
  let store: Record<string, string>;

  beforeEach(() => {
    jest.resetModules();
    jest.doMock('react-native', () => ({ Platform: { OS: 'web' } }));
    store = {};
    // @ts-expect-error -- test-only global shim, jsdom's localStorage is fine too
    // but this keeps the test explicit about what's being read/written.
    global.localStorage = {
      setItem: jest.fn((k: string, v: string) => { store[k] = v; }),
      getItem: jest.fn((k: string) => (k in store ? store[k] : null)),
      removeItem: jest.fn((k: string) => { delete store[k]; }),
    };
  });

  it('setItemAsync writes to localStorage, not expo-secure-store', async () => {
    const SecureStore = require('expo-secure-store');
    const storage = require('../services/secureStorage');
    await storage.setItemAsync('petlab_access_token', 'web-token');
    expect(global.localStorage.setItem).toHaveBeenCalledWith('petlab_access_token', 'web-token');
    expect(SecureStore.setItemAsync).not.toHaveBeenCalled();
  });

  it('getItemAsync reads from localStorage', async () => {
    const storage = require('../services/secureStorage');
    await storage.setItemAsync('k', 'v');
    const result = await storage.getItemAsync('k');
    expect(result).toBe('v');
  });

  it('getItemAsync returns null for a missing key (matches SecureStore contract)', async () => {
    const storage = require('../services/secureStorage');
    const result = await storage.getItemAsync('missing');
    expect(result).toBeNull();
  });

  it('deleteItemAsync removes from localStorage', async () => {
    const storage = require('../services/secureStorage');
    await storage.setItemAsync('k', 'v');
    await storage.deleteItemAsync('k');
    const result = await storage.getItemAsync('k');
    expect(result).toBeNull();
  });
});
