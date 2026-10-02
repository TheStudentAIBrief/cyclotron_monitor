/**
 * The Ask screen shows each component's real status under every AI answer.
 * The status comes from the server's data, not from the model, so an answer the
 * model got wrong (or was talked into by planted text) is never the only thing
 * the user sees.
 */
import { statusColor, statusText } from '../services/askStatus';
import { Colors } from '../constants/Theme';

test('a level and an estimate are shown together', () => {
  expect(statusText({ name: 'ION SOURCE', alert_level: 'RED', days_estimate: 2 })).toBe('RED · 2.0 days');
  expect(statusText({ name: 'FOILS', alert_level: 'GREEN', days_estimate: 40.25 })).toBe('GREEN · 40.3 days');
});

test('a missing estimate is said plainly', () => {
  expect(statusText({ name: 'FOILS', alert_level: 'ORANGE', days_estimate: null })).toBe('ORANGE · no estimate');
});

test('a level the server could not read is shown as a problem, not as fine', () => {
  expect(statusText({ name: 'FOILS', alert_level: 'UNKNOWN', days_estimate: 12 })).toBe('UNKNOWN · 12.0 days');
  expect(statusColor('UNKNOWN')).toBe(Colors.alertRed);
});

test('each level has its usual colour', () => {
  expect(statusColor('RED')).toBe(Colors.alertRed);
  expect(statusColor('ORANGE')).toBe(Colors.alertOrange);
  expect(statusColor('YELLOW')).toBe(Colors.alertYellow);
  expect(statusColor('GREEN')).toBe(Colors.alertGreen);
});
