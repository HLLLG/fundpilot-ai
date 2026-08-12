// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
  expect(screen.getByText(/结构修复 \(15%\)/)).toBeInTheDocument();
  expect(screen.getByText(/它们不是三重确认/)).toBeInTheDocument();
  expect(screen.getByTestId("overheat-disclosure")).toHaveTextContent(
    "本次金额按 40% 计算",
  );
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

it("shows probability-sized early entry before full trend confirmation", () => {
  render(
    <SectorOpportunityCard
      item={{
        sector_label: "云计算",
        score_policy_version: "sector_entry_maturity.2026-08.v3",
        entry_state: "forming",
        direction_score: 58.4,
        trend_strength_score: 55,
        participation_score: 68,
        position_risk_score: 58,
        trend_formation_probability: 68,
        formation_probability_band: "building",
        probability_early_probe_eligible: true,
        selection_path: "probability_early_probe",
        first_tranche_scale: 0.4,
        today_available: true,
        today_main_force_net_yi: 18,
        five_day_available: true,
        cumulative_5d_net_yi: -2,
      }}
    />,
  );

  expect(screen.getByText("可提前试仓")).toBeInTheDocument();
  expect(screen.getByText("提前试仓")).toBeInTheDocument();
  // 数值不变，但单位是「分/100」而不是「%」：这个数没经过校准，不能当概率读。
  expect(screen.getByTestId("formation-probability")).toHaveTextContent("68");
  expect(screen.getByTestId("formation-probability")).toHaveTextContent("/100");
  expect(screen.getByTestId("formation-probability")).toHaveTextContent("信号偏强");
  expect(screen.getByTestId("formation-probability")).not.toHaveTextContent("大概率形成");
  expect(screen.getByTestId("formation-probability")).toHaveTextContent("计划仓位的 40%");
  expect(screen.getByText("18 亿")).toBeInTheDocument();
});

it("keeps key direction facts visible while supporting details start collapsed", () => {
  render(
    <SectorOpportunityCard
      collapsibleDetails
      item={{
        sector_label: "云计算",
        score_policy_version: "sector_entry_maturity.2026-08.v3",
        entry_state: "ready_to_start",
        direction_score: 77,
        trend_strength_score: 76,
        participation_score: 93,
        position_risk_score: 69,
        trend_formation_probability: 90,
        formation_probability_band: "strong",
        first_tranche_scale: 1,
        today_available: true,
        today_main_force_net_yi: 28.7,
        five_day_available: true,
        cumulative_5d_net_yi: 22.83,
        entry_triggers: ["买入并录入持仓后，由日报继续确认趋势强度与资金参与度"],
      }}
    />,
  );

  expect(screen.getByText("云计算")).toBeVisible();
  expect(screen.getByText("可以开始布局")).toBeVisible();
  expect(screen.getByTestId("formation-probability")).toHaveTextContent("90");
  expect(screen.getByTestId("formation-probability")).toHaveTextContent("/100");
  expect(screen.getByTestId("formation-probability")).toHaveTextContent("计划仓位的 100%");
  expect(screen.queryByTestId("sector-opportunity-details")).not.toBeInTheDocument();
  expect(screen.queryByText("28.70 亿")).not.toBeInTheDocument();

  const expandDetails = screen.getByRole("button", { name: "展开云计算方向详情" });
  expect(expandDetails).toHaveAttribute("aria-expanded", "false");
  expect(expandDetails).not.toHaveTextContent(/展开|收起/);
  fireEvent.click(expandDetails);

  expect(screen.getByRole("button", { name: "收起云计算方向详情" })).toHaveAttribute(
    "aria-expanded",
    "true",
  );
  expect(screen.getByTestId("sector-opportunity-details")).toBeVisible();
  expect(screen.getByText("28.70 亿")).toBeVisible();
  expect(
    screen.getByText(/买入并录入持仓后，由日报继续确认趋势强度与资金参与度/),
  ).toBeVisible();
});

it("stops endorsing a broken-down direction and hides the entry tranche size", () => {
  // 生产事故（2026-08-12 半导体材料）：同一张卡片上「72/100 信号偏强」+「本次比例 计划仓位的
  // 60%」与下方的「大幅减仓评估 −50%」「本轮不加仓」并列，读起来自相矛盾。
  // 后端现在对已跌破退出线的方向发 `trend_breakdown` 档位、并且不再给 first_tranche_scale。
  render(
    <SectorOpportunityCard
      item={{
        sector_label: "半导体材料",
        score_policy_version: "sector_entry_maturity.2026-08.v3",
        entry_state: "forming",
        direction_score: 53.32,
        trend_strength_score: 40.19,
        participation_score: 96.13,
        position_risk_score: 71.75,
        trend_formation_probability: 71.82,
        formation_probability_band: "trend_breakdown",
        first_tranche_scale: null,
        overheat_flags: ["近5日涨幅超过12%，短期加速"],
        today_available: true,
        today_main_force_net_yi: 5.11,
        five_day_available: true,
        cumulative_5d_net_yi: 19.19,
        entry_triggers: [
          "20日相对强度与趋势持续性需先止跌回升至入场线 60（当前 40.2）",
          "主线状态升至形成中、已确认或拥挤",
        ],
      }}
    />,
  );

  const signal = screen.getByTestId("formation-probability");
  // 分数照旧展示，不隐藏证据。
  expect(signal).toHaveTextContent("72");
  expect(signal).toHaveTextContent("/100");
  // 但不再用强弱标签替它背书。
  expect(signal).toHaveTextContent("趋势已破位");
  expect(signal).not.toHaveTextContent("信号偏强");
  // 没有任何入场通道授权时不给出仓位比例。
  expect(signal).toHaveTextContent("本轮不投入");
  expect(signal).not.toHaveTextContent("计划仓位的");
  // 过热披露仍在，但不再宣称一个「本次金额」。
  const overheat = screen.getByTestId("overheat-disclosure");
  expect(overheat).toHaveTextContent("本轮无入场通道，不产生本次金额");
  expect(overheat).not.toHaveTextContent("本次金额按 60%");
  // 等待条件不再预设"已经在改善"。
  expect(screen.getByText(/需先止跌回升至入场线/)).toBeInTheDocument();
});

it("still shows the tranche size when a channel actually authorises it", () => {
  // 别把修复做成"谁都不显示比例"：ready_to_start 必须照旧给出本次比例。
  render(
    <SectorOpportunityCard
      item={{
        sector_label: "煤炭",
        score_policy_version: "sector_entry_maturity.2026-08.v3",
        entry_state: "ready_to_start",
        direction_score: 80,
        trend_strength_score: 78,
        participation_score: 90,
        position_risk_score: 70,
        trend_formation_probability: 88,
        formation_probability_band: "strong",
        first_tranche_scale: 0.65,
        today_available: true,
        today_main_force_net_yi: 12,
        five_day_available: true,
        cumulative_5d_net_yi: 15,
      }}
    />,
  );

  const signal = screen.getByTestId("formation-probability");
  expect(signal).toHaveTextContent("计划仓位的 65%");
  expect(signal).toHaveTextContent("强趋势");
  expect(signal).not.toHaveTextContent("本轮不投入");
});
