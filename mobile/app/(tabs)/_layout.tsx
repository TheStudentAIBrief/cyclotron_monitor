import { useEffect } from 'react';
import { Platform, TouchableOpacity } from 'react-native';
import { Tabs, useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import * as Notifications from 'expo-notifications';
import { logout } from '../../services/auth';
import { registerPushToken } from '../../services/api';

// Configure how notifications are displayed when the app is foregrounded
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
  }),
});

export default function TabLayout() {
  const router = useRouter();

  // Register push token once after login
  useEffect(() => {
    (async () => {
      try {
        const { status } = await Notifications.requestPermissionsAsync();
        if (status !== 'granted') return;
        const tokenData = await Notifications.getExpoPushTokenAsync();
        await registerPushToken(tokenData.data, Platform.OS as 'ios' | 'android');
      } catch {
        // Push registration is non-critical — don't surface errors
      }
    })();
  }, []);

  async function handleLogout() {
    await logout();
    router.replace('/(auth)/login');
  }

  return (
    <Tabs
      screenOptions={{
        headerStyle: { backgroundColor: '#1a1a2e' },
        headerTintColor: '#e0e0e0',
        headerTitleStyle: { fontWeight: '600' },
        headerRight: () => (
          <TouchableOpacity onPress={handleLogout} style={{ marginRight: 16 }}>
            <Ionicons name="log-out-outline" size={22} color="#666" />
          </TouchableOpacity>
        ),
        tabBarStyle: { backgroundColor: '#0d0d1f', borderTopColor: '#2a2a4a' },
        tabBarActiveTintColor: '#4a9eff',
        tabBarInactiveTintColor: '#555',
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: 'Dashboard',
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="pulse" size={size} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="gauges"
        options={{
          title: 'Gauge Log',
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="camera-outline" size={size} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="records"
        options={{
          title: 'Records',
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="document-text-outline" size={size} color={color} />
          ),
        }}
      />
    </Tabs>
  );
}
