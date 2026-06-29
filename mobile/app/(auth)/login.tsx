import { useState } from 'react';
import {
  View, Text, TextInput, TouchableOpacity, StyleSheet,
  KeyboardAvoidingView, Platform, ActivityIndicator, ScrollView,
} from 'react-native';
import { useRouter } from 'expo-router';
import { login } from '../../services/auth';

export default function LoginScreen() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const router = useRouter();

  async function handleLogin() {
    if (!username.trim() || !password) {
      setError('Enter username and password');
      return;
    }
    setLoading(true);
    setError('');
    try {
      await login(username.trim(), password);
      router.replace('/(tabs)');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Login failed');
    } finally {
      setLoading(false);
    }
  }

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView
        contentContainerStyle={styles.scroll}
        keyboardShouldPersistTaps="handled"
      >
        <View style={styles.card}>
          <Text style={styles.title}>PET Lab Monitor</Text>
          <Text style={styles.subtitle}>Siemens Eclipse Cyclotron</Text>

          {error ? <Text style={styles.error}>{error}</Text> : null}

          <TextInput
            style={styles.input}
            placeholder="Username"
            placeholderTextColor="#555"
            autoCapitalize="none"
            autoCorrect={false}
            value={username}
            onChangeText={setUsername}
            editable={!loading}
          />
          <TextInput
            style={styles.input}
            placeholder="Password"
            placeholderTextColor="#555"
            secureTextEntry
            value={password}
            onChangeText={setPassword}
            editable={!loading}
            onSubmitEditing={handleLogin}
            returnKeyType="done"
          />

          <TouchableOpacity
            style={[styles.button, loading && styles.buttonDisabled]}
            onPress={handleLogin}
            disabled={loading}
            activeOpacity={0.8}
          >
            {loading
              ? <ActivityIndicator color="#fff" />
              : <Text style={styles.buttonText}>Sign In</Text>
            }
          </TouchableOpacity>
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#1a1a2e' },
  scroll: { flexGrow: 1, justifyContent: 'center', alignItems: 'center', padding: 24 },
  card: {
    width: '100%',
    maxWidth: 380,
    backgroundColor: '#16213e',
    borderRadius: 14,
    padding: 28,
    borderWidth: 1,
    borderColor: '#2a2a5a',
  },
  title: {
    color: '#e0e0e0',
    fontSize: 22,
    fontWeight: '700',
    marginBottom: 4,
    textAlign: 'center',
  },
  subtitle: {
    color: '#666',
    fontSize: 13,
    textAlign: 'center',
    marginBottom: 28,
  },
  error: {
    color: '#ff6b6b',
    fontSize: 13,
    marginBottom: 14,
    textAlign: 'center',
    backgroundColor: '#3a0a0a',
    padding: 10,
    borderRadius: 6,
    overflow: 'hidden',
  },
  input: {
    backgroundColor: '#0d0d1f',
    color: '#e0e0e0',
    borderRadius: 8,
    borderWidth: 1,
    borderColor: '#2a2a5a',
    paddingHorizontal: 14,
    paddingVertical: 13,
    fontSize: 15,
    marginBottom: 12,
  },
  button: {
    backgroundColor: '#1a73e8',
    borderRadius: 8,
    paddingVertical: 14,
    alignItems: 'center',
    marginTop: 6,
  },
  buttonDisabled: { opacity: 0.55 },
  buttonText: { color: '#fff', fontSize: 15, fontWeight: '600' },
});
