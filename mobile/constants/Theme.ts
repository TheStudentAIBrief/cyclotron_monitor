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
