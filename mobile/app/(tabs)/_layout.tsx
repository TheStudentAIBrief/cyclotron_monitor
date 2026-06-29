import { TouchableOpacity } from 'react-native';
import { Tabs } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { logout } from '../../services/auth';
import { useAuth } from '../../contexts/AuthContext';

export default function TabLayout() {
  const { setAuthed } = useAuth();

  async function handleLogout() {
    await logout();
    setAuthed(false);
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
      <Tabs.Screen
        name="ask"
        options={{
          title: 'Ask AI',
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="chatbubble-ellipses-outline" size={size} color={color} />
          ),
        }}
      />
    </Tabs>
  );
}
