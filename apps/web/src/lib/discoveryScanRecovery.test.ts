import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  DISCOVERY_STREAM_SILENCE_MS,
  detectCompletedScan,
  recoverCompletedDiscoveryReport,
  sortReportsByCreatedAtDesc,
  streamLooksDead,
} from "@/lib/discoveryScanRecovery";
import type { FundDiscoveryReport } from "@/lib/api";
import { fetchDiscoveryReportDetail, listDiscoveryReports } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  listDiscoveryReports: vi.fn(),
  fetchDiscoveryReportDetail: vi.fn(),
}));

// ---------------------------------------------------------------------------
// 手机浏览器切到后台会挂起 fetch 的 reader：`streamDiscovery` 那个 promise 可能永远
// 不 settle，`finally` 不执行，于是 `isSubmitting` 与 `streamingDiscovery` 一直挂着，
// 页面死在「扫描进行中…」；而流式路径从不登记 discoveryJobId，没有任何轮询兜底。
// 报告其实早就生成了（换台设备就能看到）。这里锁定"怎么判断后台已经完成"。
// ---------------------------------------------------------------------------

function report(id: string, createdAt: string) {
  return { id, created_at: createdAt };
}

describe("detectCompletedScan", () => {
  it("reports the newest entry when the latest id changed", () => {
    const recovered = detectCompletedScan({
      reports: [report("new", "2026-08-08T06:00:00Z"), report("old", "2026-08-07T06:00:00Z")],
      knownLatestId: "old",
    });

    expect(recovered?.id).toBe("new");
  });

  it("returns null while the backend has produced nothing new", () => {
    expect(
      detectCompletedScan({
        reports: [report("old", "2026-08-07T06:00:00Z")],
        knownLatestId: "old",
      }),
    ).toBeNull();
  });

  it("treats the first ever report as a completion", () => {
    const recovered = detectCompletedScan({
      reports: [report("first", "2026-08-08T06:00:00Z")],
      knownLatestId: null,
    });

    expect(recovered?.id).toBe("first");
  });

  it("returns null for an empty list", () => {
    expect(detectCompletedScan({ reports: [], knownLatestId: null })).toBeNull();
  });

  it("ignores clock skew entirely", () => {
    // 关键：`created_at` 是服务端时钟，扫描开始时间是浏览器时钟。即使新报告的
    // created_at 看起来比旧报告还早（客户端/服务端时钟不同步），只要列表最前面
    // 那份 id 变了就该判定完成——所以这里刻意不比较时间戳。
    const recovered = detectCompletedScan({
      reports: [report("new", "2020-01-01T00:00:00Z"), report("old", "2026-08-07T06:00:00Z")],
      knownLatestId: "old",
    });

    expect(recovered?.id).toBe("new");
  });
});

describe("sortReportsByCreatedAtDesc", () => {
  it("puts the newest first without mutating the input", () => {
    const input = [
      report("a", "2026-08-01T00:00:00Z"),
      report("c", "2026-08-08T00:00:00Z"),
      report("b", "2026-08-05T00:00:00Z"),
    ];
    const sorted = sortReportsByCreatedAtDesc(input);

    expect(sorted.map((item) => item.id)).toEqual(["c", "b", "a"]);
    expect(input.map((item) => item.id)).toEqual(["a", "c", "b"]);
  });
});

describe("streamLooksDead", () => {
  it("stays patient while the stream keeps emitting", () => {
    // 健康的流每 12s 有一次阶段心跳，所以短暂沉默不能判死。
    expect(streamLooksDead(1_000, 1_000 + 12_000)).toBe(false);
  });

  it("declares the stream dead after the silence window", () => {
    expect(streamLooksDead(1_000, 1_000 + DISCOVERY_STREAM_SILENCE_MS)).toBe(true);
  });

  it("accepts a custom silence window", () => {
    expect(streamLooksDead(0, 5_000, 4_000)).toBe(true);
    expect(streamLooksDead(0, 3_000, 4_000)).toBe(false);
  });
});

describe("recoverCompletedDiscoveryReport", () => {
  beforeEach(() => {
    vi.mocked(listDiscoveryReports).mockReset();
    vi.mocked(fetchDiscoveryReportDetail).mockReset();
  });

  it("hydrates the newest report when the latest id changed", async () => {
    vi.mocked(listDiscoveryReports).mockResolvedValue([
      report("new", "2026-08-08T06:00:00Z") as FundDiscoveryReport,
      report("old", "2026-08-07T06:00:00Z") as FundDiscoveryReport,
    ]);
    vi.mocked(fetchDiscoveryReportDetail).mockResolvedValue({
      id: "new",
      created_at: "2026-08-08T06:00:00Z",
      title: "detail:new",
      summary: "",
      focus_sectors: [],
      target_sectors: [],
      recommendations: [],
      caveats: [],
      provider: "test",
    });

    const recovered = await recoverCompletedDiscoveryReport("old");

    expect(recovered?.id).toBe("new");
    expect(fetchDiscoveryReportDetail).toHaveBeenCalledWith("new");
  });

  it("returns null when nothing new was saved", async () => {
    vi.mocked(listDiscoveryReports).mockResolvedValue([
      report("old", "2026-08-07T06:00:00Z") as FundDiscoveryReport,
    ]);

    await expect(recoverCompletedDiscoveryReport("old")).resolves.toBeNull();
    expect(fetchDiscoveryReportDetail).not.toHaveBeenCalled();
  });
});
