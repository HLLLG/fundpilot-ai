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
});
