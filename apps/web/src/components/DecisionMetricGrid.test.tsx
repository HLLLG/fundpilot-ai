// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import "@testing-library/jest-dom/vitest";
import {
  DecisionMetricGrid,
  FeeBenchmarkMethodNote,
  LegacyReferenceStrip,
} from "@/components/DecisionMetricGrid";
import type { OutcomeMetricSummary } from "@/lib/api";

const metrics: OutcomeMetricSummary = {
  gross_direction: {
    eligible_count: 4,
    mature_count: 4,
    unavailable_count: 0,
    hit_count: 3,
    miss_count: 1,
    coverage_percent: 100,
    hit_rate_percent: 75,
  },
  positive_net_return: {
    eligible_count: 3,
    mature_count: 2,
    unavailable_count: 1,
    hit_count: 1,
    miss_count: 1,
    coverage_percent: 66.7,
    hit_rate_percent: 50,
  },
  gross_excess: {
    eligible_count: 4,
    mature_count: 1,
    unavailable_count: 3,
    hit_count: 1,
    miss_count: 0,
    coverage_percent: 25,
    hit_rate_percent: 100,
  },
  net_excess: {
    eligible_count: 3,
    mature_count: 1,
    unavailable_count: 2,
    hit_count: 0,
    miss_count: 1,
    coverage_percent: 33.3,
    hit_rate_percent: 0,
  },
};

afterEach(() => cleanup());

describe("DecisionMetricGrid", () => {
  it("renders four explicitly separated metric contracts and their coverage", () => {
    render(<DecisionMetricGrid metrics={metrics} />);

    expect(screen.getByText("毛收益方向")).toBeInTheDocument();
    expect(screen.getByText("假设费后正收益")).toBeInTheDocument();
    expect(screen.getByText("合同基准超额")).toBeInTheDocument();
    expect(screen.getByText("费后合同超额")).toBeInTheDocument();
    expect(screen.getByLabelText(/毛收益方向：命中率 75%，覆盖率 100%/)).toBeInTheDocument();
    expect(screen.getByLabelText(/合同基准超额：命中率 100%，覆盖率 25%/)).toBeInTheDocument();
    expect(screen.getAllByText("用户假设").length).toBeGreaterThan(0);
    expect(screen.getAllByText("正式基准").length).toBeGreaterThan(0);
  });

  it("states that 1.5 percent is an assumption and exact contract benchmark is required", () => {
    render(<FeeBenchmarkMethodNote feePercent={1.5} />);

    expect(screen.getByText(/1.5% 是你设置的买卖合计费用假设/)).toBeInTheDocument();
    expect(screen.getByText(/不是平台实际扣费/)).toBeInTheDocument();
    expect(screen.getByText(/基金合同基准/)).toBeInTheDocument();
    expect(screen.getByText(/跟踪指数和类别代理只作参考/)).toBeInTheDocument();
  });

  it("keeps legacy evidence visible while marking it excluded from formal V2", () => {
    render(
      <LegacyReferenceStrip
        horizon="T+1"
        legacy={{
          excluded_from_formal_v2: true,
          report_count: 6,
          recommendation_count: 9,
          eligible_count: 7,
          mature_count: 5,
          hit_rate_percent: 60,
        }}
      />,
    );

    expect(screen.getByText("旧口径历史参考")).toBeInTheDocument();
    expect(screen.getByText("已排除正式 V2 统计")).toBeInTheDocument();
    expect(screen.getByText(/6 份旧报告/)).toBeInTheDocument();
    expect(screen.getByText(/成熟 5\/7 · 方向命中 60%/)).toBeInTheDocument();
  });
});
