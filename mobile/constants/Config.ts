const Config = {
  API_URL: process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000',
  // Max time to wait for any single API request before failing fast.
  // Prevents the loading screens from hanging on the OS TCP connect timeout
  // (tens of seconds on iOS) when the API host is unreachable. Override with
  // EXPO_PUBLIC_API_TIMEOUT_MS.
  API_TIMEOUT_MS: Number(process.env.EXPO_PUBLIC_API_TIMEOUT_MS) || 10000,
};

export default Config;
