import { describe, expect, it } from "vitest";

import type { Holding, HoldingFieldWarning, InvestorProfile } from "@/lib/api";
import { buildWorkflowBlockers, hasBlockingErrors } from "@/lib/workflowBlockers";

const holding: Holding = {
  fund_code: "110022",
  fund_name: "示例基金",
  holding_amount: 1_000,
  return_percent: 1,
  sector_name: "人工智能",
};

const profile: InvestorProfile = {
  style: "稳健",
  horizon: "半年到一年",
  max_drawdown_percent: 8,
  concentration_limit_percent: 100,
  prefer_dca: true,
  avoid_chasing: true,
};

function warning(severity: HoldingFieldWarning["severity"]): HoldingFieldWarning {
  return {
    index: 0,
    field: "daily_profit",
    code: `${severity}-warning`,
    message: `${severity} 详情`,
    severity,
  };
}

describe("workflow blockers", () => {
  it.each(["warn", "info"] as const)(
    "does not block report generation for a %s advisory",
    (severity) => {
      const blockers = buildWorkflowBlockers({
        holdings: [holding],
        warnings: [warning(severity)],
        profile,
        hasReportToday: true,
      });

      expect(hasBlockingErrors(blockers)).toBe(false);
    },
  );

  it("surfaces the concrete reason for a blocking holding error", () => {
    const blockers = buildWorkflowBlockers({
      holdings: [holding],
      warnings: [warning("error")],
      profile,
      hasReportToday: true,
    });

    expect(hasBlockingErrors(blockers)).toBe(true);
    expect(blockers.find((item) => item.severity === "error")?.message).toBe(
      "持仓数据异常：error 详情",
    );
  });
});
