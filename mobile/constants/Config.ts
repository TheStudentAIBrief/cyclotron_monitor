const Config = {
  API_URL: process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000',
  // Timeout for fast endpoints (dashboard, gauges, records).
  // 30s gives enough headroom for WiFi cold-start; still short enough to
  // surface genuine unreachability. Override with EXPO_PUBLIC_API_TIMEOUT_MS.
  API_TIMEOUT_MS: Number(process.env.EXPO_PUBLIC_API_TIMEOUT_MS) || 30000,
  // Ask AI runs a local LLM (mistral:7b) which can take 1-3 min on CPU.
  // This timeout must exceed the Ollama generate timeout on the server side.
  API_ASK_TIMEOUT_MS: Number(process.env.EXPO_PUBLIC_API_ASK_TIMEOUT_MS) || 300000,
};

export default Config;
