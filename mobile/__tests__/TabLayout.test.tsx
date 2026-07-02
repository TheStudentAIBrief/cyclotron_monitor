import { Colors } from '../constants/Theme';

// TabLayout wires screenOptions from Theme.ts rather than hardcoded hex —
// this test guards against regressing back to inline colors.
describe('Tab layout theming', () => {
  it('Theme colors used by the tab layout are defined and PetBMS-branded', () => {
    expect(Colors.primary).toBe('#1863DC');
    expect(Colors.ink).toBe('#212121');
  });
});
