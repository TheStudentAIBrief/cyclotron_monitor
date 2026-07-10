import 'react-native-gesture-handler';
import { useEffect, useState } from 'react';
import { AppState, AppStateStatus } from 'react-native';
import { Stack, useRouter, useSegments } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { logout, restoreWebSession, setAuthChangeListener } from '../services/auth';
import { AuthContext } from '../contexts/AuthContext';
import { Colors } from '../constants/Theme';

// A stolen/lost device with the app backgrounded (not force-quit) previously kept
// its session valid indefinitely — the token in SecureStore never expired just
// from inactivity. Force re-login after this long away from the app.
export const IDLE_TIMEOUT_MS = 15 * 60 * 1000;

// Extracted as a pure function so the timeout math is unit-testable without
// mounting the whole component tree (RootLayout needs expo-router context).
export function shouldForceIdleLogout(backgroundedAt: number | null, now: number): boolean {
  return backgroundedAt !== null && now - backgroundedAt > IDLE_TIMEOUT_MS;
}

export default function RootLayout() {
  const [checking, setChecking] = useState(true);
  const [authed, setAuthed] = useState(false);
  const router = useRouter();
  const segments = useSegments();

  // api.ts's 401/403 interceptor calls logout() directly (it can't use the
  // useAuth() hook), which otherwise leaves `authed` stale at true — clearing
  // SecureStore but not the in-memory state, so protected screens stay mounted
  // until the next API call also fails. Registering here closes that gap.
  useEffect(() => {
    setAuthChangeListener(() => setAuthed(false));
    return () => setAuthChangeListener(null);
  }, []);

  // Inactivity timeout: note when the app leaves the foreground, and force
  // logout if it's reopened after more than IDLE_TIMEOUT_MS away.
  useEffect(() => {
    let backgroundedAt: number | null = null;
    const sub = AppState.addEventListener('change', (next: AppStateStatus) => {
      if (next === 'background' || next === 'inactive') {
        backgroundedAt = Date.now();
      } else if (next === 'active') {
        if (shouldForceIdleLogout(backgroundedAt, Date.now())) {
          logout();
        }
        backgroundedAt = null;
      }
    });
    return () => sub.remove();
  }, []);

  // One-time auth check on mount. On native this reads SecureStore (which can
  // REJECT -- keychain locked / first launch / simulator -- so it MUST be
  // guarded or `checking` never flips to false and the app hangs on a blank
  // screen forever). On web, the in-memory access token is always gone after a
  // reload, so restoreWebSession() attempts a silent refresh via the httpOnly
  // cookie instead of just reporting "logged out".
  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const authed = await restoreWebSession();
        if (mounted) setAuthed(authed);
      } catch {
        if (mounted) setAuthed(false);
      } finally {
        if (mounted) setChecking(false);
      }
    })();
    return () => {
      mounted = false;
    };
  }, []);

  // Redirect based on auth state. `segments` is the only value that needs to be
  // in the dep array besides the state flags; `router` from expo-router is a
  // stable reference so it does not cause re-render loops.
  useEffect(() => {
    if (checking) return;
    const inAuth = segments[0] === '(auth)';
    if (!authed && !inAuth) {
      router.replace('/(auth)/login');
    } else if (authed && inAuth) {
      router.replace('/(tabs)');
    }
  }, [authed, checking, segments, router]);

  // Render nothing but the gesture root until the auth check resolves.
  // Returning <Stack/> early mounts the initial (tabs)/index route, which fires
  // getDashboard() against the API and flashes protected UI before the redirect.
  if (checking) {
    return (
      <AuthContext.Provider value={{ setAuthed }}>
        <GestureHandlerRootView style={{ flex: 1, backgroundColor: Colors.ink }}>
          <StatusBar style="light" />
        </GestureHandlerRootView>
      </AuthContext.Provider>
    );
  }

  return (
    <AuthContext.Provider value={{ setAuthed }}>
      <GestureHandlerRootView style={{ flex: 1 }}>
        <StatusBar style="light" />
        <Stack screenOptions={{ headerShown: false }} />
      </GestureHandlerRootView>
    </AuthContext.Provider>
  );
}
