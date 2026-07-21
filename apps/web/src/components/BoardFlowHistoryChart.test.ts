import { describe, expect, it } from "vitest";

import {
  buildBoardFlowAxisDomain,
  formatBoardFlowAxisYi,
  mapBoardFlowHistoryValues,
} from "@/components/BoardFlowHistoryChart";

describe("BoardFlowHistoryChart data mapping", () => {
  it("keeps a real zero while preserving missing and non-finite flow values as gaps", () => {
    expect(
      mapBoardFlowHistoryValues([
        { date: "2026-07-01", main_force_net_yi: null },
        { date: "2026-07-02", main_force_net_yi: 0 },
        { date: "2026-07-03", main_force_net_yi: -12.5 },
        { date: "2026-07-04", main_force_net_yi: Number.NaN },
      ]),
    ).toEqual([null, 0, -12.5, null]);
  });

  it("keeps the sign on both sides of the flow axis", () => {
    expect(formatBoardFlowAxisYi(12.34)).toBe("+12.3");
    expect(formatBoardFlowAxisYi(-12.34)).toBe("-12.3");
    expect(formatBoardFlowAxisYi(0)).toBe("0");
  });

  it("uses the full compact plot for one-sided outflows instead of reserving an empty upper half", () => {
    expect(buildBoardFlowAxisDomain([-0.3, -31.8, -12.4, -72.1, -24.6])).toEqual({
      min: -100,
      max: 0,
      ticks: [-100, -50, 0],
    });
  });

  it("keeps zero as the reference line when inflows and outflows are mixed", () => {
    expect(buildBoardFlowAxisDomain([-25, 42])).toEqual({
      min: -50,
      max: 50,
      ticks: [-50, 0, 50],
    });
  });

  it("compacts very large axis values", () => {
    expect(formatBoardFlowAxisYi(1250)).toBe("+1.3k");
    expect(formatBoardFlowAxisYi(-12000)).toBe("-12k");
  });
});
