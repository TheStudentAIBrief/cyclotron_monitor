module.exports = function (api) {
  api.cache(true);
  return {
    presets: ['babel-preset-expo'],
    plugins: [
      // react-native-reanimated/plugin MUST be listed last. babel-preset-expo
      // already auto-injects this when reanimated is installed, so listing it
      // here is a safe no-op (the plugin self-guards against double execution)
      // and makes the requirement explicit.
      'react-native-reanimated/plugin',
    ],
  };
};
