// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it } from "vitest";

import { QuantEvidenceSummary } from "@/components/QuantEvidenceSummary";
import type { HoldingEvidence } from "@/lib/api";

/**
 * 因子可靠性是**同类基金共用**的统计属性（`reliability.scope="peer_group"`），
 * 同一同类组内每只基金逐字相同。所以「正向支持 不足」说的是"这类基金的因子统计不可用"，
 * 不是"这只基金量化表现差"。不把这句区分写出来，用户会把一个全体恒等的常量读成对自己
 * 持仓的评价——线上六只持仓当前全是这个形态。
 */
function evidence(overrides: Partial<HoldingEvidence> = {}): HoldingEvidence {
  return {
    schema_version: "quant_evidence.v2",
    composite: {
      level: "不足",
      score: 0,
      reliability: { level: "低", score: 1, scope: "peer_group", usable: false },
      direction: "neutral",
      coverage: { level: "中", percent: 75, basis: "基金特征字段完整度" },
      freshness: { status: "fresh" },
      positive_component_count: 0,
      risk_guard_count: 1,
    },
    components: [
      {
        source: "factor",
        role: "return_signal",
        level: "不足",
        basis: "主因子 动量(百分位90)·IC指数基金未来20日 IC +0.043，样本外/区间稳定性不足",
        direction: "unknown",
        reliability: {
          level: "低",
          score: 1,
          scope: "peer_group",
          usable: false,
          basis: "指数基金未来20日 IC +0.043，样本外/区间稳定性不足",
        },
      },
    ],
    summary: "主因子 动量(百分位90)·IC样本外稳定性不足",
    ...overrides,
  };
}

describe("QuantEvidenceSummary", () => {
  afterEach(cleanup);

  it("explains that an unusable peer-group reliability is not a per-fund verdict", () => {
    render(<QuantEvidenceSummary evidence={evidence()} />);

    const note = screen.getByTestId("quant-evidence-scope-note");
    expect(note).toHaveTextContent("同类基金共用的统计属性");
    expect(note).toHaveTextContent("不代表这只基金自身表现差");
  });

  it("labels coverage as feature completeness rather than statistical coverage", () => {
    render(<QuantEvidenceSummary evidence={evidence()} />);

    expect(screen.getByText("特征齐全度")).toBeInTheDocument();
    expect(screen.queryByText("覆盖")).not.toBeInTheDocument();
  });

  it("drops the scope note once a return route passes the reliability gate", () => {
    render(
      <QuantEvidenceSummary
        evidence={evidence({
          composite: {
            level: "高",
            score: 3,
            reliability: { level: "中", score: 2, scope: "peer_group", usable: true },
            direction: "positive",
            freshness: { status: "fresh" },
            positive_component_count: 1,
          },
          components: [
            {
              source: "factor",
              role: "return_signal",
              level: "高",
              basis: "主因子 动量(百分位95)·IC同类正向且样本外稳定",
              direction: "positive",
              reliability: { level: "中", score: 2, scope: "peer_group", usable: true },
            },
          ],
        })}
      />,
    );

    expect(screen.queryByTestId("quant-evidence-scope-note")).not.toBeInTheDocument();
  });
});
