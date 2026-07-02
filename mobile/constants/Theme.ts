// PetBMS signature palette — sourced from PET Labs Pharmaceuticals' real
// branding (petlabs.co.za + the email-signature logo), 2026-07-01.
export const Colors = {
  primary: '#1863DC',
  primaryDark: '#0056A7',
  ink: '#212121',
  surface: '#F4F4F4',
  surfaceAlt: '#EBEBEB',
  white: '#FFFFFF',

  // Alert-state colors — match the dashboard's existing RED/ORANGE/YELLOW/GREEN chip semantics.
  alertRed: '#D6304A',
  alertOrange: '#E8862E',
  alertYellow: '#E8C22E',
  alertGreen: '#2E9E6B',

  // Dark-mode chrome tokens — screens under app/ keep a dark shell; these consolidate
  // the previously-inline hex used for that chrome (Task 8).
  tabInactive: '#8A8A9A',
  surfaceDark: '#16213E',
  surfaceDarkAlt: '#0D0D1F',
  surfaceDarkBlue: '#0D1A2E',
  borderDark: '#2A2A5A',
  borderBlue: '#1E3A5F',
  textMuted: '#6B7A99',
  textMutedBlue: '#AAC4E8',
  accentPurple: '#CC88FF',

  // Alert-state background/border/text variants used by banners, cards and chips.
  alertRedBg: '#3A0A0A',
  alertRedBorder: '#7A1515',
  alertRedLight: '#FFAAAA',
  alertRedMuted: '#CC8888',
  alertGreenBg: '#1D4D2E',
  alertOrangeBg: '#7A3500',
};

export const Theme = {
  colors: Colors,
  spacing: { xs: 4, sm: 8, md: 16, lg: 24, xl: 32 },
  radius: { sm: 6, md: 12, lg: 20 },
  typography: {
    title: { fontSize: 20, fontWeight: '700' as const },
    body: { fontSize: 15, fontWeight: '400' as const },
    caption: { fontSize: 12, fontWeight: '500' as const },
  },
};
