// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it } from "vitest";
import type { HoldingEvidence } from "@/lib/api";
import { QuantEvidenceSummary } from "@/components/QuantEvidenceSummary";

afterEach(cleanup);

describe("QuantEvidenceSummary", () => {
  it("separates positive support, reliability, direction and risk guards", () => {
    const evidence: HoldingEvidence = {
      schema_version: "quant_evidence.v2",
      composite: {
        level: "不足",
        score: 0,
        reliability: { level: "高", score: 3 },
        direction: "negative",
        coverage: { level: "中", percent: 72 },
        freshness: { status: "fresh" },
        risk_guard_count: 1,
      },
      components: [],
      summary: "板块信号可靠但方向向下；组合风险只作守卫。",
    };

    render(<QuantEvidenceSummary evidence={evidence} />);

    expect(screen.getByText("正向支持")).toBeInTheDocument();
    expect(screen.getByText("可靠性")).toBeInTheDocument();
    expect(screen.getByText("负向")).toBeInTheDocument();
    expect(screen.getByText("72%")).toBeInTheDocument();
    expect(screen.getByText("1 路")).toBeInTheDocument();
    expect(screen.getByText(/板块信号可靠但方向向下/)).toBeInTheDocument();
  });

  it("keeps old evidence readable without inventing v2 dimensions", () => {
    const evidence: HoldingEvidence = {
      composite: { level: "中", score: 2 },
      components: [],
      summary: "历史综合置信中。",
    };

    render(<QuantEvidenceSummary evidence={evidence} />);

    expect(screen.getByText(/量化证据（旧口径）：历史综合置信中/)).toBeInTheDocument();
    expect(screen.queryByText("正向支持")).not.toBeInTheDocument();
  });
});
