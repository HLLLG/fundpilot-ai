// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import { ProfitAnalysisTrendChart } from "@/components/ProfitAnalysisTrendChart";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

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
  expect(chart).toHaveAttribute("height", "200");

  fireEvent.keyDown(chart, { key: "ArrowRight" });
  expect(screen.getByText(/2026-07-01，组合\+0\.50%，上证\+0\.20%/)).toBeInTheDocument();

  fireEvent.keyDown(chart, { key: "End" });
  expect(screen.getByText(/2026-07-03，组合\+0\.80%，上证—/)).toBeInTheDocument();

  fireEvent.blur(chart);
  expect(screen.queryByText(/2026-07-03，组合\+0\.80%，上证—/)).not.toBeInTheDocument();
});

it("keeps axis typography at its intended size on a wide container", () => {
  let resizeCallback: ResizeObserverCallback | null = null;
  class ResizeObserverMock {
    constructor(callback: ResizeObserverCallback) {
      resizeCallback = callback;
    }
    observe() {}
    disconnect() {}
    unobserve() {}
  }
  vi.stubGlobal("ResizeObserver", ResizeObserverMock);

  render(
    <ProfitAnalysisTrendChart
      trend={{
        kind: "intraday",
        points: [
          { time: "09:30", portfolio_percent: 0.5, index_percent: 0.2 },
          { time: "15:00", portfolio_percent: -1, index_percent: -0.4 },
        ],
      }}
    />,
  );

  act(() => {
    resizeCallback?.(
      [{ contentRect: { width: 1_200 } } as ResizeObserverEntry],
      {} as ResizeObserver,
    );
  });

  const chart = screen.getByRole("img", { name: /收益走势图/ });
  expect(chart).toHaveAttribute("viewBox", "0 0 1200 200");
  expect(screen.getByText("09:30")).toHaveStyle({ fontSize: "10px" });
});
