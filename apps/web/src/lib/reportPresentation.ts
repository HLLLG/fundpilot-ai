import type { Holding, Report } from "@/lib/api";
import { actionTone } from "@/lib/actionStyles";
import { translateEvidenceText } from "@/lib/decisionText";

export type FundRecommendation = Report["fund_recommendations"][number];
type Snapshot = Report["snapshots"][number];

export type CurrentPortfolioReportView = {
  report: Report;
  hiddenRecommendationCount: number;
};

const EMPTY_NEWS = new Set(["", "无", "暂无", "暂无利好", "暂无利空", "暂无明确利好", "暂无明确利空"]);
const ACTION_TONES = new Set(["add", "reduce", "deep_reduce", "clear_all"]);
const GUARD_NOTE = /已按.*(?:风控|规则).*调整|对照本地规则/;
const NEXT_PLAN = /(?:下一交易日|下交易日|开盘)/;
const SYSTEM_POINT = /系统校验后的最终动作|赎回开放已核验，但缺少逐笔申购时间/;
const GENERIC_VALIDATION =
  /IC\s*回测已过期|IC\s*未参与本次结论|量化证据综合置信|现有非 IC 证据置信偏低|现有可用证据置信偏低|当日涨跌为板块估算|调整比例已由系统按最终动作重新计算|交易条件或逐笔持有期仍需在实际操作前核对/;

function normalizedFundName(value?: string | null): string {
  return (value ?? "").replace(/\s+/g, "").trim();
}

/**
 * 运行时形状兜底。
 *
 * `Report` 把正文数组声明为必填，但实际到达这里的对象不一定完整：列表投影
 * (`GET /api/reports`) 不下发 holdings / snapshots / market_news /
 * fund_recommendations / recommendations，旧版本写下的缓存也可能缺字段。
 * 这一层是日报的渲染入口，读到 undefined 就是整页白屏，所以不假设、只收敛形状。
 * 数组存在时原样返回，正常报告的行为与身份都不变。
 */
function asArray<T>(value: T[] | undefined | null): T[] {
  return Array.isArray(value) ? value : [];
}

/**
 * The latest report can outlive a portfolio edit. Keep the stored report intact
 * for audit/export, but scope its active on-screen view to today's holdings so
 * deleted profile rows cannot still look like current positions.
 */
export function scopeReportToCurrentHoldings(
  report: Report,
  currentHoldings?: Holding[],
): CurrentPortfolioReportView {
  const allRecommendations = asArray(report.fund_recommendations);
  if (!currentHoldings?.length || !allRecommendations.length) {
    return { report, hiddenRecommendationCount: 0 };
  }

  const codes = new Set(
    currentHoldings
      .map((holding) => holding.fund_code?.trim())
      .filter((code): code is string => Boolean(code && code !== "000000")),
  );
  const names = new Set(
    currentHoldings
      .map((holding) => normalizedFundName(holding.fund_name))
      .filter(Boolean),
  );
  const isCurrent = (item: { fund_code?: string | null; fund_name?: string | null }) => {
    const code = item.fund_code?.trim();
    if (code && code !== "000000") return codes.has(code);
    return names.has(normalizedFundName(item.fund_name));
  };

  const fundRecommendations = allRecommendations.filter(isCurrent);
  const hiddenRecommendationCount =
    allRecommendations.length - fundRecommendations.length;
  if (hiddenRecommendationCount <= 0) {
    return { report, hiddenRecommendationCount: 0 };
  }

  const facts = report.analysis_facts as
    | (Report["analysis_facts"] & { holdings?: Array<{ fund_code?: string; fund_name?: string }> })
    | undefined;
  const analysisFacts = facts
    ? {
        ...facts,
        holdings: Array.isArray(facts.holdings)
          ? facts.holdings.filter(isCurrent)
          : facts.holdings,
      }
    : report.analysis_facts;

  return {
    report: {
      ...report,
      holdings: asArray(report.holdings).filter(isCurrent),
      snapshots: asArray(report.snapshots).filter(isCurrent),
      fund_recommendations: fundRecommendations,
      analysis_facts: analysisFacts,
    },
    hiddenRecommendationCount,
  };
}

/**
 * 一份日报到底是模型产出的，还是 provider 失败后的确定性兜底。
 *
 * 后端在 provider 调用失败时会 fail-closed：整份报告换成 `_offline_report`，
 * 每条建议被降为「观察 / 风险复核」、金额与仓位动作全部阻断，`provider` 记为
 * `offline-fallback`，失败分类写进 `analysis_facts.pipeline`。
 * 前端历史实现在生成完成时无条件提示「深度分析日报已生成」，从不看这些字段，
 * 于是出现过顶部说"已生成 Pro 深度分析"、正文每张卡片都写"模型服务不可用"的自相矛盾，
 * 让人以为是展示 bug 而不是模型没跑成。
 */
