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
    expect(screen.getByText("查看质量理由与短板")).toHaveClass("min-h-11");
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
});
