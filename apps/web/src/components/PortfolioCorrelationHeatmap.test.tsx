// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import type { PortfolioRiskCorrelation } from "@/lib/api";

const mockFetchCorrelation = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, fetchPortfolioRiskCorrelation: mockFetchCorrelation };
});

import { PortfolioCorrelationHeatmap } from "@/components/PortfolioCorrelationHeatmap";

afterEach(() => {
  cleanup();
  mockFetchCorrelation.mockReset();
});

const correlation: PortfolioRiskCorrelation = {
  available: true,
  sample_days: 60,
  codes: ["110022", "000001"],
  names: ["易方达消费行业", "华夏成长"],
  matrix: [
    [1, 0.72],
    [0.72, 1],
  ],
  max_pair: {
    code_a: "110022",
    code_b: "000001",
    name_a: "易方达消费行业",
    name_b: "华夏成长",
    corr: 0.72,
  },
};

describe("PortfolioCorrelationHeatmap", () => {
  it("renders a captioned matrix with row/column-linked cell labels", async () => {
    mockFetchCorrelation.mockResolvedValue(correlation);
    render(<PortfolioCorrelationHeatmap enabled />);

    expect(
      await screen.findByRole("table", { name: /近60个对齐交易日/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("cell", { name: "易方达消费行业与华夏成长相关系数0.72" }),
    ).toHaveTextContent("0.72");
    expect(screen.getByRole("region", { name: /可横向滚动/ })).toHaveAttribute("tabindex", "0");
  });

  it("stops after a failure and retries only when requested", async () => {
    mockFetchCorrelation
      .mockRejectedValueOnce(new Error("净值服务超时"))
      .mockResolvedValueOnce(correlation);
    render(<PortfolioCorrelationHeatmap enabled />);

    expect(await screen.findByRole("alert")).toHaveTextContent("净值服务超时");
    expect(mockFetchCorrelation).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => {
      expect(screen.getByRole("table", { name: /近60个对齐交易日/ })).toBeInTheDocument();
    });
    expect(mockFetchCorrelation).toHaveBeenCalledTimes(2);
  });
});