export type ReportProviderStatus = {
  /** 正文是否真的来自模型。 */
  modelBacked: boolean;
  /** provider 是否尝试过调用（未配置模型时为 false）。 */
  attempted: boolean;
  /** `provider_failure_category`，仅兜底时有值。 */
  failureCategory: string | null;
  /** 是否值得直接重试（限流/超时可重试；认证/余额需要先改配置）。 */
  retryable: boolean;
  message: string;
  /** 与 `NoticeTone` 兼容的子集，避免 lib 反向依赖组件层的类型。 */
  tone: "success" | "warning";
};

/** 失败分类 → 面向用户的短语。未知分类走通用兜底，不至于漏出内部标识。 */
const PROVIDER_FAILURE_PHRASES: Record<string, string> = {
  authentication: "模型服务认证失败",
  account_balance: "模型服务账户不可用",
  rate_limited: "模型服务触发限流",
  timeout: "模型调用超时",
  provider_5xx: "模型服务暂时异常",
  provider_4xx: "模型请求未被服务接受",
  connection: "无法连接模型服务",
  stream_error: "模型流式传输中断",
  transport_error: "模型网络请求失败",
  empty_content: "模型返回空内容",
  invalid_json: "模型返回内容未通过结构校验",
};

const SUCCESS_MESSAGE = "深度分析日报已生成（Pro + 有界扩展证据 + 可选风控审校）。";

function reportPipeline(report: Report): Record<string, unknown> {
  const facts = report.analysis_facts;
  if (!facts || typeof facts !== "object") return {};
  const pipeline = (facts as { pipeline?: unknown }).pipeline;
  return pipeline && typeof pipeline === "object" ? (pipeline as Record<string, unknown>) : {};
}

export function resolveReportProviderStatus(report: Report): ReportProviderStatus {
  const pipeline = reportPipeline(report);
  const provider = (report.provider ?? "").trim();
  const providerStatus = String(pipeline.provider_status ?? "").trim();

  // `provider_status` 是后端权威字段；旧报告可能没有，回退到 provider 名字判断。
  const isFallback =
    providerStatus === "fallback" || provider === "offline-fallback";
  const isOffline = providerStatus === "offline" || provider === "offline";
  if (!isFallback && !isOffline) {
    return {
      modelBacked: true,
      attempted: true,
      failureCategory: null,
      retryable: false,
      message: SUCCESS_MESSAGE,
      tone: "success",
    };
  }

  if (isOffline && !isFallback) {
    return {
      modelBacked: false,
      attempted: false,
      failureCategory: null,
      retryable: false,
      message: "未配置模型服务，本次仅按本地规则输出观察与风险提示；配置后重新生成可获得模型分析。",
      tone: "warning",
    };
  }

  const rawCategory = pipeline.provider_failure_category;
  const failureCategory =
    typeof rawCategory === "string" && rawCategory.trim() ? rawCategory.trim() : null;
  const phrase =
    (failureCategory && PROVIDER_FAILURE_PHRASES[failureCategory]) || "模型调用失败";
  // 后端只在明确可重试时写 true；缺字段按"可重试"处理，避免把偶发问题说成配置错误。
  const retryable = pipeline.provider_failure_retryable !== false;
  const nextStep = retryable
    ? "稍后重新生成即可。"
    : "请先检查模型服务配置或账户状态，再重新生成。";

  return {
    modelBacked: false,
    attempted: true,
    failureCategory,
    retryable,
    message: `${phrase}，本次日报已降级为观察与风险提示，未产出可执行建议。${nextStep}`,
    tone: "warning",
  };
}

export function meaningfulNewsLines(values?: string[]): string[] {
  const result: string[] = [];
  for (const raw of values ?? []) {
    const value = raw.trim().replace(/[。；;]+$/, "");
    if (EMPTY_NEWS.has(value) || result.includes(value)) continue;
    result.push(value);
  }
  return result;
}

export function displayFundRecommendations(report: Report): FundRecommendation[] {
  const fundRecommendations = asArray(report.fund_recommendations);
  if (fundRecommendations.length > 0) return fundRecommendations;
  const byCode = new Map<string, FundRecommendation>();
  for (const line of asArray(report.recommendations)) {
    const match = line.match(/^\[(\d{6})\s*[·｜|]\s*([^\]]+)\]\s*(.*)$/);
    if (!match) continue;
    const [, fundCode, action, rest] = match;
    const point = rest.trim();
    const existing = byCode.get(fundCode);
    if (!existing) {
      byCode.set(fundCode, {
        fund_code: fundCode,
        fund_name: fundCode,
        action: action.trim(),
        points: point ? [point] : [],
      });
    } else if (point && !existing.points.includes(point)) {
      existing.points.push(point);
    }
  }
  return [...byCode.values()];
}

