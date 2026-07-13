// @vitest-environment jsdom

import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import type { RebalanceSimulation } from "@/lib/api";
import {
  installMatchMedia,
  type MatchMediaController,
} from "@/test/matchMedia";

const apiMocks = vi.hoisted(() => ({
  fetchRebalanceSimulation: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, ...apiMocks };
});

import { RebalanceSimulationPanel } from "@/components/RebalanceSimulationPanel";

const DESKTOP_QUERY = "(min-width: 640px)";
let matchMedia: MatchMediaController;

const simulation: RebalanceSimulation = {
  assumption: "仅模拟报告中的示意金额，不会执行真实交易。",
  current_total: 10_000,
  simulated_total: 10_000,
  concentration_limit_percent: 40,
  warnings: [],
  rows: [
    {
      fund_code: "110022",
      fund_name: "易方达消费行业股票",
      action: "减仓",
      current_amount: 6_000,
      delta_yuan: -1_000,
      simulated_amount: 5_000,
      current_weight_percent: 60,
      simulated_weight_percent: 50,
      weight_delta_percent: -10,
      amount_note: "金额仅用于帮助理解报告建议。",
    },
  ],
};

beforeEach(() => {
  matchMedia = installMatchMedia({ [DESKTOP_QUERY]: false });
  apiMocks.fetchRebalanceSimulation.mockResolvedValue(simulation);
});

afterEach(() => {
  cleanup();
  matchMedia.restore();
  apiMocks.fetchRebalanceSimulation.mockReset();
});

describe("RebalanceSimulationPanel responsive rows", () => {
  it("mounts only mobile rows below the breakpoint", async () => {
    render(<RebalanceSimulationPanel reportId="report-mobile" embedded />);

    expect(await screen.findByRole("list", { name: "模拟调仓明细" })).toBeInTheDocument();
    expect(screen.getByRole("listitem")).toHaveTextContent("110022");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "模拟调仓明细，可横向滚动" })).not.toBeInTheDocument();
    expect(apiMocks.fetchRebalanceSimulation).toHaveBeenCalledOnce();
  });

  it("mounts only desktop rows at and above the breakpoint", async () => {
    matchMedia.setMatches(DESKTOP_QUERY, true);

    render(<RebalanceSimulationPanel reportId="report-desktop" embedded />);

    expect(
      await screen.findByRole("table", { name: "各基金当前仓位与模拟调仓后的金额、仓位变化" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: simulation.rows[0].fund_name })).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "模拟调仓明细" })).not.toBeInTheDocument();
    expect(apiMocks.fetchRebalanceSimulation).toHaveBeenCalledOnce();
  });

  it("switches the mounted rows without requesting the simulation again", async () => {
    render(<RebalanceSimulationPanel reportId="report-responsive" embedded />);
    expect(await screen.findByRole("list", { name: "模拟调仓明细" })).toBeInTheDocument();

    act(() => matchMedia.setMatches(DESKTOP_QUERY, true));

    expect(
      screen.getByRole("table", { name: "各基金当前仓位与模拟调仓后的金额、仓位变化" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "模拟调仓明细" })).not.toBeInTheDocument();
    await waitFor(() => expect(apiMocks.fetchRebalanceSimulation).toHaveBeenCalledOnce());
  });
});
