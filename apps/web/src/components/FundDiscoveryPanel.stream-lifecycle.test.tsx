// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ComponentProps, MutableRefObject } from "react";
import "@testing-library/jest-dom/vitest";

import type {
  FundDiscoveryReport,
  Holding,
  InvestorProfile,
} from "@/lib/api";
import type { StreamingDiscoveryState } from "@/lib/discoveryStreamApi";
import { FundDiscoveryPanel } from "@/components/FundDiscoveryPanel";
import {
  fetchDiscoveryPrompt,
  fetchDiscoveryReportDetail,
  listDiscoveryReports,
  saveDiscoveryPromptRemote,
} from "@/lib/api";
import { streamDiscovery } from "@/lib/discoveryStreamApi";
import { deleteClientCachesWhere } from "@/lib/clientCache";
import { resetDiscoveryReportCacheForTests } from "@/lib/discoveryReportCache";

vi.mock("@/lib/api", () => ({
  fetchDiscoveryPrompt: vi.fn().mockResolvedValue({
    role_prompt: "remote prompt",
    default_role_prompt: "default prompt",
    is_custom: true,
  }),
  fetchDiscoverySectors: vi.fn().mockResolvedValue([]),
  listDiscoveryReports: vi.fn().mockResolvedValue([]),
  // 列表接口只给摘要，选中某份后要按 id 再拉正文。这里回一个能认出来源 id 的标题，
  // 断言"详情确实被拉回并应用了"就不必依赖摘要占位的时序。
  fetchDiscoveryReportDetail: vi.fn(async (reportId: string) => ({
    id: reportId,
    created_at: "2026-07-11T08:00:00Z",
    title: `detail:${reportId}`,
    summary: "正文已载入。",
    focus_sectors: [],
    target_sectors: [],
    recommendations: [],
    caveats: [],
    provider: "test",
  })),
  saveDiscoveryPromptRemote: vi.fn().mockResolvedValue({}),
}));

vi.mock("@/lib/discoveryStreamApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/discoveryStreamApi")>(
    "@/lib/discoveryStreamApi",
  );
  return {
    ...actual,
    streamDiscovery: vi.fn(),
  };
});

