import { describe, expect, it } from "vitest";
import {
  holdingDisplaySectorLabel,
  isInvalidSectorLabel,
  resolveIntradayQuery,
} from "@/lib/profileSector";

describe("profileSector", () => {
  it("accepts canonical ascii sector labels like CPO", () => {
    expect(isInvalidSectorLabel("CPO")).toBe(false);
    expect(isInvalidSectorLabel("中航机遇领航混合发起C")).toBe(true);
  });

  it("resolves intraday from fund code seed when sector_name is missing", () => {
    const holding = {
      fund_code: "018957",
      fund_name: "中航机遇领航混合发起C",
      sector_name: null,
      intraday_index_name: null,
    };
    expect(holdingDisplaySectorLabel(holding)).toBe("CPO");
    expect(resolveIntradayQuery(holding)).toEqual({
      source_type: "concept",
      source_name: "CPO",
    });
  });
});
