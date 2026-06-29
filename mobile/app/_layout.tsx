import 'react-native-gesture-handler';
import { useEffect, useState } from 'react';
import { Stack, useRouter, useSegments } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import * as SecureStore from 'expo-secure-store';
import { AuthContext } from '../contexts/AuthContext';

export default function RootLayout() {
  const [checking, setChecking] = useState(true);
  const [authed, setAuthed] = useState(false);
  const router = useRouter();
  const segments = useSegments();

  // One-time auth check on mount. SecureStore can REJECT on iOS (keychain
  // locked / first launch / simulator), so it MUST be guarded or `checking`
  // never flips to false and the app hangs on a blank screen forever.
  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const token = await SecureStore.getItemAsync('petlab_access_token');
        if (mounted) setAuthed(!!token);
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
        <GestureHandlerRootView style={{ flex: 1, backgroundColor: '#1a1a2e' }}>
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
