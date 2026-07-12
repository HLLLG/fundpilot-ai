// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import "@testing-library/jest-dom/vitest";

import { ProfitAnalysisTrendChart } from "@/components/ProfitAnalysisTrendChart";

afterEach(cleanup);

it("exposes a keyboard cursor and announces the active profit point", () => {
  render(
    <ProfitAnalysisTrendChart
      trend={{
        kind: "daily",
        points: [
          { date: "2026-07-01", portfolio_percent: 0.5, index_percent: 0.2 },
          { date: "2026-07-02", portfolio_percent: 1, index_percent: 0.4 },
          { date: "2026-07-03", portfolio_percent: 0.8, index_percent: null },
        ],
      }}
    />,
  );

  const chart = screen.getByRole("img", { name: /聚焦后可用左右方向键逐点查看/ });
  expect(chart).toHaveAttribute("tabindex", "0");

  fireEvent.keyDown(chart, { key: "ArrowRight" });
  expect(screen.getByText(/2026-07-01，组合\+0\.50%，上证\+0\.20%/)).toBeInTheDocument();

  fireEvent.keyDown(chart, { key: "End" });
  expect(screen.getByText(/2026-07-03，组合\+0\.80%，上证—/)).toBeInTheDocument();

  fireEvent.blur(chart);
  expect(screen.queryByText(/2026-07-03，组合\+0\.80%，上证—/)).not.toBeInTheDocument();
});
