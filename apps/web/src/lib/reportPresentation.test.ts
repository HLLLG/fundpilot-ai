import { describe, expect, it } from "vitest";
import type { Report } from "@/lib/api";
import {
  confidenceDisplayLabel,
  displayFundRecommendations,
  groupFundRecommendations,
  keyReasonLines,
  cardSpecificValidationNotes,
  meaningfulNewsLines,
  portfolioRecommendationLines,
  safeDiagnosticMetrics,
  resolveReportProviderStatus,
  scopeReportToCurrentHoldings,
  selectNextTradingPlan,
  selectPrimaryReason,
} from "@/lib/reportPresentation";

type FundRec = Report["fund_recommendations"][number];

function rec(overrides: Partial<FundRec>): FundRec {
  return {
    fund_code: "000001",
    fund_name: "测试基金",
    action: "观察",
    points: ["保持观察"],
    ...overrides,
  };
}

describe("daily report presentation", () => {
  it("groups actionable recommendations and keeps pause ahead of watch", () => {
    const add = rec({ fund_code: "1", action: "分批加仓" });
    const watch = rec({ fund_code: "2", action: "观察" });
    const pause = rec({ fund_code: "3", action: "暂停追涨" });
    const reduce = rec({ fund_code: "4", action: "减仓评估" });
    expect(groupFundRecommendations([watch, add, pause, reduce])).toEqual({
      needsAction: [add, reduce],
      observing: [pause, watch],
    });
  });

  it("filters empty news placeholders", () => {
    expect(
      meaningfulNewsLines(["暂无明确利好", " 无 ", "真实政策利好", "真实政策利好"]),
    ).toEqual(["真实政策利好"]);
  });

  it("keeps portfolio lines while removing legacy per-fund strings", () => {
    const report = {
      fund_recommendations: [],
      recommendations: ["组合整体观望", "[000001 · 观察] 保持观察"],
    } as unknown as Report;
    expect(portfolioRecommendationLines(report)).toEqual(["组合整体观望"]);
  });

  it("parses legacy per-fund recommendation strings", () => {
    const report = {
      fund_recommendations: [],
      recommendations: ["[000001 · 观察] 保持观察", "[000001 · 观察] 等待企稳"],
    } as unknown as Report;
    expect(displayFundRecommendations(report)).toEqual([
      {
        fund_code: "000001",
        fund_name: "000001",
        action: "观察",
        points: ["保持观察", "等待企稳"],
      },
    ]);
  });

  it("selects position basis before non-guard points", () => {
    expect(
      selectPrimaryReason(
        rec({
          suggested_position_change_basis: "集中度超过上限",
          points: ["已按风控规则调整", "板块资金偏弱"],
        }),
      ),
    ).toBe("集中度超过上限");
  });

  it("extracts the next-trading-day conditional plan", () => {
    expect(
      selectNextTradingPlan(["资金偏弱", "下交易日：若再跌2%则减仓"]),
    ).toBe("下交易日：若再跌2%则减仓");
  });

  it("drops system-copy points from the why-this-recommendation list", () => {
    expect(
      keyReasonLines(
        rec({
          points: [
            "系统校验后的最终动作：减仓评估。建议相对当前持仓减少 25%。",
            "赎回开放已核验，但缺少逐笔申购时间，无法确认锁定期与适用赎回费；保留减仓比例。",
            "房地产趋势已跌破退出线",
            "下交易日：若再跌2%则减仓",
          ],
        }),
      ),
    ).toEqual(["房地产趋势已跌破退出线"]);
  });

  it("keeps only card-specific validation notes", () => {
    expect(
      cardSpecificValidationNotes([
        "IC 回测已过期，IC 未参与本次结论",
        "1路已参与量化证据综合置信：不足",
        "当日涨跌为板块估算",
        "调整比例已由系统按最终动作重新计算，原始模型金额或比例不直接作为依据。",
        "按先进先出，本次减仓将触及仍在 7 天惩罚费窗口内的批次",
        "你 2026-08-12 刚买入过该基金",
      ]),
    ).toEqual([
      "按先进先出，本次减仓将触及仍在 7 天惩罚费窗口内的批次",
      "你 2026-08-12 刚买入过该基金",
    ]);
  });

  it("maps confidence into beginner-facing reference labels", () => {
    expect(confidenceDisplayLabel("高")).toBe("参考度：高");
    expect(confidenceDisplayLabel("中")).toBe("参考度：中");
    expect(confidenceDisplayLabel("低")).toBe("参考度：有限");
    expect(confidenceDisplayLabel(undefined)).toBeNull();
  });

  it("hides impossible diagnostic values but preserves normal hints", () => {
    expect(
      safeDiagnosticMetrics({ return_1y_percent: 8220.94, max_drawdown_1y_percent: -160.53 }),
    ).toEqual({ hints: [], invalid: true });
    expect(
      safeDiagnosticMetrics({ return_1y_percent: 12.3, max_drawdown_1y_percent: -18.6 }),
    ).toEqual({ hints: ["近1年 12.3%", "最大回撤 -18.6%"], invalid: false });
  });

  it("scopes the latest report view to the current authoritative holdings", () => {
    const report = {
      holdings: [
        { fund_code: "010236", fund_name: "当前基金", holding_amount: 1_000 },
        { fund_code: "021627", fund_name: "旧基金", holding_amount: 2_000 },
      ],
      snapshots: [
        { fund_code: "010236", fund_name: "当前基金" },
        { fund_code: "021627", fund_name: "旧基金" },
      ],
      fund_recommendations: [
        rec({ fund_code: "010236", fund_name: "当前基金" }),
        rec({ fund_code: "021627", fund_name: "旧基金" }),
      ],
      analysis_facts: {
        holdings: [{ fund_code: "010236" }, { fund_code: "021627" }],
      },
    } as unknown as Report;

    const scoped = scopeReportToCurrentHoldings(report, [
      {
        fund_code: "010236",
        fund_name: "当前基金",
        holding_amount: 1_000,
        return_percent: 0,
      },
    ]);

    expect(scoped.hiddenRecommendationCount).toBe(1);
    expect(scoped.report.fund_recommendations.map((item) => item.fund_code)).toEqual([
      "010236",
    ]);
    expect(scoped.report.holdings.map((item) => item.fund_code)).toEqual(["010236"]);
    expect(scoped.report.snapshots.map((item) => item.fund_code)).toEqual(["010236"]);
    expect(
      (scoped.report.analysis_facts as { holdings: Array<{ fund_code: string }> }).holdings,
    ).toEqual([{ fund_code: "010236" }]);
    expect(report.fund_recommendations).toHaveLength(2);
  });
});

