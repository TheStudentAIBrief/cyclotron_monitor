import { Platform } from 'react-native';
import * as SecureStore from 'expo-secure-store';

// expo-secure-store has no web implementation — its web shim
// (ExpoSecureStore.web.js) exports an empty object, so calling
// SecureStore.setItemAsync() on web throws
// "ExpoSecureStore.setValueWithKeyAsync is not a function". Fall back to
// localStorage on web (the PWA build); native platforms keep using the
// Keychain/Keystore-backed SecureStore as before.
export async function setItemAsync(key: string, value: string): Promise<void> {
  if (Platform.OS === 'web') {
    localStorage.setItem(key, value);
    return;
  }
  await SecureStore.setItemAsync(key, value);
}

export async function getItemAsync(key: string): Promise<string | null> {
  if (Platform.OS === 'web') {
    return localStorage.getItem(key);
  }
  return SecureStore.getItemAsync(key);
}

export async function deleteItemAsync(key: string): Promise<void> {
  if (Platform.OS === 'web') {
    localStorage.removeItem(key);
    return;
  }
  await SecureStore.deleteItemAsync(key);
}
