import { describe, expect, it } from "vitest";

import {
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
});
