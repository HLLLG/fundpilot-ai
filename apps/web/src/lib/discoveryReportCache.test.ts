// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FundDiscoveryReport } from "@/lib/api";
import {
  DISCOVERY_REPORT_DETAIL_STALE_MS,
  deleteDiscoveryReportDetailCache,
  isDiscoveryReportDetailCacheFresh,
  readDiscoveryReportDetailCache,
  readFreshLatestDiscoveryReport,
  resetDiscoveryReportCacheForTests,
  writeDiscoveryReportDetailCache,
} from "@/lib/discoveryReportCache";

function report(id: string, title = id): FundDiscoveryReport {
  return {
    id,
    created_at: "2026-08-14T08:00:00Z",
    title,
    summary: "正文",
    focus_sectors: [],
    target_sectors: [],
    recommendations: [],
    caveats: [],
    provider: "test",
    discovery_facts: {},
  };
}

describe("discoveryReportCache", () => {
  beforeEach(() => {
    resetDiscoveryReportCacheForTests();
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-14T08:00:00Z"));
  });

  afterEach(() => {
    resetDiscoveryReportCacheForTests();
    vi.useRealTimers();
  });

  it("returns a fresh latest report within 30 minutes", () => {
    writeDiscoveryReportDetailCache(7, report("rep-1", "煤炭机会"), { asLatest: true });

    expect(readFreshLatestDiscoveryReport(7)?.title).toBe("煤炭机会");
    expect(isDiscoveryReportDetailCacheFresh(7, "rep-1")).toBe(true);

    vi.advanceTimersByTime(DISCOVERY_REPORT_DETAIL_STALE_MS - 1);
    expect(readFreshLatestDiscoveryReport(7)?.id).toBe("rep-1");
  });

  it("expires the latest report after 30 minutes", () => {
    writeDiscoveryReportDetailCache(7, report("rep-1"), { asLatest: true });
    vi.advanceTimersByTime(DISCOVERY_REPORT_DETAIL_STALE_MS + 1);

    expect(isDiscoveryReportDetailCacheFresh(7, "rep-1")).toBe(false);
    expect(readFreshLatestDiscoveryReport(7)).toBeNull();
  });

  it("does not leak one account's latest report into another", () => {
    writeDiscoveryReportDetailCache(11, report("a-private", "A 的报告"), { asLatest: true });

    expect(readFreshLatestDiscoveryReport(22)).toBeNull();
    expect(readDiscoveryReportDetailCache(22, "a-private")).toBeNull();
    expect(readFreshLatestDiscoveryReport(11)?.title).toBe("A 的报告");
  });

  it("keeps historical details without rewriting the latest pointer", () => {
    writeDiscoveryReportDetailCache(7, report("latest"), { asLatest: true });
    writeDiscoveryReportDetailCache(7, report("older", "旧报告"));

    expect(readFreshLatestDiscoveryReport(7)?.id).toBe("latest");
    expect(readDiscoveryReportDetailCache(7, "older")?.title).toBe("旧报告");
  });

  it("clears the latest pointer when that report is deleted", () => {
    writeDiscoveryReportDetailCache(7, report("rep-1"), { asLatest: true });
    deleteDiscoveryReportDetailCache(7, "rep-1");

    expect(readFreshLatestDiscoveryReport(7)).toBeNull();
    expect(readDiscoveryReportDetailCache(7, "rep-1")).toBeNull();
  });
});
