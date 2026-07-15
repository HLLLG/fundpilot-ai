// @vitest-environment jsdom

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import "@testing-library/jest-dom/vitest";

import { FundLookthroughEvidence } from "@/components/FundLookthroughEvidence";
import type { FundLookthroughResearch } from "@/lib/api";

afterEach(cleanup);

function qualifiedResearch(): FundLookthroughResearch {
  return {
    schema_version: "fund_lookthrough_research.v1",
    status: "qualified",
    decision_at: "2026-07-14T10:00:00+08:00",
    execution_qualified: false,
    qualification: {
      research_qualified: true,
      execution_qualified: false,
    },
    portfolio: {
      scope: "whole_account",
      disclosed_security_mass_lower_bound_percent: 55,
      identity_known_security_mass_lower_bound_percent: 42.5,
      unknown_account_mass_percent: 57.5,
      unknown_fund_holdings_scope_mass_percent: 61.25,
      industry_unknown_mass_percent: 70,
      listing_market_unknown_mass_percent: 69,
      security_exposure_lower_bounds: [
        {
          security_key: "CN:600519",
          security_name: "贵州茅台",
          exposure_lower_bound_percent: 8.25,
        },
      ],
      industry_exposure_lower_bounds: [
        { industry: "食品饮料", exposure_lower_bound_percent: 12.5 },
      ],
      listing_market_exposure_lower_bounds: [
        { listing_market: "上海证券交易所", exposure_lower_bound_percent: 31 },
      ],
    },
    candidates: [
      {
        fund_code: "006081",
        portfolio_security_overlap_lower_bound_percent: 12.4,
        portfolio_overlap_interpretation: "positive_disclosed_overlap_lower_bound",
        vintage_alignment: { status: "same_as_of_date" },
        max_existing_fund_overlap_lower_bound_percent: 16.8,
        snapshot: {
          report_period: "2026-03-31",
          as_of_date: "2026-03-31",
          available_at: "2026-04-25T09:00:00+08:00",
        },
        top_common_with_portfolio: [
          {
            security_key: "CN:600519",
            security_name: "贵州茅台",
            overlap_contribution_lower_bound_percent: 3.2,
          },
        ],
      },
    ],
    resolution_audit: { resolved_count: 8, unresolved_count: 2 },
  };
}

