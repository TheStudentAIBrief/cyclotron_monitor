import { shouldForceIdleLogout, IDLE_TIMEOUT_MS } from '../app/_layout';

// MED-19 hardening: a device backgrounded (not force-quit) for a long time
// previously kept its session valid indefinitely. This guards the pure timeout
// decision without needing to mount RootLayout's full expo-router tree.
describe('shouldForceIdleLogout', () => {
  it('returns false when the app was never backgrounded', () => {
    expect(shouldForceIdleLogout(null, Date.now())).toBe(false);
  });

  it('returns false when reopened well within the idle window', () => {
    const backgroundedAt = 1_000_000;
    const now = backgroundedAt + IDLE_TIMEOUT_MS - 1;
    expect(shouldForceIdleLogout(backgroundedAt, now)).toBe(false);
  });

  it('returns true when reopened after the idle window has elapsed', () => {
    const backgroundedAt = 1_000_000;
    const now = backgroundedAt + IDLE_TIMEOUT_MS + 1;
    expect(shouldForceIdleLogout(backgroundedAt, now)).toBe(true);
  });

  it('returns false exactly at the boundary (strictly greater-than, not equal)', () => {
    const backgroundedAt = 1_000_000;
    const now = backgroundedAt + IDLE_TIMEOUT_MS;
    expect(shouldForceIdleLogout(backgroundedAt, now)).toBe(false);
  });
});
