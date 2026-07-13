"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { Holding, InvestorProfile, SwingAlertItem } from "@/lib/api";
import { evaluateSwingAlerts } from "@/lib/api";
import { ensureNotificationPermission, notifyDesktop } from "@/lib/notifications";

/** 盘中波段信号自动评估间隔（与板块 UI 刷新解耦） */
const SWING_ALERT_EVALUATE_INTERVAL_MS = 15 * 60 * 1000;

type UseSwingAlertsOptions = {
  holdings: Holding[];
  profile: InvestorProfile;
  enabled?: boolean;
  /** 评估前先刷新板块，返回最新持仓供评估使用 */
  onBeforeEvaluate?: () => Promise<Holding[] | void>;
  evaluateIntervalMs?: number;
};

function isAlertsActive(profile: InvestorProfile): boolean {
  return Boolean(profile.swing_alerts_enabled || profile.decision_style === "aggressive");
}

export function useSwingAlerts({
  holdings,
  profile,
  enabled = true,
  onBeforeEvaluate,
  evaluateIntervalMs = SWING_ALERT_EVALUATE_INTERVAL_MS,
}: UseSwingAlertsOptions) {
  const [items, setItems] = useState<SwingAlertItem[]>([]);
  const [sessionKind, setSessionKind] = useState<string | null>(null);
  const [isEvaluating, setIsEvaluating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const profileRef = useRef(profile);
  const holdingsRef = useRef(holdings);
  const onBeforeEvaluateRef = useRef(onBeforeEvaluate);

  useEffect(() => {
    profileRef.current = profile;
  }, [profile]);

  useEffect(() => {
    holdingsRef.current = holdings;
  }, [holdings]);

  useEffect(() => {
    onBeforeEvaluateRef.current = onBeforeEvaluate;
  }, [onBeforeEvaluate]);

  const evaluate = useCallback(async () => {
    if (!enabled || !isAlertsActive(profileRef.current)) {
      return;
    }
    const scope = profileRef.current.swing_monitor_scope ?? "both";
    if (scope === "holdings" && !holdingsRef.current.length) {
      return;
    }
    setIsEvaluating(true);
    try {
      const refreshedHoldings = await onBeforeEvaluateRef.current?.();
      const holdingsForEval =
        refreshedHoldings && refreshedHoldings.length > 0
          ? refreshedHoldings
          : holdingsRef.current;
      const result = await evaluateSwingAlerts(holdingsForEval, profileRef.current);
      setSessionKind(result.session_kind);
      setItems(result.items);
      setError(null);
      if (result.new_count > 0) {
        await ensureNotificationPermission();
        for (const item of result.items.filter((row) => row.is_new)) {
          notifyDesktop(item.title, { body: item.message });
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "波段信号评估失败");
    } finally {
      setIsEvaluating(false);
    }
  }, [enabled]);

  useEffect(() => {
    if (!enabled || !isAlertsActive(profile)) {
      return;
    }
    let cancelled = false;
    const tick = async () => {
      if (cancelled) {
        return;
      }
      await evaluate();
    };
    void tick();
    const timer = window.setInterval(() => void tick(), evaluateIntervalMs);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [
    enabled,
    evaluate,
    evaluateIntervalMs,
    profile,
  ]);

  useEffect(() => {
    if (!profile.swing_alerts_enabled && profile.decision_style !== "aggressive") {
      setItems([]);
    }
  }, [profile.swing_alerts_enabled, profile.decision_style]);

  return {
    items,
    sessionKind,
    isEvaluating,
    error,
    evaluate,
    alertsActive: isAlertsActive(profile),
  };
}
