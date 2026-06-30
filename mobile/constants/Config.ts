const Config = {
  API_URL: process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000',
  // Timeout for fast endpoints (dashboard, gauges, records).
  // 30s gives WiFi cold-start enough headroom (10s was observed to timeout on WiFi).
  // Override with EXPO_PUBLIC_API_TIMEOUT_MS.
  API_TIMEOUT_MS: Number(process.env.EXPO_PUBLIC_API_TIMEOUT_MS) || 30000,
  // Gauge photo OCR runs qwen2.5vl:7b via Ollama — needs ≥2 min on CPU.
  // Override with EXPO_PUBLIC_API_OCR_TIMEOUT_MS.
  API_OCR_TIMEOUT_MS: Number(process.env.EXPO_PUBLIC_API_OCR_TIMEOUT_MS) || 120000,
  // Ask AI runs a local LLM (mistral:7b) which can take several minutes on CPU.
  // This timeout must exceed the Ollama generate timeout on the server side (600s).
  API_ASK_TIMEOUT_MS: Number(process.env.EXPO_PUBLIC_API_ASK_TIMEOUT_MS) || 600000,
};

export default Config;