// ---------------------------------------------------------------------------
// 回归：列表摘要占位不得让日报整页崩溃
//
// 线上报障 7bc1ab07dce7a312833f107ecb3004f9：
// `TypeError: Cannot read properties of undefined (reading 'length')`，
// 抛在 `scopeReportToCurrentHoldings` 读 `report.fund_recommendations.length` 时。
//
// 成因：后端 `_REPORT_SUMMARY_FIELDS`（apps/api/app/database.py）有意不在
// `GET /api/reports` 里下发正文数组，而 Dashboard 的 `hydrateReport` 会先把列表摘要
// 当占位 Report 渲染、再按 id 拉正文。`Report` 类型却把那些数组声明为必填，
// 于是 typecheck / lint / build 全都拦不住 —— 修复前这四道检查都是绿的。
// 只有真的喂一个残缺对象进来才能发现。
//
// 所以这几个用例的作用是钉住两件事：
//   1. `asArray()` 那些守卫不是"多余的类型判断"，删掉就会重新白屏；
//   2. 补空数组之后，正文到达时按持仓收窄的行为必须依旧生效。
// ---------------------------------------------------------------------------

/** 只包含 `_REPORT_SUMMARY_FIELDS` 投影出的字段，正文数组一律缺失。 */
function summaryOnlyReport(): Report {
  return {
    id: "r-1",
    created_at: "2026-08-07T05:58:06Z",
    title: "今日日报",
    summary: "摘要",
    provider: "deepseek",
    risk: {
      level: "medium",
      suggested_action: "watch",
      weighted_return_percent: 1.2,
      alerts: [],
    },
    caveats: [],
    market_context: [],
  } as unknown as Report;
}

// 持仓必须非空：为空会命中 `!currentHoldings?.length` 短路，绕开真正的崩溃点。
// 线上那次正是"账户有持仓"的用户才触发的。
const currentHoldings = [
  { fund_code: "010236", fund_name: "当前基金", holding_amount: 1_000, return_percent: 0 },
];

