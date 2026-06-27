// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { MutableRefObject } from "react";

import type {
  AnalysisMode,
  FundDiscoveryReport,
  Holding,
  InvestorProfile,
} from "@/lib/api";
import type { StreamingDiscoveryState } from "@/lib/discoveryStreamApi";
import { FundDiscoveryPanel } from "@/components/FundDiscoveryPanel";
import { fetchDiscoveryPrompt, saveDiscoveryPromptRemote } from "@/lib/api";

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

describe("FundDiscoveryPanel stream lifecycle", () => {
  it("does not abort an active discovery stream when the tab unmounts", () => {
    const abort = vi.fn();
    const abortRef = {
      current: { abort },
    } as unknown as MutableRefObject<AbortController | null>;

    const view = render(
      <FundDiscoveryPanel
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
    await waitFor(() => expect(document.body.textContent).toContain("remote prompt"));
    await new Promise((resolve) => window.setTimeout(resolve, 0));
    expect(saveDiscoveryPromptRemote).not.toHaveBeenCalled();
  });

  it("saves the discovery prompt after the user edits it", async () => {
    render(
      <FundDiscoveryPanel
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

    await waitFor(() => expect(document.body.textContent).toContain("remote prompt"));
    vi.mocked(saveDiscoveryPromptRemote).mockClear();
    fireEvent.click(screen.getByRole("button", { name: /编辑/ }));
    fireEvent.change(document.querySelector("[data-testid='analysis-role-prompt']") as HTMLTextAreaElement, {
      target: { value: "changed prompt" },
    });

    await waitFor(() => expect(saveDiscoveryPromptRemote).toHaveBeenCalledWith("changed prompt"));
  });
});
