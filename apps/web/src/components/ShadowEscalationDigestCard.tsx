"use client";

import { useEffect, useState } from "react";
import { History, Loader2 } from "lucide-react";
import { fetchShadowEscalationDigest, type ShadowEscalationDigest } from "@/lib/api";
import { InlineNotice } from "@/components/InlineNotice";
import { StatusPill } from "@/components/StatusPill";

/**
 * M6.3/M5：灰度期间的"本周复盘摘要"卡片。设计文档要求"仅
 * `DECISION_ESCALATION_MODE=shadow` 时展示"——组件自行请求一次摘要接口，接口
 * 响应里带 `escalation_mode` 字段，若为 `enforced` 则该卡片直接不渲染任何内容
 * （灰度已结束，不再需要这份"若启用会怎样"的参考）。
 */
export function ShadowEscalationDigestCard() {
  const [data, setData] = useState<ShadowEscalationDigest | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retrySequence, setRetrySequence] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    void fetchShadowEscalationDigest(7)
      .then((result) => {
        if (!cancelled) {
          setData(result);
        }
      })
      .catch((loadError) => {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "灰度复盘加载失败");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [retrySequence]);

  if (loading && !data) {
    return (
      <section className="glass-panel rounded-[24px] p-5">
        <h3 className="text-lg font-black text-slate-950">灰度复盘摘要</h3>
        <div className="mt-2 flex items-center gap-2 text-sm text-slate-500">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          正在汇总近 7 天的灰度触发记录…
        </div>
      </section>
    );
  }

  if (error && !data) {
    return (
      <section className="glass-panel rounded-[24px] p-5">
        <h3 className="text-lg font-black text-slate-950">灰度复盘摘要</h3>
        <InlineNotice
          tone="error"
          message={`灰度复盘加载失败：${error}`}
          action={{
            label: "重试",
            onClick: () => setRetrySequence((current) => current + 1),
          }}
          className="mt-3"
        />
      </section>
    );
  }

  // enforced 模式下灰度已结束，不展示该卡片（不占用诊断区版面）。
  if (!data || data.escalation_mode !== "shadow") {
    return null;
  }

  if (!data.available) {
    return (
      <section className="glass-panel rounded-[24px] p-5">
        <h3 className="text-lg font-black text-slate-950">灰度复盘摘要</h3>
        <InlineNotice
          tone="info"
          message={data.summary ?? "当前暂无可汇总的灰度复盘记录。"}
          className="mt-3"
        />
      </section>
    );
  }

  const bySector = Object.entries(data.by_sector ?? {});
  const byAction = Object.entries(data.by_would_be_action ?? {});
  const outcomes = data.outcomes;

  return (
    <section
      className="glass-panel rounded-[24px] p-5"
      data-testid="shadow-escalation-digest"
      aria-busy={loading}
    >
      <div className="flex items-start gap-3">
        <div className="inline-flex h-10 w-10 items-center justify-center rounded-2xl bg-[var(--brand)] text-white">
          <History size={20} />
        </div>
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-lg font-black text-slate-950">灰度复盘摘要</h3>
            <StatusPill tone="amber">shadow 观察期</StatusPill>
          </div>
          <p className="mt-1 text-xs text-slate-600">
            近 {data.lookback_days ?? 7} 天 · 日报 {data.report_count ?? 0} 份 / 荐基{" "}
            {data.discovery_report_count ?? 0} 份
          </p>
        </div>
      </div>

      {error ? (
        <InlineNotice
          tone="warning"
          message={`灰度复盘更新失败，继续显示上次成功获取的结果：${error}`}
          action={{
            label: "重试",
            onClick: () => setRetrySequence((current) => current + 1),
          }}
          className="mt-3"
        />
      ) : loading ? (
        <InlineNotice
          tone="info"
          message="正在更新灰度复盘，当前继续显示已有结果。"
          className="mt-3"
        />
      ) : null}

      <p className="mt-3 text-sm leading-6 text-slate-700">{data.summary}</p>

      {data.trigger_count > 0 ? (
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          {bySector.length ? (
            <div className="rounded-2xl border border-slate-100 bg-white/80 p-3">
              <div className="text-xs font-bold text-slate-500">涉及板块</div>
              <ul className="mt-1.5 space-y-1 text-sm text-slate-700">
                {bySector.slice(0, 5).map(([label, count]) => (
                  <li key={label} className="flex items-center justify-between gap-2">
                    <span className="break-words">{label}</span>
                    <span className="font-semibold text-slate-900">{count} 次</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {byAction.length ? (
            <div className="rounded-2xl border border-slate-100 bg-white/80 p-3">
              <div className="text-xs font-bold text-slate-500">建议升级动作分布</div>
              <ul className="mt-1.5 space-y-1 text-sm text-slate-700">
                {byAction.slice(0, 5).map(([label, count]) => (
                  <li key={label} className="flex items-center justify-between gap-2">
                    <span className="break-words">{label}</span>
                    <span className="font-semibold text-slate-900">{count} 次</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}

      {outcomes && outcomes.verified_count > 0 ? (
        <div className="mt-3 rounded-2xl border border-blue-100 bg-blue-50/60 px-3 py-2.5">
          <div className="text-xs font-bold text-blue-800">
            次日走势对照（当日层面近似，非严格复盘）
          </div>
          <p className="mt-1 text-sm text-blue-900">
            {outcomes.aligned_count}/{outcomes.verified_count} 次触发当日走势偏弱，与升级判断方向一致
          </p>
        </div>
      ) : null}

      <p className="mt-3 text-xs leading-5 text-slate-500">
        观察约 1 个月（约 20 个交易日）后，可结合这份摘要判断是否切换到 enforced
        （切换需修改 FUND_AI_DECISION_ESCALATION_MODE 配置并重启服务）；本卡片仅供参考，不构成投资建议。
      </p>
    </section>
  );
}
