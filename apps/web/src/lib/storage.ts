import type { AnalysisPromptConfig, DiscoveryPromptConfig, DiscoverySectorHeat, InvestorProfile } from "@/lib/api";

const PROFILE_KEY = "fundpilot-investor-profile";
const ANALYSIS_PROMPT_KEY = "fundpilot-analysis-prompt";
const DISCOVERY_PROMPT_KEY = "fundpilot-discovery-prompt";
const DISCOVERY_SECTORS_KEY = "fundpilot-discovery-sectors";
const MODE_KEY = "fundpilot-analysis-mode";
const CHAT_MODE_KEY = "fundpilot-report-chat-mode";

const USER_SCOPED_STORAGE_VERSION = 1;

type UserStorageId = number | null | undefined;

type UserScopedStorageBucket<T> = {
  version: typeof USER_SCOPED_STORAGE_VERSION;
  byUserId: Record<string, T>;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Read account-owned data only from the versioned bucket format. The previous
 * single-value format intentionally is not migrated because it has no owner and
 * therefore cannot be exposed safely after an account switch.
 */
function loadUserScopedValue<T>(key: string, userId: UserStorageId): T | null {
  if (typeof window === "undefined" || userId == null) {
    return null;
  }
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as unknown;
    if (
      !isRecord(parsed) ||
      parsed.version !== USER_SCOPED_STORAGE_VERSION ||
      !isRecord(parsed.byUserId)
    ) {
      return null;
    }
    return (parsed.byUserId[String(userId)] as T | undefined) ?? null;
  } catch {
    return null;
  }
}

function saveUserScopedValue<T>(key: string, userId: UserStorageId, value: T): void {
  if (typeof window === "undefined" || userId == null) {
    return;
  }
  try {
    const raw = window.localStorage.getItem(key);
    let parsed: unknown = null;
    if (raw) {
      try {
        parsed = JSON.parse(raw) as unknown;
      } catch {
        // A malformed or legacy ownerless value is safe to replace with a bucket.
      }
    }
    const existingByUserId =
      isRecord(parsed) &&
      parsed.version === USER_SCOPED_STORAGE_VERSION &&
      isRecord(parsed.byUserId)
        ? parsed.byUserId
        : {};
    const bucket: UserScopedStorageBucket<T> = {
      version: USER_SCOPED_STORAGE_VERSION,
      byUserId: {
        ...(existingByUserId as Record<string, T>),
        [String(userId)]: value,
      },
    };
    window.localStorage.setItem(key, JSON.stringify(bucket));
  } catch {
    // Ignore unavailable or malformed localStorage; remote state remains authoritative.
  }
}

export type AnalysisMode = "fast" | "deep";
export type ReportChatMode = AnalysisMode;

const DEFAULT_EXPECTED_INVESTMENT_AMOUNT = 30_000;

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

export function loadInvestorProfile(
  userId: UserStorageId,
  fallback: InvestorProfile,
): InvestorProfile {
  const stored = loadUserScopedValue<Partial<InvestorProfile>>(PROFILE_KEY, userId);
  return normalizeInvestorProfile(stored, fallback);
}

export function saveInvestorProfile(userId: UserStorageId, profile: InvestorProfile) {
  saveUserScopedValue(PROFILE_KEY, userId, profile);
}

export function loadAnalysisPrompt(
  userId: UserStorageId,
  fallback: Pick<AnalysisPromptConfig, "role_prompt" | "default_role_prompt">,
): AnalysisPromptConfig {
  const parsed = loadUserScopedValue<Partial<AnalysisPromptConfig>>(
    ANALYSIS_PROMPT_KEY,
    userId,
  );
  if (parsed) {
    const rolePrompt = parsed.role_prompt?.trim() || fallback.role_prompt;
    const defaultRolePrompt =
      parsed.default_role_prompt?.trim() || fallback.default_role_prompt;
    return {
      role_prompt: rolePrompt,
      is_custom: Boolean(parsed.is_custom),
      default_role_prompt: defaultRolePrompt,
    };
  }
  return {
    role_prompt: fallback.role_prompt,
    is_custom: false,
    default_role_prompt: fallback.default_role_prompt,
  };
}

export function saveAnalysisPrompt(userId: UserStorageId, config: AnalysisPromptConfig) {
  saveUserScopedValue(ANALYSIS_PROMPT_KEY, userId, config);
}

export function loadDiscoveryPrompt(
  userId: UserStorageId,
  fallback: Pick<DiscoveryPromptConfig, "role_prompt" | "default_role_prompt">,
): DiscoveryPromptConfig {
  const parsed = loadUserScopedValue<Partial<DiscoveryPromptConfig>>(
    DISCOVERY_PROMPT_KEY,
    userId,
  );
  if (parsed) {
    const rolePrompt = parsed.role_prompt?.trim() || fallback.role_prompt;
    const defaultRolePrompt =
      parsed.default_role_prompt?.trim() || fallback.default_role_prompt;
    return {
      role_prompt: rolePrompt,
      is_custom: Boolean(parsed.is_custom),
      default_role_prompt: defaultRolePrompt,
    };
  }
  return {
    role_prompt: fallback.role_prompt,
    is_custom: false,
    default_role_prompt: fallback.default_role_prompt,
  };
}

export function saveDiscoveryPrompt(userId: UserStorageId, config: DiscoveryPromptConfig) {
  saveUserScopedValue(DISCOVERY_PROMPT_KEY, userId, config);
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
  | "holdings"
  | "report"
  | "history"
  | "dashboard"
  | "market"
  | "discovery";

const DASHBOARD_TAB_IDS: DashboardTabId[] = [
  "holdings",
  "report",
  "history",
  "dashboard",
  "market",
  "discovery",
];

export function loadDashboardTab(fallback: DashboardTabId = "holdings"): DashboardTabId {
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
