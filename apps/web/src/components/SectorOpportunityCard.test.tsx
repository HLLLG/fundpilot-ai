// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, expect, it } from "vitest";

import { SectorOpportunityCard } from "@/components/SectorOpportunityCard";

afterEach(cleanup);

it("shows live today flow without fabricating missing five-day history", () => {
  render(
    <SectorOpportunityCard
      item={{
        sector_label: "人工智能",
        today_available: true,
        five_day_available: false,
        history_point_count: 1,
        today_main_force_net_yi: 12.34,
        cumulative_5d_net_yi: null,
      }}
    />,
  );

  expect(screen.getByText("12.34 亿")).toBeInTheDocument();
  expect(screen.getByText("5日历史暂缺")).toBeInTheDocument();
  expect(screen.queryByText("— 亿")).not.toBeInTheDocument();
});

it("shows both main-force values with exactly five history points", () => {
  render(
    <SectorOpportunityCard
      item={{
        sector_label: "半导体",
        today_available: true,
        five_day_available: true,
        history_point_count: 5,
        today_main_force_net_yi: -248.78,
        cumulative_5d_net_yi: -162.81,
      }}
    />,
  );

  expect(screen.getByText("-248.78 亿")).toBeInTheDocument();
  expect(screen.getByText("-162.81 亿")).toBeInTheDocument();
});

it("keeps legacy explicit numeric flow values visible", () => {
  render(
    <SectorOpportunityCard
      item={{
        sector_label: "消费",
        today_main_force_net_yi: 8.5,
        cumulative_5d_net_yi: 21.25,
      }}
    />,
  );

  expect(screen.getByText("8.50 亿")).toBeInTheDocument();
  expect(screen.getByText("21.25 亿")).toBeInTheDocument();
});

it("treats zero as a real main-force value", () => {
  render(
    <SectorOpportunityCard
      item={{
        sector_label: "银行",
        today_available: true,
        five_day_available: true,
        history_point_count: 5,
        today_main_force_net_yi: 0,
        cumulative_5d_net_yi: 0,
      }}
    />,
  );

  expect(screen.getAllByText("0 亿")).toHaveLength(2);
  expect(screen.queryByText("今日数据暂缺")).not.toBeInTheDocument();
  expect(screen.queryByText("5日历史暂缺")).not.toBeInTheDocument();
});

it("does not show units for independently unavailable flow values", () => {
  render(
    <SectorOpportunityCard
      item={{
        sector_label: "新能源",
        today_available: false,
        five_day_available: false,
        history_point_count: 0,
        today_main_force_net_yi: 99,
        cumulative_5d_net_yi: 88,
      }}
    />,
  );

  expect(screen.getByText("今日数据暂缺")).toBeInTheDocument();
  expect(screen.getByText("5日历史暂缺")).toBeInTheDocument();
  expect(screen.queryByText("— 亿")).not.toBeInTheDocument();
  expect(screen.queryByText("99.00 亿")).not.toBeInTheDocument();
  expect(screen.queryByText("88.00 亿")).not.toBeInTheDocument();
});