describe("summary-only report placeholder", () => {
  it("scopes without throwing when the body arrays are absent entirely", () => {
    const report = summaryOnlyReport();
    expect(() => scopeReportToCurrentHoldings(report, currentHoldings)).not.toThrow();
    expect(scopeReportToCurrentHoldings(report, currentHoldings)).toEqual({
      report,
      hiddenRecommendationCount: 0,
    });
  });

  it("renders the remaining ReportPanel body as empty rather than crashing", () => {
    const report = summaryOnlyReport();
    expect(displayFundRecommendations(report)).toEqual([]);
    expect(portfolioRecommendationLines(report)).toEqual([]);
  });

  it("keeps narrowing to current holdings once the detail body arrives", () => {
    const detail = {
      ...summaryOnlyReport(),
      holdings: [],
      snapshots: [],
      recommendations: [],
      fund_recommendations: [
        rec({ fund_code: "010236", fund_name: "当前基金" }),
        rec({ fund_code: "021627", fund_name: "旧基金" }),
      ],
    } as unknown as Report;

    const scoped = scopeReportToCurrentHoldings(detail, currentHoldings);

    expect(scoped.hiddenRecommendationCount).toBe(1);
    expect(scoped.report.fund_recommendations.map((item) => item.fund_code)).toEqual([
      "010236",
    ]);
  });
});

// ---------------------------------------------------------------------------
// provider 兜底的可见性。
//
// 后端 provider 调用失败时是 fail-closed 的：整份报告换成离线兜底，每条建议降为
// 「观察 / 风险复核」，金额与仓位动作全部阻断。前端历史实现在生成完成时无条件提示
// 「深度分析日报已生成（Pro + …）」，于是顶部宣布成功、正文每张卡片都写
// 「模型服务不可用」——用户会以为是展示 bug，而不是模型压根没跑成。
// ---------------------------------------------------------------------------

function reportWithPipeline(
  provider: string,
  pipeline?: Record<string, unknown>,
): Report {
  return {
    id: "r-provider",
    created_at: "2026-08-08T06:40:00Z",
    title: "每日基金操作日报",
    summary: "摘要",
    provider,
    caveats: [],
    market_context: [],
    ...(pipeline ? { analysis_facts: { pipeline } } : {}),
  } as unknown as Report;
}

describe("report provider status", () => {
  it("reports a model-backed run as success", () => {
    const status = resolveReportProviderStatus(
      reportWithPipeline("deepseek-v4-pro", {
        provider_status: "success",
        attempted_model: "deepseek-v4-pro",
      }),
    );

    expect(status.modelBacked).toBe(true);
    expect(status.tone).toBe("success");
    expect(status.message).toContain("深度分析日报已生成");
  });

  it("never claims success for a provider fallback", () => {
    const status = resolveReportProviderStatus(
      reportWithPipeline("offline-fallback", {
        provider_status: "fallback",
        provider_failure_category: "invalid_json",
        provider_failure_retryable: true,
      }),
    );

    expect(status.modelBacked).toBe(false);
    expect(status.tone).toBe("warning");
    expect(status.message).not.toContain("已生成");
    expect(status.message).toContain("模型返回内容未通过结构校验");
    expect(status.message).toContain("稍后重新生成");
  });

  it("tells the user to fix configuration when a retry cannot help", () => {
    const status = resolveReportProviderStatus(
      reportWithPipeline("offline-fallback", {
        provider_status: "fallback",
        provider_failure_category: "authentication",
        provider_failure_retryable: false,
      }),
    );

    expect(status.retryable).toBe(false);
    expect(status.message).toContain("模型服务认证失败");
    expect(status.message).toContain("检查模型服务配置");
  });

  it("distinguishes an unconfigured model from a failed call", () => {
    const status = resolveReportProviderStatus(
      reportWithPipeline("offline", { provider_status: "offline", provider_attempted: false }),
    );

    expect(status.attempted).toBe(false);
    expect(status.message).toContain("未配置模型服务");
    expect(status.tone).toBe("warning");
  });

  it("falls back to the provider name when pipeline metadata is missing", () => {
    // 旧报告没有 pipeline.provider_status，只能靠 provider 字段判断。
    expect(resolveReportProviderStatus(reportWithPipeline("offline-fallback")).modelBacked).toBe(
      false,
    );
    expect(resolveReportProviderStatus(reportWithPipeline("deepseek-v4-pro")).modelBacked).toBe(
      true,
    );
  });

  it("keeps an unknown failure category out of the user-facing copy", () => {
    const status = resolveReportProviderStatus(
      reportWithPipeline("offline-fallback", {
        provider_status: "fallback",
        provider_failure_category: "some_future_category",
      }),
    );

    expect(status.failureCategory).toBe("some_future_category");
    expect(status.message).toContain("模型调用失败");
    expect(status.message).not.toContain("some_future_category");
  });

  it("treats a missing retryable flag as retryable", () => {
    // 缺字段时不该把偶发问题说成配置错误。
    const status = resolveReportProviderStatus(
      reportWithPipeline("offline-fallback", {
        provider_status: "fallback",
        provider_failure_category: "timeout",
      }),
    );

    expect(status.retryable).toBe(true);
    expect(status.message).toContain("稍后重新生成");
  });
});
