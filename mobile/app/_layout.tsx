import { useEffect, useState } from 'react';
import { Stack, useRouter, useSegments } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import * as SecureStore from 'expo-secure-store';

export default function RootLayout() {
  const [checking, setChecking] = useState(true);
  const [authed, setAuthed] = useState(false);
  const router = useRouter();
  const segments = useSegments();

  // One-time auth check on mount
  useEffect(() => {
    SecureStore.getItemAsync('petlab_access_token').then(token => {
      setAuthed(!!token);
      setChecking(false);
    });
  }, []);

  // Redirect based on auth state
  useEffect(() => {
    if (checking) return;
    const inAuth = segments[0] === '(auth)';
    if (!authed && !inAuth) {
      router.replace('/(auth)/login');
    } else if (authed && inAuth) {
      router.replace('/(tabs)');
    }
  }, [authed, checking, segments, router]);

  return (
    <>
      <StatusBar style="light" />
      <Stack screenOptions={{ headerShown: false }} />
    </>
  );
}
