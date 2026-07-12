// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import "@testing-library/jest-dom/vitest";

import { HoldingDonutChart } from "@/components/HoldingDonutChart";
import type { PortfolioAllocationRow } from "@/lib/api";

afterEach(cleanup);

function row(index: number, weight: number): PortfolioAllocationRow {
  return {
    fund_code: String(100000 + index),
    fund_name: `示例基金${index}`,
    holding_amount: weight * 100,
    weight_percent: weight,
  };
}

describe("HoldingDonutChart", () => {
  it("gives the chart a text equivalent and keeps a compact legend", () => {
    render(<HoldingDonutChart rows={[row(1, 60), row(2, 30), row(3, 10)]} />);

    expect(screen.getByRole("img", { name: /持仓分布图，共3只基金/ })).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "持仓占比图例" })).toBeInTheDocument();
    expect(screen.queryByText(/查看全部/)).not.toBeInTheDocument();
  });

  it("groups long tails as other and preserves the complete list on demand", () => {
    const rows = [row(1, 30), row(2, 20), row(3, 15), row(4, 12), row(5, 9), row(6, 7), row(7, 4), row(8, 3)];
    render(<HoldingDonutChart rows={rows} />);

    expect(screen.getByText("其他")).toBeInTheDocument();
    expect(screen.getByText("查看全部 8 只持仓明细")).toHaveClass("min-h-11");
    expect(screen.getAllByText("示例基金8").length).toBeGreaterThan(0);
  });
});
