import { Colors } from '../constants/Theme';

describe('Theme colors', () => {
  it('defines the PetLabs signature palette', () => {
    expect(Colors.primary).toBe('#1863DC');
    expect(Colors.primaryDark).toBe('#0056A7');
    expect(Colors.ink).toBe('#212121');
  });

  it('defines alert-state colors matching existing RED/ORANGE/YELLOW/GREEN chips', () => {
    expect(Colors.alertRed).toBeDefined();
    expect(Colors.alertOrange).toBeDefined();
    expect(Colors.alertYellow).toBeDefined();
    expect(Colors.alertGreen).toBeDefined();
  });

  it('every color value is a valid hex string', () => {
    Object.values(Colors).forEach((v) => {
      expect(typeof v).toBe('string');
      expect(v).toMatch(/^#[0-9A-Fa-f]{6}$/);
    });
  });
});
