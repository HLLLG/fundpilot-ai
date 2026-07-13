/**
 * Shared helpers for humanizing quantitative decision text produced by the
 * backend guards (discovery_guard / recommendation_guard). The backend already
 * humanizes most strings, but some internal field-name fragments can still
 * leak through (older records, edge cases); these regexes translate them into
 * readable Chinese so both the 荐基 and 日报 panels present a consistent voice.
 */

const PATTERN_LABELS: Record<string, string> = {
  accumulation: "回调中有资金承接",
  aligned_up: "上涨有资金配合",
  distribution: "涨幅较快但资金流出",
  flow_date_mismatch: "资金日期需核验",
  flow_turning_positive: "资金开始转正",
  multi_day_outflow_then_inflow: "资金由流出转回流",
  price_flow_aligned_up: "上涨有资金配合",
  weak_outflow: "资金偏弱",
};

export function patternLabel(pattern: string): string {
  const normalized = pattern.trim().toLowerCase();
  return PATTERN_LABELS[normalized] ?? pattern;
}

export function trackLabel(track: string): string {
  const normalized = track.trim().toLowerCase();
  if (normalized === "momentum") {
    return "顺势观察";
  }
  if (normalized === "setup") {
    return "蓄势观察";
  }
  return track;
}

function formatAbsPercent(value: string | number): string {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return String(value);
  }
  return Math.abs(number).toFixed(2).replace(/\.00$/, "").replace(/0$/, "");
}

export function formatMetric(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) {
    return "—";
  }
  return Number(value).toFixed(2).replace(/\.00$/, "");
}

const DIVERGENCE_RULE_LABELS: Record<string, string> = {
  flow_price_distribution: "涨但资金流出",
  flow_price_accumulation: "跌但资金流入",
};

type DivergenceBacktestLike = {
  sample_days?: number;
  by_rule?: Record<
    string,
    {
      rule_id: string;
      label: string;
      trigger_count: number;
      hit_rate_percent: number | null;
      edge_percent?: number | null;
      significant?: boolean | null;
    }
  >;
} | null | undefined;

/**
 * M1.3：把 `flow_divergence_backtest.by_rule` 里显著的量价背离规则，压缩成一句
 * 可直接展示的历史回测证据文案，如"过去 98 个交易日「涨但资金流出」出现 18 次，
 * 次日下跌概率 72%，超基准 22pp"。只展示 `significant=true` 的规则——不显著的
 * 桶只是噪音，不应喧宾夺主。共享给 SectorOpportunityCard（板块卡片内嵌）与
 * ReportPanel（持仓卡片内一句话摘要）两处使用，避免各写一套措辞。
 */
export function divergenceBacktestLines(backtest: DivergenceBacktestLike): string[] {
  if (!backtest?.by_rule) {
    return [];
  }
  const sampleDays = backtest.sample_days;
  return Object.values(backtest.by_rule)
    .filter((rule) => rule.significant && rule.hit_rate_percent != null)
    .map((rule) => {
      const ruleLabel = DIVERGENCE_RULE_LABELS[rule.rule_id] ?? rule.label;
      const window = sampleDays ? `过去 ${sampleDays} 个交易日` : "历史窗口内";
      const edge = rule.edge_percent != null ? `，超基准 ${rule.edge_percent}pp` : "";
      return `${window}「${ruleLabel}」出现 ${rule.trigger_count} 次，次日命中概率 ${rule.hit_rate_percent}%${edge}`;
    });
}

export function translateEvidenceText(text: string): string {
  return text
    .replace(/\bopportunity\s+absent\b/gi, "当前不构成机会")
    .replace(/\bopportunity\s+present\b/gi, "当前构成机会")
    .replace(/机会\s*absent\b/gi, "当前不构成机会")
    .replace(/机会\s*present\b/gi, "当前构成机会")
    .replace(/\bdaily_return(?:_percent)?\s*(?:数据)?\s*(?:is\s+)?pending\b/gi, "当日涨跌待确认")
    .replace(/\bmomentum(?=分位|因子|\b)/gi, "动量")
    .replace(/nav_trend\.distance_from_high_percent\s*(?:=|为|约)?\s*([-+]?\d+(?:\.\d+)?)%?/gi, "距离近期高点约 $1%")
    .replace(/max_drawdown_1y_percent\s*(?:=|为|约)?\s*([-+]?\d+(?:\.\d+)?)%?/gi, (_match, value: string) => `近1年最大回撤约 ${formatAbsPercent(value)}%`)
    .replace(/estimated_daily_return_percent\s*(?:=|为|约)?\s*([-+]?\d+(?:\.\d+)?)%?/gi, "今日涨跌约 $1%")
    .replace(/distance_from_high_percent\s*(?:=|为|约)?\s*([-+]?\d+(?:\.\d+)?)%?/gi, "距离近期高点约 $1%")
    .replace(/heat_score\s*(?:=|为|约)?\s*([-+]?\d+(?:\.\d+)?)/gi, "板块热度分 $1")
    .replace(/confidence\s*(?:=|为)?\s*([高中低])/gi, "置信度$1")
    .replace(/track=momentum/gi, "顺势观察")
    .replace(/track=setup/gi, "蓄势观察")
    .replace(/pattern=([a-z_]+)/gi, (_match, value: string) => patternLabel(value))
    .replace(/fund_quality_score\s*(?:=|为)?\s*([-+]?\d+(?:\.\d+)?)/gi, "基金质量分 $1")
    .replace(/sector_fit_score\s*(?:=|为)?\s*([-+]?\d+(?:\.\d+)?)/gi, "板块匹配分 $1")
    .replace(/sector_opportunities\s*得分/gi, "系统方向得分")
    .replace(/sector_opportunities/gi, "系统筛出的主方向")
    .replace(/quality_reasons/gi, "加分原因")
    .replace(/quality_penalties\s*提示/gi, "系统校验提示")
    .replace(/quality_penalties/gi, "系统校验提示")
    .replace(/sector_estimate/gi, "板块估算")
    .replace(/nav_trend/gi, "净值走势")
    .replace(/return_3m_percent/gi, "近3月收益")
    .replace(/return_6m_percent/gi, "近6月收益")
    .replace(/return_1y_percent/gi, "近1年收益");
}
