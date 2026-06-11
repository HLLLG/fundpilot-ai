/** A 股分时 X 轴：09:30–11:30 占左半，13:00–15:00 占右半（共 240 分钟）。 */
const OPEN_MINUTES = 9 * 60 + 30;
const MORNING_END_MINUTES = 11 * 60 + 30;
const AFTERNOON_OPEN_MINUTES = 13 * 60;
const CLOSE_MINUTES = 15 * 60;
const SESSION_MINUTES = 240;

export function parseClockMinutes(time: string): number | null {
  const match = /^(\d{1,2}):(\d{2})/.exec(time.trim());
  if (!match) {
    return null;
  }
  const hour = Number.parseInt(match[1], 10);
  const minute = Number.parseInt(match[2], 10);
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) {
    return null;
  }
  return hour * 60 + minute;
}

export function clockToSessionRatio(time: string): number {
  const mins = parseClockMinutes(time);
  if (mins == null) {
    return 0;
  }
  if (mins <= OPEN_MINUTES) {
    return 0;
  }
  if (mins <= MORNING_END_MINUTES) {
    return (mins - OPEN_MINUTES) / SESSION_MINUTES;
  }
  if (mins < AFTERNOON_OPEN_MINUTES) {
    return (MORNING_END_MINUTES - OPEN_MINUTES) / SESSION_MINUTES;
  }
  if (mins <= CLOSE_MINUTES) {
    const morningSpan = MORNING_END_MINUTES - OPEN_MINUTES;
    return morningSpan / SESSION_MINUTES + (mins - AFTERNOON_OPEN_MINUTES) / SESSION_MINUTES;
  }
  return 1;
}
