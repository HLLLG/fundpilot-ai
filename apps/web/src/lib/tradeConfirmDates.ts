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

/** 同码同日同方向同金额的占用身份。用来对照已入库条数，不把同一张图里的两笔真买入折成一笔。 */
export function sameDayTransactionKey(input: {
  direction: string;
  fund_code?: string | null;
  fund_name?: string;
  amount_yuan: number;
  trade_time: string;
}): string {
  const day = input.trade_time.trim().slice(0, 10);
  const amount = Number(input.amount_yuan);
  const rounded = Number.isFinite(amount) ? amount.toFixed(2) : String(input.amount_yuan);
  const code = (input.fund_code || "").trim();
  if (code && code !== "000000") {
    return `${code}|${input.direction}|${day}|${rounded}`;
  }
  return `${input.direction}|${input.fund_name ?? ""}|${day}|${rounded}`;
}

export function countSameDayKeys(
  items: Array<{
    direction: string;
    fund_code?: string | null;
    fund_name?: string;
    amount_yuan: number;
    trade_time: string;
  }>,
): Map<string, number> {
  const counts = new Map<string, number>();
  for (const item of items) {
    const key = sameDayTransactionKey(item);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return counts;
}

export function markAlreadyRecordedTransactions<T extends {
  fund_code?: string | null;
  direction: string;
  fund_name?: string;
  amount_yuan: number;
  trade_time: string;
}>(transactions: T[], recordedCounts: Map<string, number>): boolean[] {
  const remaining = new Map(recordedCounts);
  return transactions.map((tx) => {
    if (!tx.fund_code) {
      return false;
    }
    const key = sameDayTransactionKey(tx);
    const available = remaining.get(key) ?? 0;
    if (available <= 0) {
      return false;
    }
    remaining.set(key, available - 1);
    return true;
  });
}
