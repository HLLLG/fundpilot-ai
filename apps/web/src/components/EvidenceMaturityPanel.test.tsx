// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EvidenceMaturityPanel } from "@/components/EvidenceMaturityPanel";
import { fetchEvidenceMaturityStatus, type EvidenceMaturityStatus } from "@/lib/api";


vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, fetchEvidenceMaturityStatus: vi.fn() };
});


function payload(): EvidenceMaturityStatus {
  return {
    schema_version: "evidence_maturity.v1",
    generated_at: "2026-07-18T08:00:00+00:00",
    overall_status: "collecting",
    mode: "evidence_collection_and_shadow_validation",
    automatic_promotion_allowed: false,
    worker: {
      status: "healthy",
      healthy: true,
      reason: "ok",
      heartbeat_age_seconds: 4,
      jobs: [
        { name: "market-shared-refresh", persistent: true, alive: true },
      ],
    },
    universe: {
      status: "collecting",
      snapshot_count: 4,
      latest_sampled_fund_count: 1500,
      effective_anchor_count: 4,
      minimum_effective_anchor_count: 24,
      anchor_progress_percent: 16.67,
      publishable: false,
    },
    factor_ic: {
      status: "active",
      available: true,
      stale: false,
      confidence_eligible: true,
      point_in_time_scope: "membership_only",
      nav_revision_pit: false,
      mature_period_count_20d: 0,
      mature_period_count_60d: 0,
      economic_minimum_period_count: 36,
      economic_progress_percent_20d: 0,
      economic_progress_percent_60d: 0,
      confidence_block_reasons: [],
    },
    decision_score_shadow: {
      status: "collecting",
      artifact_count: 0,
      valid_artifact_count: 0,
      scored_coverage_percent: null,
      automatic_promotion_allowed: false,
    },
    decision_quality: {
      status: "collecting",
      snapshot_available: true,
      readiness_status: "insufficient_data",
      mature_decision_day_count: 0,
      minimum_shadow_mature_decision_days: 20,
      minimum_manual_review_mature_decision_days: 60,
      formal_label_coverage_percent: null,
      minimum_manual_review_label_coverage_percent: 80,
      maturity_progress_percent: 0,
      automatic_promotion_allowed: false,
    },
    milestones: [],
    alerts: [
      {
        code: "nav_observation_pit_collecting",
        severity: "info",
        title: "NAV 时点证据尚未完整",
        message: "当前最多证明基金池成员 PIT。",
        action: "继续追加式采集 NAV observation。",
      },
    ],
    notices: ["空值表示尚无可验证证据，不按 0 分处理。"],
  };
}


afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});


describe("EvidenceMaturityPanel", () => {
  it("loads only when enabled and renders honest missing evidence", async () => {
    vi.mocked(fetchEvidenceMaturityStatus).mockResolvedValue(payload());
    const view = render(<EvidenceMaturityPanel enabled={false} />);
    expect(fetchEvidenceMaturityStatus).not.toHaveBeenCalled();

    view.rerender(<EvidenceMaturityPanel enabled />);

    expect(await screen.findByText("证据持续积累中")).toBeInTheDocument();
    expect(screen.getByText("4 / 24")).toBeInTheDocument();
    expect(screen.getByText("0 / 36")).toBeInTheDocument();
    expect(screen.getAllByText("尚无证据").length).toBeGreaterThan(0);
    expect(screen.getByText("NAV 时点证据尚未完整")).toBeInTheDocument();
    expect(screen.getByText(/automatic promotion 始终关闭/)).toBeInTheDocument();
  });

  it("offers a retry after a read failure", async () => {
    vi.mocked(fetchEvidenceMaturityStatus)
      .mockRejectedValueOnce(new Error("暂时不可用"))
      .mockResolvedValueOnce(payload());

    render(<EvidenceMaturityPanel enabled />);

    expect(await screen.findByText(/证据成熟度加载失败：暂时不可用/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => expect(fetchEvidenceMaturityStatus).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("证据持续积累中")).toBeInTheDocument();
  });
});
