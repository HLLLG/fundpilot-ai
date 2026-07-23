"use client";

import { useReportWebVitals } from "next/web-vitals";
import { getAccessToken } from "@/lib/auth";
import { API_BASE, apiFetch } from "@/lib/api/core";

type VitalName = "CLS" | "FCP" | "INP" | "LCP" | "TTFB";
type ReportCallback = Parameters<typeof useReportWebVitals>[0];
const SUPPORTED_VITALS = new Set<VitalName>([
  "CLS",
  "FCP",
  "INP",
  "LCP",
  "TTFB",
]);

function isSupportedVital(name: string): name is VitalName {
  return SUPPORTED_VITALS.has(name as VitalName);
}

const reportWebVital: ReportCallback = (metric) => {
  if (
    typeof window === "undefined" ||
    !getAccessToken() ||
    !isSupportedVital(metric.name) ||
    !Number.isFinite(metric.value) ||
    metric.value < 0
  ) {
    return;
  }
  void apiFetch(`${API_BASE}/api/telemetry/web-vitals`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: metric.name,
      value: metric.value,
      path: window.location.pathname || "/",
      rating: metric.rating ?? "unknown",
    }),
    keepalive: true,
    timeoutMs: 5000,
  }).catch(() => undefined);
};

export function WebVitalsReporter() {
  useReportWebVitals(reportWebVital);
  return null;
}