export function portfolioRecommendationLines(report: Report): string[] {
  const lines = asArray(report.recommendations);
  if (asArray(report.fund_recommendations).length > 0) return lines;
  return lines.filter((line) => !/^\[\d{6}\s*[·｜|]/.test(line.trim()));
}

export function groupFundRecommendations(items: FundRecommendation[]) {
  const needsAction: FundRecommendation[] = [];
  const pauses: FundRecommendation[] = [];
  const watches: FundRecommendation[] = [];
  for (const item of items) {
    const tone = actionTone(item.action);
    const hasPositionChange =
      item.suggested_position_change_percent != null &&
      item.suggested_position_change_percent !== 0;
    if (ACTION_TONES.has(tone) || hasPositionChange) needsAction.push(item);
    else if (tone === "pause") pauses.push(item);
    else watches.push(item);
  }
  return { needsAction, observing: [...pauses, ...watches] };
}

export function selectPrimaryReason(item: FundRecommendation): string {
  const candidate =
    item.suggested_position_change_basis?.trim() ||
    item.amount_note?.trim() ||
    item.points.find(
      (point) => point.trim() && !GUARD_NOTE.test(point) && !SYSTEM_POINT.test(point),
    ) ||
    item.points.find((point) => point.trim() && !SYSTEM_POINT.test(point)) ||
    item.points[0] ||
    "暂无需要立即操作的新增信号";
  return translateEvidenceText(candidate);
}

export function selectNextTradingPlan(points: string[]): string | null {
  const match = points.find((point) => NEXT_PLAN.test(point));
  return match ? translateEvidenceText(match) : null;
}

export function keyReasonLines(item: FundRecommendation): string[] {
  const result: string[] = [];
  for (const point of item.points) {
    if (GUARD_NOTE.test(point) || NEXT_PLAN.test(point) || SYSTEM_POINT.test(point)) continue;
    const value = translateEvidenceText(point.trim());
    if (value && !result.includes(value)) result.push(value);
    if (result.length === 3) break;
  }
  return result;
}

function evidenceKey(value?: string | null): string {
  return value ? translateEvidenceText(value.trim()).trim() : "";
}

/**
 * 「为什么这样建议」里的因果句。
 *
 * 卡头已经占用主因、区块上方占用下一交易日预案。有第二条因果时去掉与卡头重复的那句；
 * 瘦身后只剩 1 条因果时再抽走会让整个区块空白，这时把主因留在这里。
 */
export function whyReasonLines(item: FundRecommendation): string[] {
  const newsKeys = new Set(
    [...meaningfulNewsLines(item.news_bullish), ...meaningfulNewsLines(item.news_bearish)]
      .map(evidenceKey)
      .filter(Boolean),
  );
  const candidates = keyReasonLines(item).filter((reason) => !newsKeys.has(evidenceKey(reason)));
  const primaryKey = evidenceKey(selectPrimaryReason(item));
  const extras = candidates.filter((reason) => evidenceKey(reason) !== primaryKey);
  return extras.length > 0 ? extras : candidates;
}

export function cardSpecificValidationNotes(values?: string[]): string[] {
  const result: string[] = [];
  for (const raw of values ?? []) {
    const value = translateEvidenceText(raw.trim());
    if (!value || GENERIC_VALIDATION.test(value) || result.includes(value)) continue;
    result.push(value);
  }
  return result;
}

export function confidenceDisplayLabel(value?: string): string | null {
  if (!value) return null;
  if (value.includes("高")) return "参考度：高";
  if (value.includes("中")) return "参考度：中";
  return "参考度：有限";
}

export function safeDiagnosticMetrics(
  snapshot: Pick<Snapshot, "return_1y_percent" | "max_drawdown_1y_percent">,
): { hints: string[]; invalid: boolean } {
  const hints: string[] = [];
  let invalid = false;
  const yearly = snapshot.return_1y_percent;
  if (yearly != null) {
    if (Number.isFinite(yearly) && yearly >= -100 && yearly <= 1000) hints.push(`近1年 ${yearly}%`);
    else invalid = true;
  }
  const drawdown = snapshot.max_drawdown_1y_percent;
  if (drawdown != null) {
    if (Number.isFinite(drawdown) && drawdown >= -100 && drawdown <= 0) hints.push(`最大回撤 ${drawdown}%`);
    else invalid = true;
  }
  return { hints, invalid };
}