vi.mock("@/components/DiscoveryReportPanel", () => ({
  DiscoveryReportPanel: ({ report }: { report: FundDiscoveryReport }) => (
    <section data-testid="discovery-report-stub">{report.title}</section>
  ),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  window.localStorage.clear();
  window.sessionStorage.clear();
  resetDiscoveryReportCacheForTests();
  deleteClientCachesWhere((key) => key.startsWith("discovery-panel:"));
});

function holding(): Holding {
  return {
    fund_code: "519674",
    fund_name: "银河创新成长",
    sector_name: "半导体",
    holding_amount: 10000,
    return_percent: 1.2,
  };
}

function profile(): InvestorProfile {
  return {
    max_drawdown_percent: 15,
    concentration_limit_percent: 35,
    expected_investment_amount: 30000,
    prefer_dca: true,
    avoid_chasing: true,
    round_trip_fee_percent: 1.5,
    min_net_profit_percent: 1,
    hold_days_target: 7,
  };
}

function streamingDiscovery(): StreamingDiscoveryState {
  return {
    stage: "news",
    stageLabel: "拉取市场要闻…",
    fundCodes: ["161725"],
    fundNames: ["招商中证白酒"],
    partialByCode: {},
    stageLog: [{ stage: "news", label: "拉取市场要闻…", at: Date.now() }],
    tokenBuffer: "",
    startedAt: Date.now() - 1000,
  };
}

function discoveryReport(): FundDiscoveryReport {
  return {
    id: "discovery-1",
    created_at: "2026-07-11T08:00:00Z",
    title: "上一份机会报告",
    summary: "保留用于扫描期间阅读。",
    focus_sectors: [],
    target_sectors: ["半导体"],
    recommendations: [],
    caveats: [],
    provider: "test",
  };
}

function panelProps(
  overrides: Partial<ComponentProps<typeof FundDiscoveryPanel>> = {},
): ComponentProps<typeof FundDiscoveryPanel> {
  return {
    userId: 101,
    holdings: [holding()],
    profile: profile(),
    discoveryJobId: null,
    onDiscoveryJobIdChange: vi.fn(),
    pendingDiscoveryReport: null,
    onPendingDiscoveryReportApplied: vi.fn(),
    onRegisterDiscoveryScanRetry: vi.fn(),
    streamingDiscovery: null,
    onStreamingDiscoveryChange: vi.fn(),
    onDiscoveryStreamComplete: vi.fn(),
    discoveryStreamAbortRef: { current: null },
    ...overrides,
  };
}

function renderPanel(overrides: Partial<ComponentProps<typeof FundDiscoveryPanel>> = {}) {
  return render(<FundDiscoveryPanel {...panelProps(overrides)} />);
}

describe("FundDiscoveryPanel stream lifecycle", () => {
  it("does not abort an active discovery stream when the tab unmounts", () => {
    const abort = vi.fn();
    const abortRef = {
      current: { abort },
    } as unknown as MutableRefObject<AbortController | null>;

    const view = render(
      <FundDiscoveryPanel
        userId={101}
        holdings={[holding()]}
        profile={profile()}
        discoveryJobId={null}
        onDiscoveryJobIdChange={vi.fn()}
        pendingDiscoveryReport={null as FundDiscoveryReport | null}
        onPendingDiscoveryReportApplied={vi.fn()}
        onRegisterDiscoveryScanRetry={vi.fn()}
        streamingDiscovery={streamingDiscovery()}
        onStreamingDiscoveryChange={vi.fn()}
        onDiscoveryStreamComplete={vi.fn()}
        discoveryStreamAbortRef={abortRef}
      />,
    );

    view.unmount();

    expect(abort).not.toHaveBeenCalled();
  });

  it("does not save the discovery prompt back while loading the initial remote value", async () => {
    render(
      <FundDiscoveryPanel
        userId={101}
        holdings={[holding()]}
        profile={profile()}
        discoveryJobId={null}
        onDiscoveryJobIdChange={vi.fn()}
        pendingDiscoveryReport={null as FundDiscoveryReport | null}
        onPendingDiscoveryReportApplied={vi.fn()}
        onRegisterDiscoveryScanRetry={vi.fn()}
        streamingDiscovery={null}
        onStreamingDiscoveryChange={vi.fn()}
        onDiscoveryStreamComplete={vi.fn()}
        discoveryStreamAbortRef={{ current: null }}
      />,
    );

    await waitFor(() => expect(fetchDiscoveryPrompt).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "高级设置" }));
    expect(screen.getByRole("button", { name: /AI 分析偏好附录（高级）/ })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(document.body.textContent).not.toContain("remote prompt");
    await new Promise((resolve) => window.setTimeout(resolve, 0));
    expect(saveDiscoveryPromptRemote).not.toHaveBeenCalled();
  });

  it("does not reuse discovery history cached by another account", async () => {
    const accountAReport = {
      ...discoveryReport(),
      id: "account-a-report",
      title: "Account A private discovery report",
    };
    vi.mocked(listDiscoveryReports)
      .mockResolvedValueOnce([accountAReport])
      .mockImplementationOnce(() => new Promise(() => undefined));

    const accountA = renderPanel({ userId: 9_101 });
    const accountATrigger = await screen.findByRole("button", {
      name: "历史推荐，共 1 份",
    });
    expect(accountATrigger).toHaveAttribute("aria-haspopup", "dialog");
    expect(accountATrigger).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(accountATrigger);
    expect(accountATrigger).toHaveAttribute("aria-expanded", "true");
    // 标题会同时出现在历史抽屉列表和自动载入的正文区，所以必须限定在抽屉里查。
    const accountADrawer = screen.getByRole("dialog", { name: "历史推荐" });
    await waitFor(() =>
      expect(
        within(accountADrawer).getByText("Account A private discovery report"),
      ).toBeInTheDocument(),
    );
    accountA.unmount();

    renderPanel({ userId: 9_202 });

    const accountBTrigger = screen.getByRole("button", { name: "历史推荐" });
    expect(accountBTrigger).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(accountBTrigger);
    expect(screen.getByRole("dialog", { name: "历史推荐" })).toBeInTheDocument();
    expect(screen.queryByText("Account A private discovery report")).not.toBeInTheDocument();
    expect(listDiscoveryReports).toHaveBeenCalledTimes(2);
  });

  it("saves the discovery prompt after the user edits it", async () => {
    render(
      <FundDiscoveryPanel
        userId={101}
        holdings={[holding()]}
        profile={profile()}
        discoveryJobId={null}
        onDiscoveryJobIdChange={vi.fn()}
        pendingDiscoveryReport={null as FundDiscoveryReport | null}
        onPendingDiscoveryReportApplied={vi.fn()}
        onRegisterDiscoveryScanRetry={vi.fn()}
        streamingDiscovery={null}
        onStreamingDiscoveryChange={vi.fn()}
        onDiscoveryStreamComplete={vi.fn()}
        discoveryStreamAbortRef={{ current: null }}
      />,
    );

    await waitFor(() => expect(fetchDiscoveryPrompt).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "高级设置" }));
    fireEvent.click(screen.getByRole("button", { name: /AI 分析偏好附录（高级）/ }));
    await screen.findByText("remote prompt", {}, { timeout: 10_000 });
    vi.mocked(saveDiscoveryPromptRemote).mockClear();
    fireEvent.click(screen.getByRole("button", { name: /编辑/ }));
    fireEvent.change(document.querySelector("[data-testid='analysis-role-prompt']") as HTMLTextAreaElement, {
      target: { value: "changed prompt" },
    });

    await waitFor(() => expect(saveDiscoveryPromptRemote).toHaveBeenCalledWith("changed prompt"));
  }, 15_000);

  it("drops the retired recommendation-goal and strategy switches from the main entry", () => {
    renderPanel();

    // 推荐目标固定为市场优选、策略固定为机会优先，两个选择器都不再出现。
    expect(screen.queryByRole("group", { name: "推荐目标" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "市场优选" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "组合补缺" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /稳健筛选/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "选基策略" })).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "基金类型偏好" })).not.toBeInTheDocument();
    expect(screen.queryByText("系统自动选基")).not.toBeInTheDocument();
  });

  it("keeps the main discovery entry deep-only", () => {
    renderPanel();

    expect(screen.queryByRole("button", { name: "快速 · Flash" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "深度 · Pro" })).not.toBeInTheDocument();
  });

  it("defaults the investment budget to 10000 and ignores holdings changes", async () => {
    const view = renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "高级设置" }));
    const input = screen.getByRole("spinbutton", { name: /本次可投入预算/ });
    expect(input).toHaveValue(10000);

    view.rerender(
      <FundDiscoveryPanel
        {...panelProps({ holdings: [{ ...holding(), holding_amount: 15000 }] })}
      />,
    );
    expect(input).toHaveValue(10000);

    fireEvent.change(input, { target: { value: "8000" } });
    view.rerender(<FundDiscoveryPanel {...panelProps({ userId: 202 })} />);
    expect(screen.getByRole("spinbutton", { name: /本次可投入预算/ })).toHaveValue(10000);
    view.rerender(<FundDiscoveryPanel {...panelProps({ userId: 101 })} />);
    expect(screen.getByRole("spinbutton", { name: /本次可投入预算/ })).toHaveValue(8000);

    view.unmount();
    renderPanel({ holdings: [{ ...holding(), holding_amount: 18000 }] });
    fireEvent.click(screen.getByRole("button", { name: "高级设置" }));
    expect(screen.getByRole("spinbutton", { name: /本次可投入预算/ })).toHaveValue(8000);

    fireEvent.click(screen.getByRole("button", { name: "扫描今日机会" }));
    await waitFor(() => expect(streamDiscovery).toHaveBeenCalled());
    expect(vi.mocked(streamDiscovery).mock.calls[0]?.[3]).toMatchObject({
      budgetYuan: 8000,
    });

    cleanup();
    renderPanel({ userId: 202 });
    fireEvent.click(screen.getByRole("button", { name: "高级设置" }));
    expect(screen.getByRole("spinbutton", { name: /本次可投入预算/ })).toHaveValue(10000);
  });

  it("submits an explicit zero budget instead of falling back to the server default", async () => {
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "高级设置" }));
    const input = screen.getByRole("spinbutton", { name: /本次可投入预算/ });
    fireEvent.change(input, { target: { value: "0" } });
    fireEvent.click(screen.getByRole("button", { name: "扫描今日机会" }));

    await waitFor(() => expect(streamDiscovery).toHaveBeenCalled());
    expect(vi.mocked(streamDiscovery).mock.calls[0]?.[3]).toMatchObject({
      budgetYuan: 0,
    });
  });

  it("sends the fixed scan goal, strategy and share-class policies for normal scans", async () => {
    renderPanel();

    fireEvent.click(screen.getByRole("button", { name: "扫描今日机会" }));

    await waitFor(() => expect(streamDiscovery).toHaveBeenCalled());
    const options = vi.mocked(streamDiscovery).mock.calls[0]?.[3];
    expect(options).toMatchObject({
      scanMode: "full_market",
      selectionStrategy: "balanced",
      fundTypePreference: "any",
      discoveryStrategy: "opportunity_first",
      budgetYuan: 10000,
    });
  });

  it("states the fixed opportunity-first strategy without offering a switch", () => {
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "高级设置" }));

    expect(screen.getByRole("group", { name: "荐基决策策略" })).toBeInTheDocument();
    expect(screen.getByTestId("discovery-strategy-opportunity_first")).toHaveTextContent(
      "机会优先",
    );
    expect(screen.queryByRole("button", { name: /机会优先/ })).not.toBeInTheDocument();
  });

  it("labels reports from a retired scan goal as a generic historical mode", async () => {
    renderPanel({
      pendingDiscoveryReport: {
        ...discoveryReport(),
        discovery_facts: {
          effective_configuration: {
            scan_goal: "retired_mode",
          },
        },
      },
    });

    expect(await screen.findByTestId("discovery-config-summary")).toHaveTextContent("历史模式");
  });

  it("collapses completed reports to a run summary and keeps the old report after a stream failure", async () => {
    vi.mocked(streamDiscovery)
      .mockRejectedValueOnce(new Error("流式连接波动"))
      .mockRejectedValueOnce(new Error("流式连接波动"));
    renderPanel({
      pendingDiscoveryReport: {
        ...discoveryReport(),
        analysis_mode: "deep",
        focus_sectors: ["医药"],
        discovery_facts: {
          effective_configuration: {
            scan_goal: "portfolio_gap",
            selection_policy: "auto_quality",
            share_class_policy: "family_dedupe_then_cost_check",
          },
        },
      },
    });

    expect(await screen.findByTestId("discovery-config-summary")).toHaveTextContent(
      "组合补缺 · 历史稳健策略 · 关注 医药 · 预算 1万",
    );
    expect(screen.queryByRole("group", { name: "荐基决策策略" })).not.toBeInTheDocument();
    expect(screen.getByTestId("discovery-report-stub")).toHaveTextContent("上一份机会报告");
    expect(screen.getByRole("button", { name: "高级设置" })).toHaveClass("min-h-11");

    fireEvent.click(screen.getByRole("button", { name: "重新扫描" }));
    await waitFor(() => expect(streamDiscovery).toHaveBeenCalledTimes(2));
    const failureMessage = await screen.findByText(/没有转入后台任务，请再点一次重新扫描/);
    expect(failureMessage).toHaveTextContent("流式连接波动");
    expect(failureMessage.closest('[role="alert"]')).toHaveClass("inline-notice-error");
    expect(screen.getByTestId("discovery-report-stub")).toHaveTextContent("上一份机会报告");

    fireEvent.click(screen.getByRole("button", { name: "高级设置" }));
    expect(screen.getByRole("group", { name: "荐基决策策略" })).toBeInTheDocument();
  });

  it("retries a transient stream failure once and does not start a background job", async () => {
    vi.mocked(streamDiscovery)
      .mockRejectedValueOnce(new Error("Failed to fetch"))
      .mockResolvedValueOnce(undefined);
    renderPanel();

    fireEvent.click(screen.getByRole("button", { name: "扫描今日机会" }));
    await waitFor(() => expect(streamDiscovery).toHaveBeenCalledTimes(2));
    expect(screen.queryByText(/后台扫描/)).not.toBeInTheDocument();
  });

  it("loads a report that was saved after the stream dropped", async () => {
    vi.mocked(listDiscoveryReports).mockImplementation(async () => {
      if (vi.mocked(streamDiscovery).mock.calls.length === 0) {
        return [{ ...discoveryReport(), id: "old", created_at: "2026-08-01T00:00:00Z" }];
      }
      return [
        { ...discoveryReport(), id: "fresh", created_at: "2026-08-19T00:00:00Z" },
        { ...discoveryReport(), id: "old", created_at: "2026-08-01T00:00:00Z" },
      ];
    });
    vi.mocked(fetchDiscoveryReportDetail).mockImplementation(async (reportId: string) => ({
      ...discoveryReport(),
      id: reportId,
      title: `detail:${reportId}`,
    }));
    vi.mocked(streamDiscovery).mockRejectedValue(
      new Error("流式扫描异常结束，未收到完成状态。"),
    );
    const onDiscoveryStreamComplete = vi.fn();
    renderPanel({ userId: 701, onDiscoveryStreamComplete });

    await waitFor(() =>
      expect(screen.getByTestId("discovery-report-stub")).toHaveTextContent("detail:old"),
    );
    fireEvent.click(screen.getByRole("button", { name: "重新扫描" }));

    await waitFor(() =>
      expect(onDiscoveryStreamComplete).toHaveBeenCalledWith(
        expect.objectContaining({ id: "fresh", title: "detail:fresh" }),
      ),
    );
    expect(streamDiscovery).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/没有转入后台任务/)).not.toBeInTheDocument();
  });

  it("starts a discovery stream even when a daily report stream is already active", async () => {
    vi.mocked(streamDiscovery).mockResolvedValue(undefined);
    renderPanel();

    fireEvent.click(screen.getByRole("button", { name: "扫描今日机会" }));
    await waitFor(() => expect(streamDiscovery).toHaveBeenCalledTimes(1));
    expect(screen.queryByText(/日报正在流式生成/)).not.toBeInTheDocument();
    expect(screen.queryByText(/同时开两条长连接/)).not.toBeInTheDocument();
  });

  it("keeps the previous report visible while a new stream is running", async () => {
    renderPanel({
      pendingDiscoveryReport: discoveryReport(),
      streamingDiscovery: streamingDiscovery(),
    });

    expect(await screen.findByTestId("discovery-report-stub")).toHaveTextContent("上一份机会报告");
    expect(screen.getByTestId("discovery-streaming")).toBeInTheDocument();
    expect(screen.getByText("新扫描正在进行，下方继续显示上次报告，完成后会自动替换。")).toBeInTheDocument();
  });

  it("announces an intentional cancellation as information instead of an error", () => {
    renderPanel({ streamingDiscovery: streamingDiscovery() });

    const cancel = screen.getByTestId("discovery-stream-cancel-btn");
    expect(cancel).toHaveClass("min-h-11");
    fireEvent.click(cancel);

    const message = screen.getByText("已停止扫描，当前条件与页面中的已有结果均已保留。");
    expect(message.closest('[role="status"]')).toHaveClass("inline-notice-info");
    expect(message.closest('[role="alert"]')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// 打开「发现」页要像日报页一样默认展示上一份报告。历史实现让 report 停在 null，
// 用户每次都得先打开「历史推荐」抽屉手动点一次才看得到上次结果。
// ---------------------------------------------------------------------------
describe("FundDiscoveryPanel latest-report autoload", () => {
  it("loads the newest report body on open", async () => {
    vi.mocked(listDiscoveryReports).mockResolvedValue([
      { ...discoveryReport(), id: "older", created_at: "2026-08-01T00:00:00Z" },
      { ...discoveryReport(), id: "newest", created_at: "2026-08-08T00:00:00Z" },
    ]);

    renderPanel({ userId: 501 });

    // 摘要占位先切进来，随后按 id 拉回的正文替换掉它。
    await waitFor(() =>
      expect(screen.getByTestId("discovery-report-stub")).toHaveTextContent("detail:newest"),
    );
    expect(fetchDiscoveryReportDetail).toHaveBeenCalledWith("newest");
  });

  it("does not steal focus or scroll when autoloading", async () => {
    // 自动载入必须是安静的：抢焦点 / 自动滚动都会让刚进页面的用户莫名其妙。
    vi.mocked(listDiscoveryReports).mockResolvedValue([
      { ...discoveryReport(), id: "newest", created_at: "2026-08-08T00:00:00Z" },
    ]);
    const activeBefore = document.activeElement;

    renderPanel({ userId: 502 });
    await waitFor(() =>
      expect(screen.getByTestId("discovery-report-stub")).toHaveTextContent("detail:newest"),
    );
    await new Promise((resolve) => window.setTimeout(resolve, 10));

    expect(document.activeElement).toBe(activeBefore);
  });

  it("leaves an in-flight scan alone", async () => {
    vi.mocked(listDiscoveryReports).mockResolvedValue([
      { ...discoveryReport(), id: "newest", created_at: "2026-08-08T00:00:00Z" },
    ]);

    renderPanel({ userId: 503, streamingDiscovery: streamingDiscovery() });
    await waitFor(() => expect(listDiscoveryReports).toHaveBeenCalled());
    await new Promise((resolve) => window.setTimeout(resolve, 10));

    expect(fetchDiscoveryReportDetail).not.toHaveBeenCalled();
  });

  it("defers to a just-completed report waiting to be applied", async () => {
    vi.mocked(listDiscoveryReports).mockResolvedValue([
      { ...discoveryReport(), id: "newest", created_at: "2026-08-08T00:00:00Z" },
    ]);

    renderPanel({
      userId: 504,
      pendingDiscoveryReport: { ...discoveryReport(), id: "fresh-scan", title: "刚扫完的报告" },
    });
    await waitFor(() => expect(listDiscoveryReports).toHaveBeenCalled());
    await new Promise((resolve) => window.setTimeout(resolve, 10));

    expect(fetchDiscoveryReportDetail).not.toHaveBeenCalled();
  });

  it("reuses a fresh cached latest report when the page is reopened", async () => {
    vi.mocked(listDiscoveryReports).mockResolvedValue([
      { ...discoveryReport(), id: "newest", created_at: "2026-08-08T00:00:00Z" },
    ]);

    const first = renderPanel({ userId: 507 });
    await waitFor(() =>
      expect(screen.getByTestId("discovery-report-stub")).toHaveTextContent("detail:newest"),
    );
    expect(fetchDiscoveryReportDetail).toHaveBeenCalledTimes(1);
    first.unmount();
    vi.mocked(fetchDiscoveryReportDetail).mockClear();

    renderPanel({ userId: 507 });
    expect(screen.getByTestId("discovery-report-stub")).toHaveTextContent("detail:newest");
    await new Promise((resolve) => window.setTimeout(resolve, 20));
    expect(fetchDiscoveryReportDetail).not.toHaveBeenCalled();
  });

  it("offers a stop control while a scan is running so the user is never trapped", () => {
    // 手机切走再回来时流可能已被系统挂起，主按钮是禁用态；没有这个出口页面就死住。
    renderPanel({ userId: 505, streamingDiscovery: streamingDiscovery() });

    expect(screen.getByTestId("discovery-scan-button")).toBeDisabled();
    expect(screen.getByTestId("discovery-stop-button")).toBeEnabled();
  });

  it("clears the background job id when the user stops a scan", () => {
    // `isRunning` 把 discoveryJobId 也算在内，只清流式状态按钮仍然是禁用的。
    const onDiscoveryJobIdChange = vi.fn();
    renderPanel({
      userId: 506,
      discoveryJobId: "job-9",
      streamingDiscovery: streamingDiscovery(),
      onDiscoveryJobIdChange,
    });

    fireEvent.click(screen.getByTestId("discovery-stop-button"));

    expect(onDiscoveryJobIdChange).toHaveBeenCalledWith(null);
  });
});
