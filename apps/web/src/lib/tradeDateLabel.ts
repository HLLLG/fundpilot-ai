/** ISO 日期（YYYY-MM-DD）→ 短标签「MM-DD」。 */
export function formatTradeDateShort(isoDate: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoDate);
  if (!match) {
    return isoDate;
  }
  return `${match[2]}-${match[3]}`;
}

type HoldingsColumnDateSession = {
  is_trading_day?: boolean;
  calendar_date?: string | null;
  effective_trade_date?: string | null;
};

/**
 * 当日收益 / 关联板块 / 持有收益列头日期：
 * 交易日用当天；休市用上一交易日（effective_trade_date 已按交易日历回退）。
 */
export function holdingsColumnAsOfIso(session: HoldingsColumnDateSession): string | null {
  if (session.is_trading_day) {
    return session.calendar_date || session.effective_trade_date || null;
  }
  return session.effective_trade_date || session.calendar_date || null;
}

export function formatHoldingsColumnDateShort(
  session: HoldingsColumnDateSession,
): string | null {
  const iso = holdingsColumnAsOfIso(session);
  return iso ? formatTradeDateShort(iso) : null;
}
