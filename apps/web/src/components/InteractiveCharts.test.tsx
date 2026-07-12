// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import "@testing-library/jest-dom/vitest";

import { IntradayPercentChart } from "@/components/IntradayPercentChart";
import { PerformanceReturnChart } from "@/components/PerformanceReturnChart";

afterEach(cleanup);

describe("interactive chart access", () => {
  it("describes intraday data and supports arrow-key inspection", () => {
    render(
      <IntradayPercentChart
        points={[
          { time: "09:30", percent: 0.1 },
          { time: "10:30", percent: -0.2 },
          { time: "15:00", percent: 0.35 },
        ]}
      />,
    );

    const chart = screen.getByRole("img", { name: /分时涨跌幅走势图/ });
    expect(chart).toHaveAttribute("tabindex", "0");
    fireEvent.keyDown(chart, { key: "ArrowRight" });
    expect(screen.getByText("09:30，涨跌幅+0.10%")).toBeInTheDocument();
    fireEvent.keyDown(chart, { key: "End" });
    expect(screen.getByText("15:00，涨跌幅+0.35%")).toBeInTheDocument();
  });

  it("describes cumulative returns and exposes 44px trade-date controls", () => {
    render(
      <PerformanceReturnChart
        points={[
          { date: "2026-07-01", nav: 1, dailyReturn: null, fundPercent: 0, benchPercent: 0 },
          { date: "2026-07-02", nav: 1.01, dailyReturn: 1, fundPercent: 1, benchPercent: 0.4 },
          { date: "2026-07-03", nav: 1.02, dailyReturn: 0.99, fundPercent: 2, benchPercent: 0.8 },
        ]}
        markers={[
          {
            date: "2026-07-02",
            kind: "buy",
            items: [
              {
                direction: "buy",
                amount_yuan: 500,
                trade_time: "2026-07-02 14:30:00",
                status: "confirmed",
              },
            ],
          },
        ]}
      />,
    );

    const chart = screen.getByRole("img", { name: /基金累计收益走势图/ });
    fireEvent.keyDown(chart, { key: "ArrowRight" });
    expect(screen.getByText(/2026-07-01，基金收益0.00%/)).toBeInTheDocument();

    const marker = screen.getByRole("button", { name: "07-02 · 加仓" });
    expect(marker).toHaveClass("touch-target");
    fireEvent.click(marker);
    expect(marker).toHaveAttribute("aria-expanded", "true");
    expect(screen.getAllByText("2026-07-02").length).toBeGreaterThanOrEqual(2);
  });
});
