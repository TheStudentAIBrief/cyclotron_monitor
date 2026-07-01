import { TouchableOpacity } from 'react-native';
import { Tabs } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { logout } from '../../services/auth';
import { useAuth } from '../../contexts/AuthContext';
import { Colors } from '../../constants/Theme';

export default function TabLayout() {
  const { setAuthed } = useAuth();

  async function handleLogout() {
    await logout();
    setAuthed(false);
  }

  return (
    <Tabs
      screenOptions={{
        headerStyle: { backgroundColor: Colors.primaryDark },
        headerTintColor: Colors.white,
        headerTitleStyle: { fontWeight: '600' },
        headerRight: () => (
          <TouchableOpacity onPress={handleLogout} style={{ marginRight: 16 }}>
            <Ionicons name="log-out-outline" size={22} color={Colors.surfaceAlt} />
          </TouchableOpacity>
        ),
        tabBarStyle: { backgroundColor: Colors.ink, borderTopColor: Colors.primaryDark },
        tabBarActiveTintColor: Colors.primary,
        tabBarInactiveTintColor: '#8A8A9A',
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
        name="petrace"
        options={{
          title: 'PETrace',
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="radio-outline" size={size} color={color} />
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
