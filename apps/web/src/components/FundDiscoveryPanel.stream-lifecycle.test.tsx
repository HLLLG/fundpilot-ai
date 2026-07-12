// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ComponentProps, MutableRefObject } from "react";
import "@testing-library/jest-dom/vitest";

import type {
  AnalysisMode,
  FundDiscoveryReport,
  Holding,
  InvestorProfile,
} from "@/lib/api";
import type { StreamingDiscoveryState } from "@/lib/discoveryStreamApi";
import { FundDiscoveryPanel } from "@/components/FundDiscoveryPanel";
import {
  fetchDiscoveryPrompt,
  listDiscoveryReports,
  saveDiscoveryPromptRemote,
  startDiscoveryJob,
} from "@/lib/api";
import { streamDiscovery } from "@/lib/discoveryStreamApi";

vi.mock("@/lib/api", () => ({
  fetchDiscoveryPrompt: vi.fn().mockResolvedValue({
    role_prompt: "remote prompt",
    default_role_prompt: "default prompt",
    is_custom: true,
  }),
  fetchDiscoverySectors: vi.fn().mockResolvedValue([]),
  listDiscoveryReports: vi.fn().mockResolvedValue([]),
  saveDiscoveryPromptRemote: vi.fn().mockResolvedValue({}),
  startDiscoveryJob: vi.fn().mockResolvedValue("job-1"),
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
    style: "稳健",
    horizon: "半年到一年",
    max_drawdown_percent: 15,
    concentration_limit_percent: 35,
    expected_investment_amount: 30000,
    prefer_dca: true,
    avoid_chasing: true,
    decision_style: "conservative",
    investment_preset: "conservative_hold",
    round_trip_fee_percent: 1.5,
    min_net_profit_percent: 1,
    swing_alerts_enabled: false,
    swing_monitor_scope: "both",
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

function renderPanel(overrides: Partial<ComponentProps<typeof FundDiscoveryPanel>> = {}) {
  const props: ComponentProps<typeof FundDiscoveryPanel> = {
    userId: 101,
    holdings: [holding()],
    profile: profile(),
    onProfileChange: vi.fn(),
    analysisMode: "fast",
    onAnalysisModeChange: vi.fn(),
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
  return render(<FundDiscoveryPanel {...props} />);
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
        onProfileChange={vi.fn()}
        analysisMode={"fast" as AnalysisMode}
        onAnalysisModeChange={vi.fn()}
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
        onProfileChange={vi.fn()}
        analysisMode={"fast" as AnalysisMode}
        onAnalysisModeChange={vi.fn()}
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
    expect(screen.getByRole("button", { name: /AI 角色设定（高级）/ })).toHaveAttribute(
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
    await screen.findByText("Account A private discovery report");
    accountA.unmount();

    renderPanel({ userId: 9_202 });

    expect(screen.queryByText("Account A private discovery report")).not.toBeInTheDocument();
    expect(listDiscoveryReports).toHaveBeenCalledTimes(2);
  });

  it("saves the discovery prompt after the user edits it", async () => {
    render(
      <FundDiscoveryPanel
        userId={101}
        holdings={[holding()]}
        profile={profile()}
        onProfileChange={vi.fn()}
        analysisMode={"fast" as AnalysisMode}
        onAnalysisModeChange={vi.fn()}
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
    fireEvent.click(screen.getByRole("button", { name: /AI 角色设定（高级）/ }));
    await waitFor(() => expect(document.body.textContent).toContain("remote prompt"));
    vi.mocked(saveDiscoveryPromptRemote).mockClear();
    fireEvent.click(screen.getByRole("button", { name: /编辑/ }));
    fireEvent.change(document.querySelector("[data-testid='analysis-role-prompt']") as HTMLTextAreaElement, {
      target: { value: "changed prompt" },
    });

    await waitFor(() => expect(saveDiscoveryPromptRemote).toHaveBeenCalledWith("changed prompt"));
  });

  it("exposes complete first-run configuration as labelled, pressed button groups", () => {
    renderPanel();

    expect(screen.getByRole("group", { name: "扫描模式" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "选基策略" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "基金类型偏好" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "全市场机会" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "均衡潜力" })).toHaveClass("min-h-11");
    expect(screen.getByRole("button", { name: "不限" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: "短线抄底" }));
    const advanced = screen.getByRole("button", { name: "抄底筛选（高级）" });
    expect(advanced).toHaveAttribute("aria-expanded", "false");
    expect(advanced).toHaveAttribute("aria-controls", "discovery-dip-advanced");
    fireEvent.click(advanced);
    expect(advanced).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("group", { name: "回看天数" })).toBeInTheDocument();
  });

  it("collapses completed reports to a run summary and keeps the old report during fallback", async () => {
    vi.mocked(streamDiscovery).mockRejectedValueOnce(new Error("流式连接波动"));
    renderPanel({ pendingDiscoveryReport: discoveryReport() });

    expect(await screen.findByTestId("discovery-config-summary")).toHaveTextContent(
      "全市场机会 · 均衡潜力 · 基金类型：不限 · 快速分析",
    );
    expect(screen.queryByRole("group", { name: "扫描模式" })).not.toBeInTheDocument();
    expect(screen.getByTestId("discovery-report-stub")).toHaveTextContent("上一份机会报告");
    expect(screen.getByRole("button", { name: "调整条件" })).toHaveClass("min-h-11");

    fireEvent.click(screen.getByRole("button", { name: "重新扫描" }));
    await waitFor(() => expect(startDiscoveryJob).toHaveBeenCalled());
    const fallbackMessage = await screen.findByText(
      "流式连接波动，已切换到后台扫描；完成后会自动更新结果。",
    );
    expect(fallbackMessage.closest('[role="status"]')).toHaveClass("bg-amber-50/90");
    expect(screen.getByTestId("discovery-report-stub")).toHaveTextContent("上一份机会报告");

    fireEvent.click(screen.getByRole("button", { name: "调整条件" }));
    expect(screen.getByRole("group", { name: "扫描模式" })).toBeInTheDocument();
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
    expect(message.closest('[role="status"]')).toHaveClass("bg-blue-50/90");
    expect(message.closest('[role="alert"]')).toBeNull();
  });
});
