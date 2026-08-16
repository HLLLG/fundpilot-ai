import { describe, expect, it } from "vitest";

import {
  changeFromPriceAndPercent,
  formatIndexChange,
  formatIndexPercent,
  formatIndexPrice,
  indexTone,
  toCnIndexCards,
  toUsIndexCards,
} from "@/lib/marketIndexStrip";

describe("marketIndexStrip formatters", () => {
  it("formats price and signed change like Yangjibao cards", () => {
    expect(formatIndexPrice(3927.18).replace(/,/g, "")).toBe("3927.18");
    expect(formatIndexChange(0.22)).toBe("+0.22");
    expect(formatIndexChange(-12.4)).toBe("-12.40");
    expect(formatIndexPercent(0.01)).toBe("+0.01%");
    expect(formatIndexPercent(-1.2)).toBe("-1.20%");
  });

  it("maps A-share quotes to strip cards", () => {
    const cards = toCnIndexCards([
      {
        symbol: "000001",
        display_name: "上证指数",
        last_price: 3927.18,
        change: 0.22,
        change_percent: 0.01,
        status: "ok",
      },
    ]);
    expect(cards[0]).toMatchObject({
      key: "000001",
      name: "上证指数",
      lastPrice: 3927.18,
      change: 0.22,
      changePercent: 0.01,
    });
  });

  it("derives US point change from price and percent", () => {
    expect(changeFromPriceAndPercent(100, 1)).toBeCloseTo(0.9901, 4);
    const cards = toUsIndexCards([
      {
        symbol: "DOW_FUT",
        display_name: "道琼斯",
        last_price: 53839.99,
        change_percent: 0.13,
        status: "ok",
      },
    ]);
    expect(cards).toHaveLength(1);
    expect(cards[0].name).toBe("道琼斯");
  });

  it("uses red-up / green-down / flat tones", () => {
    expect(indexTone(0.01, "ok")).toBe("up");
    expect(indexTone(-0.2, "ok")).toBe("down");
    expect(indexTone(0, "ok")).toBe("flat");
    expect(indexTone(1, "unavailable")).toBe("flat");
  });
});
