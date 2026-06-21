import type { AnalysisPromptConfig, DiscoveryPromptConfig, DiscoverySectorHeat, InvestorProfile } from "@/lib/api";

const PROFILE_KEY = "fundpilot-investor-profile";
const ANALYSIS_PROMPT_KEY = "fundpilot-analysis-prompt";
const DISCOVERY_PROMPT_KEY = "fundpilot-discovery-prompt";
const DISCOVERY_SECTORS_KEY = "fundpilot-discovery-sectors";
const MODE_KEY = "fundpilot-analysis-mode";
const CHAT_MODE_KEY = "fundpilot-report-chat-mode";

export type AnalysisMode = "fast" | "deep";
export type ReportChatMode = AnalysisMode;

export const DEFAULT_EXPECTED_INVESTMENT_AMOUNT = 30_000;

export function normalizeInvestorProfile(
  raw: Partial<InvestorProfile> | null | undefined,
  fallback: InvestorProfile,
): InvestorProfile {
  const source = raw ?? {};
  const expected =
    source.expected_investment_amount != null && source.expected_investment_amount > 0
      ? Number(source.expected_investment_amount)
      : fallback.expected_investment_amount ?? DEFAULT_EXPECTED_INVESTMENT_AMOUNT;

  return {
    style: source.style?.trim() || fallback.style,
    horizon: source.horizon?.trim() || fallback.horizon,
    max_drawdown_percent: Number(source.max_drawdown_percent ?? fallback.max_drawdown_percent),
    concentration_limit_percent: Number(
      source.concentration_limit_percent ?? fallback.concentration_limit_percent,
    ),
    expected_investment_amount: expected,
    prefer_dca: source.prefer_dca ?? fallback.prefer_dca,
    avoid_chasing: source.avoid_chasing ?? fallback.avoid_chasing,
    decision_style:
      source.decision_style === "tactical" ||
      source.decision_style === "conservative" ||
      source.decision_style === "aggressive"
        ? source.decision_style
        : fallback.decision_style ?? "conservative",
    investment_preset:
      source.investment_preset === "aggressive_swing" ||
      source.investment_preset === "conservative_hold"
        ? source.investment_preset
        : fallback.investment_preset ?? "conservative_hold",
    round_trip_fee_percent: Number(
      source.round_trip_fee_percent ?? fallback.round_trip_fee_percent ?? 1.5,
    ),
    min_net_profit_percent: Number(
      source.min_net_profit_percent ?? fallback.min_net_profit_percent ?? 1.0,
    ),
    hold_days_target: Number(source.hold_days_target ?? fallback.hold_days_target ?? 7),
    swing_alerts_enabled: source.swing_alerts_enabled ?? fallback.swing_alerts_enabled ?? false,
    swing_monitor_scope:
      source.swing_monitor_scope === "holdings" ||
      source.swing_monitor_scope === "full_market" ||
      source.swing_monitor_scope === "both"
        ? source.swing_monitor_scope
        : fallback.swing_monitor_scope ?? "both",
  };
}

export function loadInvestorProfile(fallback: InvestorProfile): InvestorProfile {
  if (typeof window === "undefined") {
    return normalizeInvestorProfile(fallback, fallback);
  }
  try {
    const raw = window.localStorage.getItem(PROFILE_KEY);
    if (!raw) {
      return normalizeInvestorProfile(fallback, fallback);
    }
    return normalizeInvestorProfile(JSON.parse(raw) as Partial<InvestorProfile>, fallback);
  } catch {
    return normalizeInvestorProfile(fallback, fallback);
  }
}

export function saveInvestorProfile(profile: InvestorProfile) {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(PROFILE_KEY, JSON.stringify(profile));
}

export function loadAnalysisPrompt(
  fallback: Pick<AnalysisPromptConfig, "role_prompt" | "default_role_prompt">,
): AnalysisPromptConfig {
  if (typeof window === "undefined") {
    return {
      role_prompt: fallback.role_prompt,
      is_custom: false,
      default_role_prompt: fallback.default_role_prompt,
    };
  }
  try {
    const raw = window.localStorage.getItem(ANALYSIS_PROMPT_KEY);
    if (!raw) {
      return {
        role_prompt: fallback.role_prompt,
        is_custom: false,
        default_role_prompt: fallback.default_role_prompt,
      };
    }
    const parsed = JSON.parse(raw) as Partial<AnalysisPromptConfig>;
    const rolePrompt = parsed.role_prompt?.trim() || fallback.role_prompt;
    const defaultRolePrompt =
      parsed.default_role_prompt?.trim() || fallback.default_role_prompt;
    return {
      role_prompt: rolePrompt,
      is_custom: Boolean(parsed.is_custom),
      default_role_prompt: defaultRolePrompt,
    };
  } catch {
    return {
      role_prompt: fallback.role_prompt,
      is_custom: false,
      default_role_prompt: fallback.default_role_prompt,
    };
  }
}

