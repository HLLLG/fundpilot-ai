import type { InvestorProfile } from "@/lib/api";

const PROFILE_KEY = "fundpilot-investor-profile";
const MODE_KEY = "fundpilot-analysis-mode";
const CHAT_MODE_KEY = "fundpilot-report-chat-mode";

export type AnalysisMode = "fast" | "deep";
export type ReportChatMode = AnalysisMode;

export function loadInvestorProfile(fallback: InvestorProfile): InvestorProfile {
  if (typeof window === "undefined") {
    return fallback;
  }
  try {
    const raw = window.localStorage.getItem(PROFILE_KEY);
    if (!raw) {
      return fallback;
    }
    return { ...fallback, ...JSON.parse(raw) } as InvestorProfile;
  } catch {
    return fallback;
  }
}

export function saveInvestorProfile(profile: InvestorProfile) {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(PROFILE_KEY, JSON.stringify(profile));
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