describe("FundLookthroughEvidence", () => {
  it("shows disclosed lower bounds beside retained unknown mass and snapshot timing", () => {
    render(
      <FundLookthroughEvidence
        research={qualifiedResearch()}
        candidateNames={{ "006081": "海富通电子传媒股票A" }}
        context="discovery"
      />,
    );

    const region = screen.getByRole("region", { name: "基金持仓穿透证据" });
    expect(region).toHaveTextContent("已披露下限 · 已识别证券");
    expect(region).toHaveTextContent("≥ 42.5%");
    expect(region).toHaveTextContent("证券披露质量下限 ≥ 55%");
    expect(region).toHaveTextContent("未知质量 · 全账户");
    expect(region).toHaveTextContent("57.5%");
    expect(region).toHaveTextContent("未知质量 · 基金持仓口径");
    expect(region).toHaveTextContent("61.25%");
    expect(region).toHaveTextContent("证券 · 已披露下限");
    expect(region).toHaveTextContent("贵州茅台");
    expect(region).toHaveTextContent("≥ 8.25%");
    expect(region).toHaveTextContent("未知质量 70%");
    expect(region).toHaveTextContent("未知质量 69%");
    expect(region).toHaveTextContent("报告期 2026-03-31");
    expect(region).toHaveTextContent("时点 2026-07-14 10:00");
    expect(region).toHaveTextContent("仅风险研究，不授权配置");
    expect(region).toHaveTextContent("穿透资格审计已记录");
    expect(region).toHaveTextContent("证券身份解析审计已记录");
  });

  it("uses interpretation semantics for no-common evidence and never claims zero overlap", () => {
    const research = qualifiedResearch();
    research.candidates = [
      {
        fund_code: "006081",
        portfolio_security_overlap_lower_bound_percent: 0,
        common_disclosed_weight_percent: 0,
        portfolio_overlap_interpretation: "no_common_in_disclosed_scope",
        max_existing_fund_overlap_lower_bound_percent: 0,
        top_common_with_portfolio: [],
      },
    ];

    render(<FundLookthroughEvidence research={research} />);

    const candidate = screen.getByRole("article", { name: "候选基金持仓重合证据" });
    expect(candidate).toHaveTextContent(
      "披露范围内未发现共同证券，完整组合重合未知",
    );
    expect(within(candidate).queryByText(/0%\s*重合/)).not.toBeInTheDocument();
    expect(within(candidate).queryByText(/≥\s*0%/)).not.toBeInTheDocument();
    expect(within(candidate).queryByText(/完全分散/)).not.toBeInTheDocument();
  });

  it("renders partial evidence as a neutral descriptive downgrade", () => {
    const research = qualifiedResearch();
    research.status = "partial";
    research.execution_qualified = false;

    render(<FundLookthroughEvidence research={research} />);

    const region = screen.getByRole("region", { name: "基金持仓穿透证据" });
    expect(region).toHaveTextContent("部分披露");
    expect(region).toHaveTextContent("证据不完整，仅展示当前可核验的披露下限");
    expect(region).toHaveTextContent("仅风险研究，不授权配置");
    expect(region).toHaveTextContent("≥ 42.5%");
  });

  it("summarizes mixed daily holding vintages instead of selecting the first report period", () => {
    const research = qualifiedResearch();
    research.candidates = [];
    research.existing_funds = [
      { fund_code: "000001", snapshot: { report_period: "2025-12-31" } },
      { fund_code: "000002", snapshot: { report_period: "2026-03-31" } },
    ];

    render(<FundLookthroughEvidence research={research} context="daily" />);

    const region = screen.getByRole("region", { name: "基金持仓穿透证据" });
    expect(region).toHaveTextContent("报告期 多报告期 · 最新披露拼图");
    expect(region).not.toHaveTextContent("报告期 2025-12-31");
  });

  it.each([
    "cross_vintage_disclosed_similarity",
    "cross_vintage_descriptive_similarity",
    "cross_vintage_no_common_in_disclosed_scope",
    "cross_vintage_identity_evidence_insufficient",
  ])("keeps %s descriptive and never formats it as a current overlap lower bound", (interpretation) => {
    const research = qualifiedResearch();
    research.candidates = [
      {
        fund_code: "006081",
        portfolio_security_overlap_lower_bound_percent: 12.4,
        max_existing_fund_overlap_lower_bound_percent: 16.8,
        portfolio_overlap_interpretation: interpretation,
        top_common_with_portfolio: [
          {
            security_name: "贵州茅台",
            overlap_contribution_lower_bound_percent: 3.2,
          },
        ],
      },
    ];

    render(<FundLookthroughEvidence research={research} />);

    const candidate = screen.getByRole("article", { name: "候选基金持仓重合证据" });
    expect(candidate).toHaveTextContent(
      "报告期不一致，仅作跨期披露相似度，不是当前重合下界",
    );
    expect(candidate).toHaveTextContent("跨期共同披露证券 · 仅作相似度");
    expect(candidate).not.toHaveTextContent("已披露重合下限");
    expect(candidate).not.toHaveTextContent("最高披露重合下限");
    expect(candidate).not.toHaveTextContent("贡献下限");
    expect(candidate).not.toHaveTextContent("≥ 12.4%");
  });

  it.each(["cross_vintage", "mixed"])(
    "gives authoritative vintage status %s precedence over a positive interpretation",
    (status) => {
      const research = qualifiedResearch();
      research.candidates = [
        {
          fund_code: "006081",
          portfolio_security_overlap_lower_bound_percent: 12.4,
          max_existing_fund_overlap_lower_bound_percent: 16.8,
          portfolio_overlap_interpretation: "positive_disclosed_overlap_lower_bound",
          vintage_alignment: { status },
          top_common_with_portfolio: [
            {
              security_name: "贵州茅台",
              overlap_contribution_lower_bound_percent: 3.2,
            },
          ],
        },
      ];

      render(<FundLookthroughEvidence research={research} />);

      const candidate = screen.getByRole("article", { name: "候选基金持仓重合证据" });
      expect(candidate).toHaveTextContent(
        "报告期不一致，仅作跨期披露相似度，不是当前重合下界",
      );
      expect(candidate).not.toHaveTextContent("已披露重合下限");
      expect(candidate).not.toHaveTextContent("最高披露重合下限");
      expect(candidate).not.toHaveTextContent("贡献下限");
      expect(candidate).not.toHaveTextContent("≥ 12.4%");
    },
  );

  it("does not display residual overlap numbers for an unknown interpretation", () => {
    const research = qualifiedResearch();
    research.candidates = [
      {
        fund_code: "006081",
        portfolio_security_overlap_lower_bound_percent: 12.4,
        max_existing_fund_overlap_lower_bound_percent: 16.8,
        portfolio_overlap_interpretation: "unknown_future_interpretation",
        vintage_alignment: { status: "same_as_of_date" },
        top_common_with_portfolio: [
          {
            security_name: "贵州茅台",
            overlap_contribution_lower_bound_percent: 3.2,
          },
        ],
      },
    ];

    render(<FundLookthroughEvidence research={research} />);

    const candidate = screen.getByRole("article", { name: "候选基金持仓重合证据" });
    expect(candidate).toHaveTextContent("披露重合证据不足，完整组合重合未知");
    expect(candidate).not.toHaveTextContent("已披露重合下限");
    expect(candidate).not.toHaveTextContent("最高披露重合下限");
    expect(candidate).not.toHaveTextContent("贡献下限");
    expect(candidate).not.toHaveTextContent("≥ 12.4%");
  });

  it("keeps unavailable research neutral and retains unknown instead of inventing zeroes", () => {
    render(
      <FundLookthroughEvidence
        research={{
          schema_version: "fund_lookthrough_research.v1",
          status: "unavailable",
          portfolio: null,
          candidates: [],
        }}
      />,
    );

    const region = screen.getByRole("region", { name: "基金持仓穿透证据" });
    expect(region).toHaveTextContent("资料暂不可用");
    expect(region).toHaveTextContent("未知质量保持未知，不记为 0");
    expect(region).toHaveTextContent("不将缺失资料解释为零敞口或零重合");
    expect(region).not.toHaveTextContent("完全分散");
  });

  it("maps a record-keyed candidate to its discovery fund name", () => {
    const research = qualifiedResearch();
    research.candidates = {
      "006081": {
        portfolio_security_overlap_lower_bound_percent: 12.4,
        portfolio_overlap_interpretation: "positive_disclosed_overlap_lower_bound",
        vintage_alignment: { status: "same_as_of_date" },
      },
    };

    render(
      <FundLookthroughEvidence
        research={research}
        candidateNames={{ "006081": "海富通电子传媒股票A" }}
        context="discovery"
      />,
    );

    const candidate = screen.getByRole("article", {
      name: "海富通电子传媒股票A持仓重合证据",
    });
    expect(candidate).toHaveTextContent("006081");
    expect(candidate).toHaveTextContent("已披露重合下限 ≥ 12.4%");
  });

  it("does not render anything for a legacy report without lookthrough research", () => {
    const { container } = render(<FundLookthroughEvidence research={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });
});
