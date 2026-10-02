import { Colors } from '../constants/Theme';

/** One component's real status, as the server read it from the monitoring data. */
export interface AskStatus {
  name: string;
  alert_level: 'RED' | 'ORANGE' | 'YELLOW' | 'GREEN' | 'UNKNOWN';
  days_estimate: number | null;
}

const LEVEL_COLORS: Record<string, string> = {
  RED: Colors.alertRed,
  ORANGE: Colors.alertOrange,
  YELLOW: Colors.alertYellow,
  GREEN: Colors.alertGreen,
};

/** A level the server could not read is drawn as a problem, never as fine. */
export const statusColor = (level: string): string => LEVEL_COLORS[level] ?? Colors.alertRed;

export const statusText = (s: AskStatus): string =>
  `${s.alert_level} · ${s.days_estimate === null ? 'no estimate' : `${s.days_estimate.toFixed(1)} days`}`;
