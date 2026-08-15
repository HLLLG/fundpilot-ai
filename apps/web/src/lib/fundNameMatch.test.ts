import { describe, expect, it } from "vitest";
import {
  normalizeFundNameForLookup,
  pickBestFundMatch,
  pickUniqueFundMatch,
} from "./fundNameMatch";

describe("fundNameMatch", () => {
  it("strips 灵活配置 so Alipay full names match East Money short names", () => {
    expect(normalizeFundNameForLookup("万家宏观择时多策略灵活配置混合C")).toBe(
      "万家宏观择时多策略混合C",
    );
  });

  it("auto-picks the C-class short name and ignores the A-class sibling", () => {
    expect(
      pickUniqueFundMatch("万家宏观择时多策略灵活配置混合C", [
        { fund_code: "519212", fund_name: "万家宏观择时多策略混合A" },
        { fund_code: "017787", fund_name: "万家宏观择时多策略混合C" },
      ])?.fund_code,
    ).toBe("017787");
  });

  it("does not auto-pick 招商前沿 when the OCR name is 招商医疗保健", () => {
    expect(
      pickUniqueFundMatch("招商医疗保健股票A", [
        { fund_code: "011373", fund_name: "招商前沿医疗保健股票A" },
        { fund_code: "011374", fund_name: "招商前沿医疗保健股票C" },
      ]),
    ).toBeNull();
  });

  it("picks the closest share-class sibling when no exact name exists", () => {
    expect(
      pickBestFundMatch("招商医疗保健股票A", [
        { fund_code: "011373", fund_name: "招商前沿医疗保健股票A" },
        { fund_code: "011374", fund_name: "招商前沿医疗保健股票C" },
      ])?.fund_code,
    ).toBe("011373");
  });

  it("matches a held fund whose East Money name dropped 型证券投资基金", () => {
    expect(
      pickUniqueFundMatch("招商医疗保健股票A", [
        { fund_code: "000960", fund_name: "招商医疗保健股票型证券投资基金A" },
      ])?.fund_code,
    ).toBe("000960");
  });
});
