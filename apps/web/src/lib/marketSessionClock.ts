const WEEKEND = new Set(["Sat", "Sun"]);

type ZoneParts = {
  weekday: string;
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
  second: number;
};

function zoneParts(at: Date, timeZone: string): ZoneParts {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    weekday: "short",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).formatToParts(at);
  const value = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? "";
  return {
    weekday: value("weekday"),
    year: Number(value("year")),
    month: Number(value("month")),
    day: Number(value("day")),
    hour: Number(value("hour")),
    minute: Number(value("minute")),
    second: Number(value("second")),
  };
}

function zoneOffsetMs(at: Date, timeZone: string): number {
  const parts = zoneParts(at, timeZone);
  const asUtc = Date.UTC(
    parts.year,
    parts.month - 1,
    parts.day,
    parts.hour,
    parts.minute,
    parts.second,
  );
  return asUtc - at.getTime();
}

function wallClockToUtcMs(
  timeZone: string,
  year: number,
  month: number,
  day: number,
  hour: number,
  minute: number,
): number {
  const utcGuess = Date.UTC(year, month - 1, day, hour, minute, 0);
  let utc = utcGuess - zoneOffsetMs(new Date(utcGuess), timeZone);
  utc = utcGuess - zoneOffsetMs(new Date(utc), timeZone);
  return utc;
}

function shiftCalendarDay(year: number, month: number, day: number, days: number): {
  year: number;
  month: number;
  day: number;
  weekday: string;
} {
  const shifted = new Date(Date.UTC(year, month - 1, day + days));
  const weekday = new Intl.DateTimeFormat("en-US", {
    weekday: "short",
    timeZone: "UTC",
  }).format(shifted);
  return {
    year: shifted.getUTCFullYear(),
    month: shifted.getUTCMonth() + 1,
    day: shifted.getUTCDate(),
    weekday,
  };
}

/** 距下一个工作日墙钟时刻的毫秒数；已过则落到再下一个工作日。 */
export function msUntilNextWeekdayWallClock(
  timeZone: string,
  hour: number,
  minute: number,
  now: Date = new Date(),
): number {
  const current = zoneParts(now, timeZone);
  let year = current.year;
  let month = current.month;
  let day = current.day;
  let weekday = current.weekday;
  const minutesNow = current.hour * 60 + current.minute;
  const targetMinutes = hour * 60 + minute;
  const stillToday = !WEEKEND.has(weekday) && minutesNow < targetMinutes;
  if (!stillToday) {
    ({ year, month, day, weekday } = shiftCalendarDay(year, month, day, 1));
    while (WEEKEND.has(weekday)) {
      ({ year, month, day, weekday } = shiftCalendarDay(year, month, day, 1));
    }
  }
  return Math.max(0, wallClockToUtcMs(timeZone, year, month, day, hour, minute) - now.getTime());
}
