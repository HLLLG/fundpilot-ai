// @vitest-environment jsdom

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import "@testing-library/jest-dom/vitest";

import type {
  UsDataSourceStatus,
  UsFuturesQuote,
  UsMarketSnapshot,
  UsSessionKind,
  UsdCnyQuote,
} from "@/lib/api";
import { US_SESSION_LABEL } from "@/lib/usMarketOverview";
import { UsMarketOverview } from "@/components/UsMarketOverview";

afterEach(() => {
  cleanup();
});

function futuresQuote(overrides: Partial<UsFuturesQuote> = {}): UsFuturesQuote {
  return {
    symbol: "NASDAQ_FUT",
    display_name: "纳斯达克",
    last_price: 19850.5,
    change_percent: 0.62,
    quote_time: "2026-06-17T08:12:00-04:00",
    status: "ok",
    ...overrides,
  };
}

function usdCnyQuote(overrides: Partial<UsdCnyQuote> = {}): UsdCnyQuote {
  return {
    last_price: 6.8096,
    change_percent: -0.02,
    quote_time: "2026-06-17",
    status: "ok",
    ...overrides,
  };
}

function snapshot(overrides: Partial<UsMarketSnapshot> = {}): UsMarketSnapshot {
  return {
    session_kind: "after_hours",
    session_label: "盘后",
    et_date: "2026-06-17",
    updated_at: "2026-06-17T08:12:30-04:00",
    futures: [
      futuresQuote({ symbol: "NASDAQ_FUT", display_name: "纳斯达克", last_price: 19850.5, change_percent: -1.34 }),
      futuresQuote({ symbol: "SP500_FUT", display_name: "标普500", last_price: 5510.25, change_percent: -1.21 }),
      futuresQuote({ symbol: "DOW_FUT", display_name: "道琼斯", last_price: 40120, change_percent: -0.98 }),
    ],
    usd_cny: usdCnyQuote(),
    qdii: [],
    qdii_status: "unavailable",
    futures_status: "ok",
    forex_status: "ok",
    available: true,
    from_cache: false,
    stale: false,
    message: null,
    ...overrides,
  };
}

const NUMERIC = /\d/;

describe("UsMarketOverview", () => {
  it("renders the loading indicator when loading with no data", () => {
    render(<UsMarketOverview data={null} loading />);

    expect(screen.getByText(/加载美股概览/)).toBeInTheDocument();
  });

  it("renders futures values and percent in the ok state", () => {
    render(<UsMarketOverview data={snapshot()} loading={false} />);

    expect(screen.getByText("19,850.5")).toBeInTheDocument();
    expect(screen.getByText("-1.34%")).toBeInTheDocument();
    expect(screen.getByText("5,510.25")).toBeInTheDocument();
    expect(screen.getByText("-1.21%")).toBeInTheDocument();
    expect(screen.getByText("-0.98%")).toBeInTheDocument();
    expect(screen.getByText("汇率")).toBeInTheDocument();
    expect(screen.getByText("6.8096")).toBeInTheDocument();
  });

  it("renders the value plus a 上次 badge in the stale state", () => {
    const data = snapshot({
      forex_status: "stale",
      stale: true,
      usd_cny: usdCnyQuote({ status: "stale" }),
    });
    render(<UsMarketOverview data={data} loading={false} />);

    expect(screen.getByText(/上次 /)).toBeInTheDocument();
    expect(screen.getByText("6.8096")).toBeInTheDocument();
  });

  it("renders 暂不可用 and no numeric value in the unavailable state", () => {
    const data = snapshot({
      forex_status: "unavailable",
      usd_cny: {
        last_price: null,
        change_percent: null,
        quote_time: null,
        status: "unavailable",
      },
    });
    render(<UsMarketOverview data={data} loading={false} />);

    expect(screen.getAllByText("暂不可用").length).toBeGreaterThan(0);

    const usdCnyName = screen.getByText("汇率");
    const card = usdCnyName.closest("div")?.parentElement as HTMLElement;
    expect(card).toBeTruthy();
    expect(within(card).getByText("暂不可用")).toBeInTheDocument();
    expect(within(card).queryByText(NUMERIC)).toBeNull();
  });

  it("renders the A-share context hint and does not show QDII list UI", () => {
    render(<UsMarketOverview data={snapshot()} loading={false} />);

    expect(screen.getByText(/A 股下一交易日情绪参考/)).toBeInTheDocument();
    expect(screen.queryByText("参考涨跌")).not.toBeInTheDocument();
    expect(screen.queryByText("易方达全球成长精选")).not.toBeInTheDocument();
  });

  describe("session label rendering per session_kind", () => {
    const cases: UsSessionKind[] = ["pre_market", "regular", "after_hours", "closed"];

    it.each(cases)("renders the Chinese label for %s", (kind) => {
      const data = snapshot({ session_kind: kind, session_label: "应被映射覆盖" });
      render(<UsMarketOverview data={data} loading={false} />);

      expect(screen.getByText(`美股 · ${US_SESSION_LABEL[kind]}`)).toBeInTheDocument();
    });
  });
});

const _statusGuard: UsDataSourceStatus[] = ["ok", "stale", "unavailable"];
void _statusGuard;
