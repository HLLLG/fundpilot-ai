// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import "@testing-library/jest-dom/vitest";

import { DiscoveryCandidatePoolPanel } from "@/components/DiscoveryCandidatePoolPanel";
import type { DiscoveryCandidatePoolItem } from "@/lib/api";
import {
  installMatchMedia,
  type MatchMediaController,
} from "@/test/matchMedia";

const DESKTOP_QUERY = "(min-width: 1024px)";
let matchMedia: MatchMediaController;

beforeEach(() => {
  matchMedia = installMatchMedia({ [DESKTOP_QUERY]: false });
});

afterEach(() => {
  cleanup();
  matchMedia.restore();
});

const candidate: DiscoveryCandidatePoolItem = {
  fund_code: "006081",
  fund_name: "海富通电子传媒股票A",
  sector_label: "电子",
  fund_quality_score: 53.37,
  sector_fit_score: 37.12,
  quality_reasons: ["板块高置信匹配"],
  quality_penalties: ["缺少基金规模"],
  quality_gate: {
    eligible: false,
    status: "watch_only",
    reasons: ["核心字段缺失：最新规模"],
    missing_fields: ["fund_scale_yi"],
    coverage_percent: 85.7,
    data_as_of: "2026-07-10",
  },
  return_3m_percent: 9.79,
  return_6m_percent: 11.1,
  return_1y_percent: 26.13,
};

