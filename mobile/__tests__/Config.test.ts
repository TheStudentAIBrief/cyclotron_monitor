/**
 * TDD: Config reads EXPO_PUBLIC_* env vars at Metro build time.
 *
 * Guard: when the laptop moves from hotspot (172.20.10.2) to WiFi (192.168.x.x),
 * EXPO_PUBLIC_API_URL must be the only thing that changes. These tests catch any
 * regression that would make Config.API_URL ignore the env var and break connectivity.
 *
 * Note: in Jest, process.env.EXPO_PUBLIC_* is a regular env var — Metro's
 * compile-time substitution does not run. jest.resetModules() + require() forces
 * Config to re-evaluate with the new env value each time.
 */

const SAVED = {
  url: process.env.EXPO_PUBLIC_API_URL,
  timeout: process.env.EXPO_PUBLIC_API_TIMEOUT_MS,
  askTimeout: process.env.EXPO_PUBLIC_API_ASK_TIMEOUT_MS,
  ocrTimeout: process.env.EXPO_PUBLIC_API_OCR_TIMEOUT_MS,
};

afterEach(() => {
  if (SAVED.url === undefined) delete process.env.EXPO_PUBLIC_API_URL;
  else process.env.EXPO_PUBLIC_API_URL = SAVED.url;
  if (SAVED.timeout === undefined) delete process.env.EXPO_PUBLIC_API_TIMEOUT_MS;
  else process.env.EXPO_PUBLIC_API_TIMEOUT_MS = SAVED.timeout;
  if (SAVED.askTimeout === undefined) delete process.env.EXPO_PUBLIC_API_ASK_TIMEOUT_MS;
  else process.env.EXPO_PUBLIC_API_ASK_TIMEOUT_MS = SAVED.askTimeout;
  if (SAVED.ocrTimeout === undefined) delete process.env.EXPO_PUBLIC_API_OCR_TIMEOUT_MS;
  else process.env.EXPO_PUBLIC_API_OCR_TIMEOUT_MS = SAVED.ocrTimeout;
  jest.resetModules();
});

function loadConfig() {
  return require('../constants/Config').default as typeof import('../constants/Config').default;
}

// ── API_URL ──────────────────────────────────────────────────────────────────

test('API_URL: uses EXPO_PUBLIC_API_URL when set to WiFi IP', () => {
  process.env.EXPO_PUBLIC_API_URL = 'http://192.168.1.50:8000';
  jest.resetModules();
  expect(loadConfig().API_URL).toBe('http://192.168.1.50:8000');
});

test('API_URL: uses EXPO_PUBLIC_API_URL when set to hotspot IP', () => {
  process.env.EXPO_PUBLIC_API_URL = 'http://172.20.10.2:8000';
  jest.resetModules();
  expect(loadConfig().API_URL).toBe('http://172.20.10.2:8000');
});

test('API_URL: falls back to localhost when EXPO_PUBLIC_API_URL is unset', () => {
  delete process.env.EXPO_PUBLIC_API_URL;
  jest.resetModules();
  expect(loadConfig().API_URL).toBe('http://localhost:8000');
});

describe('API_URL: platform-aware fallback (same-origin deploy)', () => {
  beforeEach(() => {
    jest.resetModules();
  });

  test('web + no EXPO_PUBLIC_API_URL: resolves to "" (relative/same-origin fetch)', () => {
    delete process.env.EXPO_PUBLIC_API_URL;
    jest.doMock('react-native', () => ({ Platform: { OS: 'web' } }));
    expect(loadConfig().API_URL).toBe('');
  });

  test('web + explicit EXPO_PUBLIC_API_URL: explicit value wins', () => {
    process.env.EXPO_PUBLIC_API_URL = 'http://192.168.1.50:8000';
    jest.doMock('react-native', () => ({ Platform: { OS: 'web' } }));
    expect(loadConfig().API_URL).toBe('http://192.168.1.50:8000');
  });

  test('native (ios) + no EXPO_PUBLIC_API_URL: still falls back to localhost', () => {
    delete process.env.EXPO_PUBLIC_API_URL;
    jest.doMock('react-native', () => ({ Platform: { OS: 'ios' } }));
    expect(loadConfig().API_URL).toBe('http://localhost:8000');
  });

  test('native (ios) + explicit EXPO_PUBLIC_API_URL: explicit value wins', () => {
    process.env.EXPO_PUBLIC_API_URL = 'http://172.20.10.2:8000';
    jest.doMock('react-native', () => ({ Platform: { OS: 'ios' } }));
    expect(loadConfig().API_URL).toBe('http://172.20.10.2:8000');
  });
});

// ── API_TIMEOUT_MS ───────────────────────────────────────────────────────────

test('API_TIMEOUT_MS: defaults to 30000 — wide enough for WiFi cold-start', () => {
  delete process.env.EXPO_PUBLIC_API_TIMEOUT_MS;
  jest.resetModules();
  expect(loadConfig().API_TIMEOUT_MS).toBe(30000);
});

test('API_TIMEOUT_MS: default is at least 30000 — 10s causes dashboard/records timeout on WiFi', () => {
  delete process.env.EXPO_PUBLIC_API_TIMEOUT_MS;
  jest.resetModules();
  expect(loadConfig().API_TIMEOUT_MS).toBeGreaterThanOrEqual(30000);
});

test('API_TIMEOUT_MS: can be overridden via env var', () => {
  process.env.EXPO_PUBLIC_API_TIMEOUT_MS = '5000';
  jest.resetModules();
  expect(loadConfig().API_TIMEOUT_MS).toBe(5000);
});

// ── API_ASK_TIMEOUT_MS ───────────────────────────────────────────────────────

test('API_ASK_TIMEOUT_MS: defaults to 600000 (10 min — mistral:7b on CPU)', () => {
  delete process.env.EXPO_PUBLIC_API_ASK_TIMEOUT_MS;
  jest.resetModules();
  expect(loadConfig().API_ASK_TIMEOUT_MS).toBe(600000);
});

test('API_ASK_TIMEOUT_MS: is strictly greater than API_TIMEOUT_MS — Ask AI must not share the short timeout', () => {
  delete process.env.EXPO_PUBLIC_API_TIMEOUT_MS;
  delete process.env.EXPO_PUBLIC_API_ASK_TIMEOUT_MS;
  jest.resetModules();
  const cfg = loadConfig();
  expect(cfg.API_ASK_TIMEOUT_MS).toBeGreaterThan(cfg.API_TIMEOUT_MS);
});

// ── API_OCR_TIMEOUT_MS ───────────────────────────────────────────────────────

test('API_OCR_TIMEOUT_MS: defaults to 900000 — Gemini retry/backoff ceiling (~300s) + Ollama cold-start (15s) + Ollama 600s server timeout', () => {
  delete process.env.EXPO_PUBLIC_API_OCR_TIMEOUT_MS;
  jest.resetModules();
  expect(loadConfig().API_OCR_TIMEOUT_MS).toBe(900000);
});

test('API_OCR_TIMEOUT_MS: is at least 900000 — anything less can abort while Gemini retries/Ollama fallback are still legitimately working', () => {
  delete process.env.EXPO_PUBLIC_API_OCR_TIMEOUT_MS;
  jest.resetModules();
  expect(loadConfig().API_OCR_TIMEOUT_MS).toBeGreaterThanOrEqual(900000);
});
