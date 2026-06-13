import type { AnalysisPromptConfig, InvestorProfile } from "@/lib/api";

const PROFILE_KEY = "fundpilot-investor-profile";
const ANALYSIS_PROMPT_KEY = "fundpilot-analysis-prompt";
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
      source.decision_style === "tactical" || source.decision_style === "conservative"
        ? source.decision_style
        : fallback.decision_style ?? "conservative",
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
