// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Report } from "@/lib/api";
import {
  DAILY_REPORT_DETAIL_STALE_MS,
  deleteDailyReportDetailCache,
  isDailyReportDetailCacheFresh,
  isDailyReportsListCacheFresh,
  readDailyReportDetailCache,
  readDailyReportsListCache,
  readFreshLatestDailyReport,
  resetDailyReportCacheForTests,
  writeDailyReportDetailCache,
  writeDailyReportsListCache,
} from "@/lib/dailyReportCache";

function report(id: string, title = id): Report {
  return {
    id,
    created_at: "2026-08-14T08:00:00Z",
    title,
    risk: {
      level: "low",
      suggested_action: "watch",
      weighted_return_percent: 0,
      alerts: [],
    },
    holdings: [],
    snapshots: [],
    market_context: [],
    market_news: [],
    fund_recommendations: [],
    summary: "正文",
    recommendations: [],
    caveats: [],
    provider: "test",
    analysis_facts: {},
  };
}

describe("dailyReportCache", () => {
  beforeEach(() => {
    resetDailyReportCacheForTests();
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-14T08:00:00Z"));
  });

  afterEach(() => {
    resetDailyReportCacheForTests();
    vi.useRealTimers();
  });

  it("returns a fresh latest report within 30 minutes", () => {
    writeDailyReportDetailCache(7, report("rep-1", "今日操作"), { asLatest: true });

    expect(readFreshLatestDailyReport(7)?.title).toBe("今日操作");
    expect(isDailyReportDetailCacheFresh(7, "rep-1")).toBe(true);

    vi.advanceTimersByTime(DAILY_REPORT_DETAIL_STALE_MS - 1);
    expect(readFreshLatestDailyReport(7)?.id).toBe("rep-1");
  });

  it("expires the latest report after 30 minutes", () => {
    writeDailyReportDetailCache(7, report("rep-1"), { asLatest: true });
    vi.advanceTimersByTime(DAILY_REPORT_DETAIL_STALE_MS + 1);

    expect(isDailyReportDetailCacheFresh(7, "rep-1")).toBe(false);
    expect(readFreshLatestDailyReport(7)).toBeNull();
  });

  it("does not leak one account's latest report into another", () => {
    writeDailyReportDetailCache(11, report("a-private", "A 的日报"), { asLatest: true });

    expect(readFreshLatestDailyReport(22)).toBeNull();
    expect(readDailyReportDetailCache(22, "a-private")).toBeNull();
    expect(readFreshLatestDailyReport(11)?.title).toBe("A 的日报");
  });

  it("keeps historical details without rewriting the latest pointer", () => {
    writeDailyReportDetailCache(7, report("latest"), { asLatest: true });
    writeDailyReportDetailCache(7, report("older", "旧日报"));

    expect(readFreshLatestDailyReport(7)?.id).toBe("latest");
    expect(readDailyReportDetailCache(7, "older")?.title).toBe("旧日报");
  });

  it("clears the latest pointer when that report is deleted", () => {
    writeDailyReportDetailCache(7, report("rep-1"), { asLatest: true });
    deleteDailyReportDetailCache(7, "rep-1");

    expect(readFreshLatestDailyReport(7)).toBeNull();
    expect(readDailyReportDetailCache(7, "rep-1")).toBeNull();
  });

  it("expires the reports list after 30 minutes", () => {
    writeDailyReportsListCache(7, [report("rep-1")]);
    expect(isDailyReportsListCacheFresh(7)).toBe(true);
    expect(readDailyReportsListCache(7)?.[0]?.id).toBe("rep-1");

    vi.advanceTimersByTime(DAILY_REPORT_DETAIL_STALE_MS + 1);
    expect(isDailyReportsListCacheFresh(7)).toBe(false);
    expect(readDailyReportsListCache(7)?.[0]?.id).toBe("rep-1");
  });
});