describe("DiscoveryCandidatePoolPanel", () => {
  it("uses a labelled disclosure and marks observation candidates without calling them recommended", () => {
    render(
      <DiscoveryCandidatePoolPanel
        pool={[candidate]}
        decisionStatusByCode={{ [candidate.fund_code]: "watch_only" }}
      />,
    );

    const trigger = screen.getByRole("button", { name: /本次候选池/ });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");

    const card = screen.getByRole("article", { name: /研究观察/ });
    expect(card).toHaveTextContent("质量分");
    expect(card).toHaveTextContent("匹配分");
    expect(card).toHaveTextContent("近3月");
    expect(card).toHaveTextContent("近1年");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByText("查看数据完整性与质量依据").closest("summary")).toHaveClass("min-h-11");
    expect(card).toHaveTextContent("待补/刷新 1 项");
    expect(card).toHaveTextContent("仅作研究观察，不会形成可执行买入动作");
    expect(screen.queryByText("已推荐")).not.toBeInTheDocument();
  });

  it("distinguishes executable, conditional and observation candidate statuses", () => {
    const candidates = [
      candidate,
      { ...candidate, fund_code: "006082", fund_name: "等待基金" },
      { ...candidate, fund_code: "006083", fund_name: "观察基金" },
    ];
    render(
      <DiscoveryCandidatePoolPanel
        pool={candidates}
        decisionStatusByCode={{
          "006081": "actionable",
          "006082": "conditional_wait",
          "006083": "watch_only",
        }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /本次候选池/ }));

    expect(screen.getByRole("article", { name: /可执行/ })).toBeInTheDocument();
    expect(screen.getByRole("article", { name: /等待条件/ })).toBeInTheDocument();
    expect(screen.getByRole("article", { name: /研究观察/ })).toBeInTheDocument();
  });

  it("keeps a captioned semantic table for larger viewports", () => {
    matchMedia.setMatches(DESKTOP_QUERY, true);
    render(<DiscoveryCandidatePoolPanel pool={[candidate]} selectedCodes={[]} />);
    fireEvent.click(screen.getByRole("button", { name: /本次候选池/ }));

    expect(screen.getByRole("table", { name: /候选池评分/ })).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "基金候选池明细表，可左右滚动查看" }),
    ).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("columnheader", { name: "质量分" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "交易条件" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "同类研究 / 基准" })).toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: candidate.fund_code })).toBeInTheDocument();
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
  });

  it("summarizes field completeness and quality degradation without exposing long text as columns", () => {
    matchMedia.setMatches(DESKTOP_QUERY, true);
    const completeCandidate: DiscoveryCandidatePoolItem = {
      ...candidate,
      fund_code: "006082",
      fund_name: "字段完整基金",
      fund_scale_yi: 3.2,
      fund_scale_basis: "nav_times_latest_shares",
      fund_manager: "测试经理",
      established_date: "2020-01-01",
      profile_status: "complete",
      profile_sources: ["sina.fund_scale_open_sina"],
      quality_penalties: [],
      quality_gate: {
        eligible: true,
        status: "eligible",
        reasons: [],
        missing_fields: [],
        coverage_percent: 100,
        data_as_of: "2026-07-10",
      },
    };
    const excludedCandidate: DiscoveryCandidatePoolItem = {
      ...completeCandidate,
      fund_code: "006083",
      fund_name: "已剔除基金",
      quality_gate: {
        ...completeCandidate.quality_gate!,
        eligible: false,
        status: "excluded",
        reasons: ["最新估算规模低于0.5亿元"],
      },
    };

    render(
      <DiscoveryCandidatePoolPanel
        pool={[candidate, completeCandidate, excludedCandidate]}
      />,
    );

    expect(
      screen.getByLabelText(
        "核心字段完整 2 只，待补全或刷新 1 只，质量降级 2 只，状态未记录 0 只",
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /本次候选池/ }));

    expect(screen.getByRole("columnheader", { name: "证据状态" })).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "质量理由" })).not.toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "短板" })).not.toBeInTheDocument();
    expect(screen.getAllByText("查看数据完整性与质量依据")).toHaveLength(3);
    expect(screen.getAllByText("待补/刷新 1 项")).not.toHaveLength(0);
    expect(screen.getByText("最新规模", { selector: "p" })).toBeInTheDocument();
    expect(screen.getAllByText(/规模 3\.20 亿元/)).not.toHaveLength(0);
    expect(screen.getAllByText(/新浪基金规模/)).not.toHaveLength(0);
  });

  it("shows verified purchase minimums, unlimited quota, source and revalidation", () => {
    const tradeableCandidate: DiscoveryCandidatePoolItem = {
      ...candidate,
      tradeability: {
        data_status: "complete",
        freshness: "fresh",
        purchase_state: "open",
        purchase_status: "开放申购",
        redemption_state: "open",
        redemption_status: "开放赎回",
        minimum_initial_purchase_yuan: 10,
        minimum_additional_purchase_yuan: 1,
        explicit_minimum_holding_days: 365,
        daily_purchase_limit_yuan: null,
        daily_purchase_limit_unlimited: true,
        revalidation_required: true,
        source_ids: [
          "akshare.fund_purchase_em",
          "eastmoney.fundf10_purchase_info",
        ],
        checked_at: "2026-07-14T09:59:30+08:00",
        tradeability_gate: {
          status: "eligible",
          effective_initial_min_purchase_yuan: 100,
          max_purchase_unlimited: true,
          revalidation_required: true,
        },
      },
    };

    render(<DiscoveryCandidatePoolPanel pool={[tradeableCandidate]} />);
    fireEvent.click(screen.getByRole("button", { name: /本次候选池/ }));

    const evidence = screen.getByLabelText("基金交易条件");
    expect(evidence).toHaveTextContent("申购开放");
    expect(evidence).toHaveTextContent("赎回开放");
    expect(evidence).toHaveTextContent("首次起购 ¥10");
    expect(evidence).toHaveTextContent("追加起购 ¥1");
    expect(evidence).toHaveTextContent("单日限额 无限额");
    expect(evidence).toHaveTextContent("最低持有 365 天");
    expect(evidence).toHaveTextContent("东方财富申赎清单 + 东方财富基金费率页");
    expect(evidence).toHaveTextContent("下单前复核");
  });

  it("degrades historical candidates without tradeability fields safely", () => {
    render(
      <DiscoveryCandidatePoolPanel
        pool={[{ ...candidate, tradeability: {}, tradeability_gate: {}, cost_assessment: {} }]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /本次候选池/ }));

    expect(screen.getByLabelText("交易条件未记录")).toHaveTextContent(
      "历史报告未记录交易条件",
    );
    expect(screen.getByLabelText("同类研究与基准未记录")).toHaveTextContent(
      "历史报告未记录同类分位与基准角色",
    );
  });

  it("labels peer percentiles as descriptive research and exposes metric sample sizes", () => {
    const researchedCandidate: DiscoveryCandidatePoolItem = {
      ...candidate,
      peer_group: {
        schema_version: "fund_peer_group.v1",
        group_key: "domestic/equity/active/high_risk",
        group_label: "境内 / 股票 / 主动 / 高风险",
        classification_confidence: "high",
      },
      peer_rank: {
        schema_version: "peer_rank.v1",
        status: "qualified",
        qualified: true,
        execution_tilt_eligible: false,
        universe: { independent_peer_family_count: 42 },
        metrics: {
          return_3m_percent: {
            label: "近3月收益",
            role: "performance",
            percentile: 87.5,
            sample_count: 38,
          },
          max_drawdown_1y_percent: {
            label: "近1年最大回撤",
            role: "risk",
            percentile: 62.5,
            sample_count: 40,
          },
          tracking_error_1y_percent: {
            label: "近1年跟踪误差",
            applicable: false,
            applicability: "not_applicable",
            available: false,
            sample_count: 0,
            percentile: null,
            qualified: false,
            reason: "metric_not_applicable_to_equity",
          },
        },
      },
    };

    render(<DiscoveryCandidatePoolPanel pool={[researchedCandidate]} />);
    fireEvent.click(screen.getByRole("button", { name: /本次候选池/ }));

    const research = screen.getByRole("group", { name: "同类研究与基准" });
    expect(research).toHaveTextContent("境内 / 股票 / 主动 / 高风险");
    expect(research).toHaveTextContent("描述数据完整");
    expect(research).toHaveTextContent("独立基金家族样本 42");
    expect(research).toHaveTextContent("近3月收益");
    expect(research).toHaveTextContent("87.5 分位 · n=38");
    expect(research).toHaveTextContent("近1年最大回撤");
    expect(research).not.toHaveTextContent("近1年跟踪误差");
    expect(research).toHaveTextContent("仅研究描述，不参与金额分配");
  });

  it("keeps a tracking index visibly separate from a formally verified benchmark", () => {
    const trackingCandidate: DiscoveryCandidatePoolItem = {
      ...candidate,
      benchmark_research: {
        schema_version: "fund_benchmark_mapping.v1",
        comparison_role: "tracking_reference",
        formal_excess_eligible: false,
        benchmark_code: "000300",
        benchmark_name: "沪深300指数",
        reason: "contract_source_not_verified",
      },
      benchmark_metrics: {
        schema_version: "fund_benchmark_research.v1",
        status: "qualified",
        qualified: true,
        comparison_role: "tracking_reference",
        formal_excess_eligible: false,
        benchmark_code: "000300",
        benchmark_name: "沪深300指数",
        horizons: {
          "1y": {
            status: "available",
            fund_return_percent: 8.2,
            benchmark_return_percent: 6.1,
            formal_excess_return_percent: null,
            reference_difference_percent: 2.1,
          },
        },
        rolling_comparison: {
          window_days: 20,
          window_count: 233,
          reference_outperformance_rate_percent: 58.4,
        },
        alignment: { common_return_sample_days: 252 },
      },
    };

    render(<DiscoveryCandidatePoolPanel pool={[trackingCandidate]} />);
    fireEvent.click(screen.getByRole("button", { name: /本次候选池/ }));

    const research = screen.getByRole("group", { name: "同类研究与基准" });
    expect(research).toHaveTextContent("跟踪参考（非正式基准）");
    expect(research).toHaveTextContent("沪深300指数");
    expect(research).toHaveTextContent("不得用于正式超额收益判断");
    expect(research).toHaveTextContent("近1年相对参考差异");
    expect(research).toHaveTextContent("+2.10%");
    expect(research).toHaveTextContent("20日滚动胜率");
    expect(research).toHaveTextContent("对齐指标仅研究描述，不参与金额分配");
    expect(research).not.toHaveTextContent("正式业绩基准");
  });

  it("uses the formal benchmark label only for an explicitly eligible formal comparison", () => {
    const formalCandidate: DiscoveryCandidatePoolItem = {
      ...candidate,
      benchmark_comparison: {
        schema_version: "fund_benchmark_mapping.v1",
        comparison_role: "formal_excess",
        formal_excess_eligible: true,
        qualified: true,
        mapping_id: "fbm_verified",
        benchmark_name: "沪深300收益率×80%+中债综合指数收益率×20%",
        contract_verification_kind: "verified_fund_contract",
      },
      benchmark_metrics: {
        schema_version: "fund_benchmark_research.v1",
        status: "qualified",
        qualified: true,
        comparison_role: "formal_excess",
        formal_excess_eligible: true,
        benchmark_name: "沪深300收益率×80%+中债综合指数收益率×20%",
        horizons: {
          "6m": {
            status: "available",
            fund_return_percent: 5.5,
            benchmark_return_percent: 4.25,
            formal_excess_return_percent: 1.25,
            reference_difference_percent: null,
          },
        },
        rolling_comparison: {
          window_days: 20,
          formal_excess_win_rate_percent: 61.2,
        },
        alignment: { common_return_sample_days: 126 },
      },
    };

    render(<DiscoveryCandidatePoolPanel pool={[formalCandidate]} />);
    fireEvent.click(screen.getByRole("button", { name: /本次候选池/ }));

    const research = screen.getByRole("group", { name: "同类研究与基准" });
    expect(research).toHaveTextContent("正式业绩基准");
    expect(research).toHaveTextContent("沪深300收益率×80%+中债综合指数收益率×20%");
    expect(research).not.toHaveTextContent("不得用于正式超额收益判断");
    expect(research).toHaveTextContent("近6月正式超额");
    expect(research).toHaveTextContent("+1.25%");
  });
});
