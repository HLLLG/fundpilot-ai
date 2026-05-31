import type { InvestorProfile } from "@/lib/api";

const PROFILE_KEY = "fundpilot-investor-profile";
const MODE_KEY = "fundpilot-analysis-mode";
const AUTO_OCR_KEY = "fundpilot-auto-analyze-on-ocr";
const ASYNC_KEY = "fundpilot-use-async-analyze";
const INBOX_SEEN_KEY = "fundpilot-inbox-seen-ids";

export type AnalysisMode = "fast" | "deep";

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

export function loadAutoAnalyzeOnOcr(): boolean {
  if (typeof window === "undefined") {
    return false;
  }
  return window.localStorage.getItem(AUTO_OCR_KEY) === "true";
}

export function saveAutoAnalyzeOnOcr(enabled: boolean) {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(AUTO_OCR_KEY, enabled ? "true" : "false");
}

export function loadUseAsyncAnalyze(): boolean {
  if (typeof window === "undefined") {
    return true;
  }
  const raw = window.localStorage.getItem(ASYNC_KEY);
  return raw !== "false";
}

export function saveUseAsyncAnalyze(enabled: boolean) {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(ASYNC_KEY, enabled ? "true" : "false");
}

export function loadInboxSeenIds(): Set<string> {
  if (typeof window === "undefined") {
    return new Set();
  }
  try {
    const raw = window.localStorage.getItem(INBOX_SEEN_KEY);
    if (!raw) {
      return new Set();
    }
    return new Set(JSON.parse(raw) as string[]);
  } catch {
    return new Set();
  }
}

export function saveInboxSeenIds(ids: Set<string>) {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(INBOX_SEEN_KEY, JSON.stringify([...ids].slice(-200)));
}
