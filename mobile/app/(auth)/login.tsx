import { useState } from 'react';
import {
  View, Text, TextInput, TouchableOpacity, StyleSheet,
  KeyboardAvoidingView, Platform, ActivityIndicator, ScrollView,
} from 'react-native';
import { login } from '../../services/auth';
import { useAuth } from '../../contexts/AuthContext';
import { Colors } from '../../constants/Theme';

export default function LoginScreen() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const { setAuthed } = useAuth();

  async function handleLogin() {
    if (!username.trim() || !password) {
      setError('Enter username and password');
      return;
    }
    setLoading(true);
    setError('');
    try {
      await login(username.trim(), password);
      // Update root layout state — it owns all navigation decisions.
      // Calling router.replace here would race with the root layout's redirect
      // effect (which still sees authed=false) and boot the user back to login.
      setAuthed(true);
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
              ? <ActivityIndicator color={Colors.white} />
              : <Text style={styles.buttonText}>Sign In</Text>
            }
          </TouchableOpacity>
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.ink },
  scroll: { flexGrow: 1, justifyContent: 'center', alignItems: 'center', padding: 24 },
  card: {
    width: '100%',
    maxWidth: 380,
    backgroundColor: Colors.surfaceDark,
    borderRadius: 14,
    padding: 28,
    borderWidth: 1,
    borderColor: Colors.borderDark,
  },
  title: {
    color: Colors.white,
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
    color: Colors.alertRed,
    fontSize: 13,
    marginBottom: 14,
    textAlign: 'center',
    backgroundColor: Colors.alertRedBg,
    padding: 10,
    borderRadius: 6,
    overflow: 'hidden',
  },
  input: {
    backgroundColor: Colors.surfaceDarkAlt,
    color: Colors.white,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: Colors.borderDark,
    paddingHorizontal: 14,
    paddingVertical: 13,
    fontSize: 15,
    marginBottom: 12,
  },
  button: {
    backgroundColor: Colors.primary,
    borderRadius: 8,
    paddingVertical: 14,
    alignItems: 'center',
    marginTop: 6,
  },
  buttonDisabled: { opacity: 0.55 },
  buttonText: { color: Colors.white, fontSize: 15, fontWeight: '600' },
});