export function saveAnalysisPrompt(config: AnalysisPromptConfig) {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(ANALYSIS_PROMPT_KEY, JSON.stringify(config));
}

export function loadDiscoveryPrompt(
  fallback: Pick<DiscoveryPromptConfig, "role_prompt" | "default_role_prompt">,
): DiscoveryPromptConfig {
  if (typeof window === "undefined") {
    return {
      role_prompt: fallback.role_prompt,
      is_custom: false,
      default_role_prompt: fallback.default_role_prompt,
    };
  }
  try {
    const raw = window.localStorage.getItem(DISCOVERY_PROMPT_KEY);
    if (!raw) {
      return {
        role_prompt: fallback.role_prompt,
        is_custom: false,
        default_role_prompt: fallback.default_role_prompt,
      };
    }
    const parsed = JSON.parse(raw) as Partial<DiscoveryPromptConfig>;
    const rolePrompt = parsed.role_prompt?.trim() || fallback.role_prompt;
    const defaultRolePrompt =
      parsed.default_role_prompt?.trim() || fallback.default_role_prompt;
    return {
      role_prompt: rolePrompt,
      is_custom: Boolean(parsed.is_custom),
      default_role_prompt: defaultRolePrompt,
    };
  } catch {
    return {
      role_prompt: fallback.role_prompt,
      is_custom: false,
      default_role_prompt: fallback.default_role_prompt,
    };
  }
}

export function saveDiscoveryPrompt(config: DiscoveryPromptConfig) {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(DISCOVERY_PROMPT_KEY, JSON.stringify(config));
}

export function loadAnalysisMode(fallback: AnalysisMode = "deep"): AnalysisMode {
  if (typeof window === "undefined") {
    return fallback;
  }
  const raw = window.localStorage.getItem(MODE_KEY);
  return raw === "fast" || raw === "deep" ? raw : fallback;
}

export function saveAnalysisMode(mode: AnalysisMode) {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(MODE_KEY, mode);
}

export function loadReportChatMode(fallback: ReportChatMode = "fast"): ReportChatMode {
  if (typeof window === "undefined") {
    return fallback;
  }
  const raw = window.localStorage.getItem(CHAT_MODE_KEY);
  return raw === "fast" || raw === "deep" ? raw : fallback;
}

export function saveReportChatMode(mode: ReportChatMode) {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(CHAT_MODE_KEY, mode);
}

const AMOUNTS_HIDDEN_KEY = "fundpilot-amounts-hidden";

export function loadAmountsHidden(fallback = false): boolean {
  if (typeof window === "undefined") {
    return fallback;
  }
  return window.localStorage.getItem(AMOUNTS_HIDDEN_KEY) === "true";
}

export function saveAmountsHidden(hidden: boolean) {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(AMOUNTS_HIDDEN_KEY, String(hidden));
}

type DiscoverySectorHeatCache = {
  fetchedAt: number;
  sectors: DiscoverySectorHeat[];
};

/** 推荐基金关注方向：本地缓存，进入 Tab 时先展示再后台刷新 */
export function loadDiscoverySectorHeatCache(
  maxAgeMs = 30 * 60 * 1000,
): DiscoverySectorHeat[] | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    const raw = window.localStorage.getItem(DISCOVERY_SECTORS_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as DiscoverySectorHeatCache;
    if (!Array.isArray(parsed.sectors) || !parsed.sectors.length) {
      return null;
    }
    if (Date.now() - parsed.fetchedAt > maxAgeMs) {
      return null;
    }
    return parsed.sectors;
  } catch {
    return null;
  }
}

export function saveDiscoverySectorHeatCache(sectors: DiscoverySectorHeat[]) {
  if (typeof window === "undefined" || !sectors.length) {
    return;
  }
  const payload: DiscoverySectorHeatCache = {
    fetchedAt: Date.now(),
    sectors,
  };
  window.localStorage.setItem(DISCOVERY_SECTORS_KEY, JSON.stringify(payload));
}

const DASHBOARD_TAB_KEY = "fundpilot-dashboard-tab";

export type DashboardTabId =
  | "today"
  | "holdings"
  | "report"
  | "history"
  | "dashboard"
  | "market"
  | "discovery";

const DASHBOARD_TAB_IDS: DashboardTabId[] = [
  "today",
  "holdings",
  "report",
  "history",
  "dashboard",
  "market",
  "discovery",
];

export function loadDashboardTab(fallback: DashboardTabId = "today"): DashboardTabId {
  if (typeof window === "undefined") {
    return fallback;
  }
  const stored = window.sessionStorage.getItem(DASHBOARD_TAB_KEY);
  if (stored && DASHBOARD_TAB_IDS.includes(stored as DashboardTabId)) {
    return stored as DashboardTabId;
  }
  return fallback;
}

export function saveDashboardTab(tab: DashboardTabId): void {
  if (typeof window === "undefined") {
    return;
  }
  window.sessionStorage.setItem(DASHBOARD_TAB_KEY, tab);
}
