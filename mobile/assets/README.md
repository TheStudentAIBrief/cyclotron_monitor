# App Icons

Add the following image files to this directory before building for App Store or Play Store:

| File | Size | Description |
|---|---|---|
| `icon.png` | 1024×1024 | App icon (iOS + Android) |
| `splash-icon.png` | 200×200 | Splash screen logo |
| `adaptive-icon.png` | 1024×1024 | Android adaptive icon foreground |
| `notification-icon.png` | 96×96 | Android push notification icon (white on transparent) |

**Background colour:** `#1a1a2e` (matches the splash background in app.json)

For quick testing with Expo Go, you can copy any PNG from the web as a placeholder — the app will run without custom icons.

To generate correct icon sizes from a single 1024×1024 source:
```
npx expo install expo-asset
npx expo prebuild   # or use EAS Build which handles icons automatically
```
