import { Platform } from 'react-native';

const Config = {
  // On web with no explicit override, default to '' (same-origin/relative fetch) since
  // the API is served from the same domain, which is unknown at build time (e.g. Render).
  API_URL: process.env.EXPO_PUBLIC_API_URL ?? (Platform.OS === 'web' ? '' : 'http://localhost:8000'),
  // Timeout for fast endpoints (dashboard, gauges, records).
  // 30s gives WiFi cold-start enough headroom (10s was observed to timeout on WiFi).
  // Override with EXPO_PUBLIC_API_TIMEOUT_MS.
  API_TIMEOUT_MS: Number(process.env.EXPO_PUBLIC_API_TIMEOUT_MS) || 30000,
  // Gauge photo OCR: Gemini is primary (up to 4 attempts, each up to 60s network
  // timeout, plus backoff sleeps up to 30s each — worst case ~300s), falling back
  // to Ollama (up to 15s cold-start + up to 600s server-side generate timeout).
  // Worst case across both phases can approach 900s, so the client must not abort sooner.
  // Override with EXPO_PUBLIC_API_OCR_TIMEOUT_MS.
  API_OCR_TIMEOUT_MS: Number(process.env.EXPO_PUBLIC_API_OCR_TIMEOUT_MS) || 900000,
  // Ask AI runs a local LLM (mistral:7b) which can take several minutes on CPU.
  // This timeout must exceed the Ollama generate timeout on the server side (600s).
  API_ASK_TIMEOUT_MS: Number(process.env.EXPO_PUBLIC_API_ASK_TIMEOUT_MS) || 600000,
};

export default Config;
