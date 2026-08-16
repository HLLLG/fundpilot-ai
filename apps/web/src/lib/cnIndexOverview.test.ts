import { describe, expect, it } from "vitest";

import {
  CN_INDEX_REFRESH_INTERVAL_MS,
  cnIndexRefreshIntervalMs,
  formatCnIndexCaption,
  overviewFromDailyRows,
  parseApiErrorDetail,
  quoteFromDailyPoints,
} from "@/lib/cnIndexOverview";

describe("cnIndexOverview daily fallback", () => {
  it("builds last close and day change from two daily points", () => {
    const quote = quoteFromDailyPoints("000001", "上证指数", [
      { date: "2026-08-13", close: 3900 },
      { date: "2026-08-14", close: 3927.18 },
    ]);
    expect(quote.status).toBe("ok");
    expect(quote.last_price).toBe(3927.18);
    expect(quote.change).toBeCloseTo(27.18, 2);
    expect(quote.change_percent).toBeCloseTo(0.6969, 3);
    expect(quote.quote_time).toBe("2026-08-14");
  });

  it("marks empty series unavailable instead of inventing numbers", () => {
    const quote = quoteFromDailyPoints("399001", "深证成指", []);
    expect(quote.status).toBe("unavailable");
    expect(quote.last_price).toBeNull();
    expect(quote.change_percent).toBeNull();
  });

  it("treats any usable index as an available overview", () => {
    const overview = overviewFromDailyRows([
      { symbol: "000001", name: "上证指数", points: [{ date: "2026-08-14", close: 3927.18 }] },
      { symbol: "399001", name: "深证成指", points: [] },
    ]);
    expect(overview.available).toBe(true);
    expect(overview.items).toHaveLength(2);
    expect(overview.trade_date).toBe("2026-08-14");
  });

  it("hides FastAPI Not Found JSON from the UI", () => {
    expect(parseApiErrorDetail('{"detail":"Not Found"}')).toBe("主要指数接口暂不可用");
    expect(parseApiErrorDetail('{"detail":"未登录"}')).toBe("未登录");
  });
});

describe("cnIndexRefreshIntervalMs", () => {
  it("polls only while A-share indices can still move", () => {
    expect(cnIndexRefreshIntervalMs("trading_day_intraday")).toBe(CN_INDEX_REFRESH_INTERVAL_MS);
    expect(cnIndexRefreshIntervalMs("trading_day_pre_close")).toBe(CN_INDEX_REFRESH_INTERVAL_MS);
    expect(cnIndexRefreshIntervalMs("trading_day_after_close")).toBeNull();
    expect(cnIndexRefreshIntervalMs("trading_day_pre_open")).toBeNull();
    expect(cnIndexRefreshIntervalMs("non_trading_day")).toBeNull();
  });
});

describe("formatCnIndexCaption", () => {
  it("mirrors the US caption style with session and trade date", () => {
    expect(
      formatCnIndexCaption({ session_kind: "non_trading_day", trade_date: "2026-08-14" }),
    ).toBe("休市 · 2026-08-14");
    expect(
      formatCnIndexCaption({ session_kind: "trading_day_intraday", trade_date: "2026-08-17" }),
    ).toBe("盘中 · 2026-08-17");
    expect(
      formatCnIndexCaption({ session_kind: "trading_day_pre_close", trade_date: "2026-08-17" }),
    ).toBe("盘中 · 2026-08-17");
  });

  it("falls back to the date when session is missing", () => {
    expect(formatCnIndexCaption({ trade_date: "2026-08-14" })).toBe("2026-08-14");
    expect(formatCnIndexCaption({})).toBeNull();
  });
});
