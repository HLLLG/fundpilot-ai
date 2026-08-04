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

it("shows mainline status and keeps it explicitly research-only", () => {
  render(
    <SectorOpportunityCard
      item={{
        sector_label: "CPO",
        score: 72,
        mainline_regime: {
          status: "confirmed",
          score: 81.5,
          confidence: "中",
          research_ranking_only: true,
          execution_eligible: false,
          features: {
            relative_return_20d_percent: 8.2,
            relative_strength_percentile: 91.4,
            advancing_ratio_percent: 68.5,
          },
          source_dates: {
            sector_price_source: "sina_current_large_constituents_proxy",
            proxy_member_count: 8,
          },
          evidence: ["近20日相对沪深300超额 +8.20%"],
          risks: ["接近20日高位"],
        },
      }}
    />,
  );

  expect(screen.getByTestId("mainline-status")).toHaveTextContent("主线已确认");
  expect(screen.getByTestId("mainline-evidence")).toHaveTextContent("仅研究排序");
  expect(screen.getByText("+8.20%")).toBeInTheDocument();
  expect(screen.getByText("+91.40%")).toBeInTheDocument();
  expect(screen.getByText("+68.50%")).toBeInTheDocument();
  expect(screen.getByText(/当前大市值成分股代理（8 只）/)).toBeInTheDocument();
  expect(screen.getByText(/风险：接近20日高位/)).toBeInTheDocument();
});

it("renders v3 orthogonal blocks and overheat as a smaller first tranche", () => {
  render(
    <SectorOpportunityCard
      item={{
        sector_label: "半导体",
        score_policy_version: "sector_entry_maturity.2026-08.v3",
        entry_state: "ready_to_start",
        direction_score: 74.2,
        trend_strength_score: 82,
        participation_score: 58,
        position_risk_score: 61,
        block_weights: {
          trend_strength: 0.7,
          participation: 0.15,
          position_risk: 0.15,
        },
        overheat_flags: ["近5日涨幅超过12%，短期加速"],
        first_tranche_scale: 0.4,
      }}
    />,
  );

  expect(screen.getByText(/趋势强度 \(70%\)/)).toBeInTheDocument();
  expect(screen.getByText(/资金参与 \(15%\)/)).toBeInTheDocument();
  expect(screen.getByText(/价格位置 \(15%\)/)).toBeInTheDocument();
  expect(screen.getByText(/它们不是三重确认/)).toBeInTheDocument();
  expect(screen.getByTestId("overheat-disclosure")).toHaveTextContent("首批按 40% 执行");
  expect(screen.queryByText("入场成熟")).not.toBeInTheDocument();
});

it("explains flow-inflection and high-elasticity direction priority", () => {
  render(
    <SectorOpportunityCard
      item={{
        sector_label: "中药",
        score_policy_version: "sector_entry_maturity.2026-08.v3",
        entry_state: "ready_on_pullback",
        waiting_reason_code: "fund_entry_confirmation",
        direction_score: 62.4,
        trend_strength_score: 68,
        participation_score: 25,
        position_risk_score: 58,
        selection_path: "flow_inflection_probe",
        selection_priority_score: 72.8,
        sector_annualized_volatility_20d_percent: 31.2,
        sector_elasticity_percentile: 84,
      }}
    />,
  );

  expect(screen.getByText("等待基金信号")).toBeInTheDocument();
  expect(screen.getByText("资金拐点")).toBeInTheDocument();
  expect(screen.getByTestId("sector-high-elasticity")).toHaveTextContent("高弹性");
  expect(screen.getByTestId("sector-selection-priority")).toHaveTextContent(
    "今日资金转强，优先于普通等待方向",
  );
  expect(screen.getByTestId("sector-selection-priority")).toHaveTextContent(
    "20日年化波动 31.20%",
  );
});
