/** 公募基金 15:00 截点：确认净值日与首次计收益日（周末按下一工作日；节假日以后端交易日历为准）。 */

const MARKET_CLOSE_MINUTES = 15 * 60;

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

function formatDate(year: number, month: number, day: number): string {
  return `${year}-${pad(month)}-${pad(day)}`;
}

export function parseTradeDateTime(tradeTime: string): Date | null {
  const text = tradeTime.trim().replace("T", " ").replace(/\//g, "-");
  const match = text.match(
    /^(\d{4})-(\d{2})-(\d{2})(?:\s+(\d{2}):(\d{2})(?::(\d{2}))?)?$/,
  );
  if (!match) {
    return null;
  }
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4] ?? "0");
  const minute = Number(match[5] ?? "0");
  const second = Number(match[6] ?? "0");
  const parsed = new Date(year, month - 1, day, hour, minute, second);
  if (
    parsed.getFullYear() !== year ||
    parsed.getMonth() !== month - 1 ||
    parsed.getDate() !== day
  ) {
    return null;
  }
  return parsed;
}

function isWeekday(value: Date): boolean {
  const weekday = value.getDay();
  return weekday !== 0 && weekday !== 6;
}

function nextWeekday(value: Date): Date {
  const cursor = new Date(value.getFullYear(), value.getMonth(), value.getDate());
  for (let index = 0; index < 14; index += 1) {
    cursor.setDate(cursor.getDate() + 1);
    if (isWeekday(cursor)) {
      return cursor;
    }
  }
  cursor.setDate(cursor.getDate() + 1);
  return cursor;
}

function isoDate(value: Date): string {
  return formatDate(value.getFullYear(), value.getMonth() + 1, value.getDate());
}

export function resolveConfirmDate(tradeTime: string): string | null {
  const parsed = parseTradeDateTime(tradeTime);
  if (!parsed) {
    return null;
  }
  const minutes = parsed.getHours() * 60 + parsed.getMinutes();
  if (isWeekday(parsed) && minutes < MARKET_CLOSE_MINUTES) {
    return isoDate(parsed);
  }
  return isoDate(nextWeekday(parsed));
}

export function resolveFirstReturnDate(tradeTime: string): string | null {
  const confirm = resolveConfirmDate(tradeTime);
  if (!confirm) {
    return null;
  }
  const [year, month, day] = confirm.split("-").map(Number);
  return isoDate(nextWeekday(new Date(year, month - 1, day)));
}

export function recordedTransactionKey(input: {
  direction: string;
  fund_code?: string | null;
  amount_yuan: number;
  trade_time: string;
}): string {
  return `${input.fund_code ?? ""}|${input.direction}|${input.trade_time}|${input.amount_yuan}`;
}
