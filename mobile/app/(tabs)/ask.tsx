import { useState, useRef } from 'react';
import {
  View, Text, TextInput, TouchableOpacity, StyleSheet,
  ScrollView, ActivityIndicator, KeyboardAvoidingView, Platform,
} from 'react-native';
import { askAI } from '../../services/api';

export default function AskAIScreen() {
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState('');
  const [model, setModel] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const scrollRef = useRef<ScrollView>(null);

  async function handleAsk() {
    const q = question.trim();
    if (!q || loading) return;
    setLoading(true);
    setAnswer('');
    setError('');
    setModel('');
    try {
      const res = await askAI(q);
      setAnswer(res.answer);
      setModel(res.model);
      setTimeout(() => scrollRef.current?.scrollToEnd({ animated: true }), 100);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'AI unavailable');
    } finally {
      setLoading(false);
    }
  }

  const EXAMPLES = [
    'Which components need attention soon?',
    'What is the ion source status?',
    'Are any components in the red?',
  ];

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      keyboardVerticalOffset={90}
    >
      <ScrollView
        ref={scrollRef}
        contentContainerStyle={styles.scroll}
        keyboardShouldPersistTaps="handled"
      >
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Ask about the cyclotron</Text>
          <Text style={styles.cardSub}>
            Answers from live component data · powered by local AI (Ollama)
          </Text>

          <TextInput
            style={styles.input}
            placeholder="e.g. Which components need maintenance soon?"
            placeholderTextColor="#444"
            value={question}
            onChangeText={setQuestion}
            multiline
            returnKeyType="send"
            onSubmitEditing={handleAsk}
            blurOnSubmit
            editable={!loading}
          />

          <TouchableOpacity
            style={[styles.button, (!question.trim() || loading) && styles.buttonDisabled]}
            onPress={handleAsk}
            disabled={!question.trim() || loading}
            activeOpacity={0.8}
          >
            {loading
              ? <ActivityIndicator color="#04122b" />
              : <Text style={styles.buttonText}>Ask</Text>
            }
          </TouchableOpacity>
        </View>

        {/* Example chips */}
        {!answer && !loading && (
          <View>
            <Text style={styles.exampleHeader}>Try asking</Text>
            {EXAMPLES.map(ex => (
              <TouchableOpacity
                key={ex}
                style={styles.exampleChip}
                onPress={() => setQuestion(ex)}
                activeOpacity={0.7}
              >
                <Text style={styles.exampleText}>{ex}</Text>
              </TouchableOpacity>
            ))}
          </View>
        )}

        {loading && (
          <View style={styles.thinking}>
            <ActivityIndicator size="small" color="#4a9eff" />
            <Text style={styles.thinkingText}>Thinking… up to ~60 s</Text>
          </View>
        )}

        {error ? (
          <View style={styles.errorCard}>
            <Text style={styles.errorTitle}>AI unavailable</Text>
            <Text style={styles.errorBody}>{error}</Text>
            <Text style={styles.errorHint}>
              Start Ollama: <Text style={styles.code}>ollama serve</Text>
            </Text>
          </View>
        ) : null}

        {answer ? (
          <View style={styles.answerCard}>
            <Text style={styles.answerTitle}>Answer</Text>
            <Text style={styles.answerText}>{answer}</Text>
            {model ? <Text style={styles.modelLabel}>{model}</Text> : null}
          </View>
        ) : null}
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#1a1a2e' },
  scroll: { padding: 16, paddingBottom: 32 },

  card: {
    backgroundColor: '#16213e',
    borderRadius: 10,
    padding: 16,
    borderWidth: 1,
    borderColor: '#2a2a5a',
    borderLeftWidth: 4,
    borderLeftColor: '#4a9eff',
    marginBottom: 16,
  },
  cardTitle: { color: '#e0e0e0', fontSize: 16, fontWeight: '700', marginBottom: 4 },
  cardSub: { color: '#6b7a99', fontSize: 12, marginBottom: 14 },

  input: {
    backgroundColor: '#0d0d1f',
    color: '#e0e0e0',
    borderRadius: 8,
    borderWidth: 1,
    borderColor: '#2a2a5a',
    paddingHorizontal: 14,
    paddingVertical: 12,
    fontSize: 14,
    minHeight: 72,
    textAlignVertical: 'top',
    marginBottom: 12,
  },
  button: {
    backgroundColor: '#4a9eff',
    borderRadius: 8,
    paddingVertical: 13,
    alignItems: 'center',
  },
  buttonDisabled: { opacity: 0.45 },
  buttonText: { color: '#04122b', fontSize: 15, fontWeight: '700' },

  exampleHeader: { color: '#555', fontSize: 11, marginBottom: 8, textTransform: 'uppercase' },
  exampleChip: {
    backgroundColor: '#16213e',
    borderRadius: 8,
    borderWidth: 1,
    borderColor: '#2a2a5a',
    padding: 12,
    marginBottom: 8,
  },
  exampleText: { color: '#aac4e8', fontSize: 13 },

  thinking: { flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 16 },
  thinkingText: { color: '#555', fontSize: 13 },

  errorCard: {
    backgroundColor: '#3a0a0a',
    borderRadius: 10,
    padding: 16,
    borderWidth: 1,
    borderColor: '#7a1515',
    marginTop: 16,
  },
  errorTitle: { color: '#ff6b6b', fontSize: 14, fontWeight: '700', marginBottom: 6 },
  errorBody: { color: '#ffaaaa', fontSize: 13, marginBottom: 8 },
  errorHint: { color: '#cc8888', fontSize: 12 },
  code: { fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace', color: '#ffaaaa' },

  answerCard: {
    backgroundColor: '#16213e',
    borderRadius: 10,
    padding: 16,
    borderWidth: 1,
    borderColor: '#2a2a5a',
    borderLeftWidth: 4,
    borderLeftColor: '#4a9eff',
    marginTop: 16,
  },
  answerTitle: { color: '#4a9eff', fontSize: 13, fontWeight: '700', marginBottom: 10 },
  answerText: { color: '#e0e0e0', fontSize: 14, lineHeight: 22 },
  modelLabel: { color: '#444', fontSize: 11, marginTop: 12 },
});
