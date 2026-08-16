import { describe, expect, it } from "vitest";
import type { FundReturnDistribution } from "@/lib/api";
import {
  formatFundReturnDistributionUpdatedAt,
  fundReturnDistributionPollMs,
  hasSettledOfficialDistribution,
  shouldRefreshFundReturnDistribution,
  type DistributionRefreshSession,
} from "@/components/FundReturnDistributionPanel";

const official: FundReturnDistribution = {
  available: true,
  source_mode: "official_nav",
  as_of_date: "2026-08-14",
};

const estimate: FundReturnDistribution = {
  available: true,
  source_mode: "intraday_estimate",
  as_of_date: "2026-08-14",
  as_of_datetime: "2026-08-14T10:32:00+08:00",
};

function session(
  overrides: Partial<DistributionRefreshSession> = {},
): DistributionRefreshSession {
  return {
    is_trading_day: true,
    session_kind: "trading_day_intraday",
    calendar_date: "2026-08-14",
    effective_trade_date: "2026-08-14",
    ...overrides,
  };
}

describe("shouldRefreshFundReturnDistribution", () => {
  it("keeps polling a trading day until today's official NAV is ready", () => {
    expect(shouldRefreshFundReturnDistribution(session(), estimate)).toBe(true);
    expect(shouldRefreshFundReturnDistribution(session(), official)).toBe(false);
  });

  it("does not refetch last Friday's official NAV on a weekend", () => {
    const weekend = session({
      is_trading_day: false,
      session_kind: "non_trading_day",
      calendar_date: "2026-08-16",
      effective_trade_date: "2026-08-14",
    });
    expect(hasSettledOfficialDistribution(weekend, official)).toBe(true);
    expect(shouldRefreshFundReturnDistribution(weekend, official)).toBe(false);
    expect(fundReturnDistributionPollMs(weekend, official)).toBe(30 * 60_000);
  });

  it("does not refetch last close during pre-open", () => {
    const preOpen = session({
      session_kind: "trading_day_pre_open",
      calendar_date: "2026-08-17",
      effective_trade_date: "2026-08-14",
    });
    expect(shouldRefreshFundReturnDistribution(preOpen, official)).toBe(false);
  });

  it("asks for a first official snapshot when the weekend cache is empty", () => {
    const weekend = session({
      is_trading_day: false,
      session_kind: "non_trading_day",
      calendar_date: "2026-08-16",
      effective_trade_date: "2026-08-14",
    });
    expect(shouldRefreshFundReturnDistribution(weekend, null)).toBe(true);
  });
});

describe("formatFundReturnDistributionUpdatedAt", () => {
  it("pins official NAV to the 15:00 close, matching 养基宝", () => {
    expect(formatFundReturnDistributionUpdatedAt(official)).toBe("更新：2026-08-14 15:00");
  });

  it("shows the Shanghai clock for an intraday estimate", () => {
    expect(formatFundReturnDistributionUpdatedAt(estimate)).toBe("更新：2026-08-14 10:32");
  });
});
