import { View, Text, StyleSheet } from 'react-native';

type Level = 'RED' | 'ORANGE' | 'YELLOW' | 'GREEN';

const STYLES: Record<Level, { bg: string; fg: string; label: string }> = {
  RED:    { bg: '#6b1d1d', fg: '#ff6b6b', label: 'RED — CRITICAL' },
  ORANGE: { bg: '#7a3500', fg: '#ffb347', label: 'ORANGE — SOON' },
  YELLOW: { bg: '#7a5a00', fg: '#ffe066', label: 'YELLOW — WATCH' },
  GREEN:  { bg: '#1d4d2e', fg: '#7aff7a', label: 'GREEN — OK' },
};

export default function AlertBadge({ level }: { level: Level | string }) {
  const s = STYLES[level as Level] ?? STYLES.GREEN;
  return (
    <View style={[styles.badge, { backgroundColor: s.bg }]}>
      <Text style={[styles.text, { color: s.fg }]}>{s.label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  badge: { borderRadius: 5, paddingHorizontal: 8, paddingVertical: 4 },
  text: { fontSize: 11, fontWeight: '700' },
});
