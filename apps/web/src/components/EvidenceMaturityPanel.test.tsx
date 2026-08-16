// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EvidenceMaturityStatus } from "@/lib/api";

/**
 * 面板必须把「该等」与「该做事」分开。此前两类缺口显示成同一个 `collecting`，于是
 * `decision_score_shadow` 恒为 0 却看着像在积累——用户无法判断一条证据线是在推进还是
 * 在装死。这里锁住：等不到数据的缺口给出"等待无用"的行动指引，会自愈的给出"继续采集"。
 */
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, fetchEvidenceMaturityStatus: vi.fn() };
});

const { fetchEvidenceMaturityStatus } = await import("@/lib/api");
const { EvidenceMaturityPanel } = await import("@/components/EvidenceMaturityPanel");

// 只提供组件真正读取的字段；其余为该端点的可选项。
function status(
  overrides: Partial<EvidenceMaturityStatus> = {},
): EvidenceMaturityStatus {
  return {
    schema_version: "evidence_maturity.v1",
    generated_at: "2026-08-12T08:00:00+00:00",
    overall_status: "attention",
    mode: "evidence_collection_and_shadow_validation",
    automatic_promotion_allowed: false,
    worker: { status: "healthy", heartbeat_age_seconds: 4, jobs: [] },
    universe: { status: "collecting" },
    factor_ic: { status: "active" },
    nav_observation: { status: "collecting" },
    decision_score_shadow: {
      status: "blocked",
      blocker: "blocked_on_removed_input",
      blocker_label: "上游输入已移除（需改代码）",
      self_healing: false,
      candidate_count: 134,
      hard_gate_blocked_count: 134,
      hard_gate_blocked_percent: 100,
      automatic_promotion_allowed: false,
    },
    milestones: [],
    blockers: [
      {
        code: "pit_universe_membership",
        label: "PIT 成员快照锚点",
        blocker: "blocked_on_time",
        blocker_label: "等样本累积（会自愈）",
        self_healing: true,
      },
      {
        code: "decision_score_component.downside_control",
        label: "DecisionScore 维度 downside_control",
        blocker: "blocked_on_data_source",
        blocker_label: "等数据源（不会自愈）",
        self_healing: false,
        reason_counts: { peer_catalogue_metric_not_covered: 162 },
      },
      {
        code: "decision_score_hard_gate",
        label: "DecisionScore 硬门（先于所有维度）",
        blocker: "blocked_on_removed_input",
        blocker_label: "上游输入已移除（需改代码）",
        self_healing: false,
        reason_counts: { tradeability_gate_not_eligible: 134 },
        detail: "134/134 个候选在评分前被拦下；硬门不过时组件缺口不是真正原因。",
      },
    ],
    alerts: [],
    notices: [],
    ...overrides,
  } as unknown as EvidenceMaturityStatus;
}

afterEach(() => {
  cleanup();
  vi.mocked(fetchEvidenceMaturityStatus).mockReset();
});

describe("EvidenceMaturityPanel 阻塞清单", () => {
  it("把等不到数据与等时间的缺口给出不同的行动指引", async () => {
    vi.mocked(fetchEvidenceMaturityStatus).mockResolvedValue(status());

    render(<EvidenceMaturityPanel enabled />);

    expect(await screen.findByTestId("evidence-blockers")).toBeInTheDocument();
    expect(screen.getByText("PIT 成员快照锚点")).toBeInTheDocument();
    expect(
      screen.getByText(/继续采集即可推进/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/等待无用，需补数据源或改口径/),
    ).toBeInTheDocument();
    // 原因码带计数，便于判断是个别现象还是全量。
    expect(
      screen.getByText(/peer_catalogue_metric_not_covered ×162/),
    ).toBeInTheDocument();
  });

  it("blocked 状态不再显示成积累中", async () => {
    vi.mocked(fetchEvidenceMaturityStatus).mockResolvedValue(status());

    render(<EvidenceMaturityPanel enabled />);

    expect(await screen.findByTestId("evidence-maturity-content")).toBeInTheDocument();
    expect(screen.getAllByText("等不到数据").length).toBeGreaterThan(0);
  });

  it("契约失效与等数据源给出不同的行动指引", async () => {
    vi.mocked(fetchEvidenceMaturityStatus).mockResolvedValue(status());

    render(<EvidenceMaturityPanel enabled />);

    expect(await screen.findByTestId("evidence-blockers")).toBeInTheDocument();
    expect(
      screen.getByText(/上游已不再产出该输入，需恢复上游或让消费方退休/),
    ).toBeInTheDocument();
    expect(screen.getByText(/等待无用，需补数据源或改口径/)).toBeInTheDocument();
  });

  it("把硬门拦住比例摆到卡片上，避免组件缺口盖住真正原因", async () => {
    vi.mocked(fetchEvidenceMaturityStatus).mockResolvedValue(status());

    render(<EvidenceMaturityPanel enabled />);

    expect(await screen.findByText("硬门拦住")).toBeInTheDocument();
    expect(screen.getByText("被拦候选")).toBeInTheDocument();
    expect(screen.getByText("134 个")).toBeInTheDocument();
  });

  it("没有缺口时不渲染清单，避免空区块", async () => {
    vi.mocked(fetchEvidenceMaturityStatus).mockResolvedValue(status({ blockers: [] }));

    render(<EvidenceMaturityPanel enabled />);

    expect(await screen.findByTestId("evidence-maturity-content")).toBeInTheDocument();
    expect(screen.queryByTestId("evidence-blockers")).not.toBeInTheDocument();
  });
});
