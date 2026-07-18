"use client";

import { useEffect, useState } from "react";

import { InlineNotice } from "@/components/InlineNotice";
import {
  fetchEvidenceMaturityStatus,
  type EvidenceMaturityAlert,
  type EvidenceMaturityStatus,
} from "@/lib/api";


const OVERALL_LABEL: Record<string, string> = {
  healthy: "采集与证据链正常",
  collecting: "证据持续积累中",
  attention: "有项目需要检查",
  degraded: "采集链异常",
};

const STATUS_CLASS: Record<string, string> = {
  healthy: "border-emerald-200 bg-emerald-50 text-emerald-700",
  active: "border-emerald-200 bg-emerald-50 text-emerald-700",
  shadow_ready: "border-sky-200 bg-sky-50 text-sky-700",
  manual_review_ready: "border-sky-200 bg-sky-50 text-sky-700",
  collecting: "border-amber-200 bg-amber-50 text-amber-700",
  shadow: "border-amber-200 bg-amber-50 text-amber-700",
  attention: "border-amber-200 bg-amber-50 text-amber-700",
  stale: "border-rose-200 bg-rose-50 text-rose-700",
  unavailable: "border-slate-200 bg-slate-100 text-slate-600",
  degraded: "border-rose-200 bg-rose-50 text-rose-700",
};

const STATUS_LABEL: Record<string, string> = {
  healthy: "正常",
  active: "生效中",
  shadow_ready: "可做影子比较",
  manual_review_ready: "可进入人工复核",
  collecting: "积累中",
  shadow: "仅影子评估",
  attention: "需检查",
  stale: "已过期",
  unavailable: "尚不可用",
  degraded: "异常",
};


function StatusTag({ status }: { status: string }) {
  return (
    <span
      className={`inline-flex rounded-full border px-2 py-0.5 text-[11px] font-bold ${
        STATUS_CLASS[status] ?? STATUS_CLASS.unavailable
      }`}
    >
      {STATUS_LABEL[status] ?? status}
    </span>
  );
}


function EvidenceValue({
  value,
  suffix = "",
}: {
  value: number | null | undefined;
  suffix?: string;
}) {
  if (value == null) {
    return <span className="text-slate-500">尚无证据</span>;
  }
  return (
    <span>
      {value.toLocaleString("zh-CN")}
      {suffix}
    </span>
  );
}


function ProgressLine({
  label,
  value,
  target,
  percent,
}: {
  label: string;
  value: number | null | undefined;
  target: number | null | undefined;
  percent: number | null | undefined;
}) {
  const width = percent == null ? 0 : Math.max(0, Math.min(100, percent));
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-3 text-xs">
        <span className="text-slate-600">{label}</span>
        <strong className="text-slate-800">
          {value == null || target == null ? (
            <span className="font-medium text-slate-500">尚无证据</span>
          ) : (
            `${value} / ${target}`
          )}
        </strong>
      </div>
      <div
        className="h-1.5 overflow-hidden rounded-full bg-slate-200"
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent == null ? undefined : width}
        aria-valuetext={percent == null ? "尚无证据" : `${width.toFixed(0)}%`}
      >
        <div
          className="h-full rounded-full bg-[var(--brand)] transition-[width]"
          style={{ width: `${width}%` }}
        />
      </div>
    </div>
  );
}


function AlertRow({ alert }: { alert: EvidenceMaturityAlert }) {
  const styles = {
    critical: "border-rose-200 bg-rose-50/80 text-rose-900",
    warning: "border-amber-200 bg-amber-50/80 text-amber-900",
    info: "border-sky-200 bg-sky-50/70 text-sky-900",
  } as const;
  return (
    <li
      className={`rounded-xl border p-3 text-xs leading-5 ${
        styles[alert.severity as keyof typeof styles] ?? styles.info
      }`}
    >
      <strong className="block text-sm">{alert.title}</strong>
      <span className="block opacity-90">{alert.message}</span>
      <span className="mt-1 block font-medium">下一步：{alert.action}</span>
    </li>
  );
}


function MaturityContent({ data }: { data: EvidenceMaturityStatus }) {
  const universe = data.universe;
  const factor = data.factor_ic;
  const score = data.decision_score_shadow;
  const quality = data.decision_quality;
  const heartbeatJobs = data.worker.jobs.filter((job) => job.persistent);
  const pitLabel = factor.nav_revision_pit
    ? "完整 NAV-PIT"
    : factor.cohort_mode === "point_in_time" ||
        factor.point_in_time_scope === "membership_only"
      ? "成员 PIT"
      : "当前存续样本（PIT 积累中）";

  return (
    <div className="space-y-4" data-testid="evidence-maturity-content">
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-[var(--line)] bg-white p-3">
        <div>
          <p className="text-sm font-bold text-slate-900">
            {OVERALL_LABEL[data.overall_status] ?? data.overall_status}
          </p>
          <p className="mt-1 text-xs leading-5 text-slate-500">
            采集健康只证明任务在运行，不代表模型已经通过统计与经济门槛。
          </p>
        </div>
        <StatusTag status={data.overall_status} />
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <article className="rounded-xl border border-[var(--line)] bg-white p-3">
          <div className="flex items-center justify-between gap-2">
            <h4 className="text-sm font-bold text-slate-900">后台采集 Worker</h4>
            <StatusTag status={data.worker.status} />
          </div>
          <dl className="mt-3 grid grid-cols-2 gap-2 text-xs">
            <div>
              <dt className="text-slate-500">心跳延迟</dt>
              <dd className="mt-0.5 font-bold text-slate-800">
                <EvidenceValue value={data.worker.heartbeat_age_seconds} suffix=" 秒" />
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">常驻任务</dt>
              <dd className="mt-0.5 font-bold text-slate-800">
                {heartbeatJobs.length ? `${heartbeatJobs.length} 项存活` : "尚无证据"}
              </dd>
            </div>
          </dl>
          {heartbeatJobs.length ? (
            <p className="mt-2 break-words text-[11px] leading-5 text-slate-500">
              {heartbeatJobs.map((job) => job.name).join(" · ")}
            </p>
          ) : null}
        </article>

        <article className="rounded-xl border border-[var(--line)] bg-white p-3">
          <div className="flex items-center justify-between gap-2">
            <h4 className="text-sm font-bold text-slate-900">PIT 基金池与 Factor IC</h4>
            <StatusTag status={factor.status} />
          </div>
          <dl className="mt-3 grid grid-cols-2 gap-2 text-xs">
            <div>
              <dt className="text-slate-500">真实成员快照</dt>
              <dd className="mt-0.5 font-bold text-slate-800">
                <EvidenceValue value={universe.snapshot_count} suffix=" 份" />
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">最近样本基金</dt>
              <dd className="mt-0.5 font-bold text-slate-800">
                <EvidenceValue value={universe.latest_sampled_fund_count} suffix=" 只" />
              </dd>
            </div>
          </dl>
          <div className="mt-3 space-y-3">
            <ProgressLine
              label="有效 PIT 锚点"
              value={universe.effective_anchor_count}
              target={universe.minimum_effective_anchor_count}
              percent={universe.anchor_progress_percent}
            />
            <ProgressLine
              label="20 日经济样本期"
              value={factor.mature_period_count_20d}
              target={factor.economic_minimum_period_count}
              percent={factor.economic_progress_percent_20d}
            />
          </div>
          <p className="mt-3 text-[11px] leading-5 text-slate-500">
            当前口径：{pitLabel}。20 日门槛理论最短约 17.5 个月，但到期不会自动通过。
          </p>
        </article>

        <article className="rounded-xl border border-[var(--line)] bg-white p-3">
          <div className="flex items-center justify-between gap-2">
            <h4 className="text-sm font-bold text-slate-900">DecisionScore 影子模型</h4>
            <StatusTag status={score.status} />
          </div>
          <dl className="mt-3 grid grid-cols-2 gap-2 text-xs">
            <div>
              <dt className="text-slate-500">有效影子制品</dt>
              <dd className="mt-0.5 font-bold text-slate-800">
                <EvidenceValue value={score.valid_artifact_count} suffix=" 份" />
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">候选评分覆盖</dt>
              <dd className="mt-0.5 font-bold text-slate-800">
                <EvidenceValue value={score.scored_coverage_percent} suffix="%" />
              </dd>
            </div>
          </dl>
          <p className="mt-3 text-[11px] leading-5 text-slate-500">
            只比较新旧排序差异；不参与线上推荐，也不会自动调权。
          </p>
        </article>

        <article className="rounded-xl border border-[var(--line)] bg-white p-3">
          <div className="flex items-center justify-between gap-2">
            <h4 className="text-sm font-bold text-slate-900">决策质量前向证据</h4>
            <StatusTag status={quality.status} />
          </div>
          <div className="mt-3">
            <ProgressLine
              label="成熟决策日（人工复核门槛）"
              value={quality.mature_decision_day_count}
              target={quality.minimum_manual_review_mature_decision_days}
              percent={quality.maturity_progress_percent}
            />
          </div>
          <dl className="mt-3 grid grid-cols-2 gap-2 text-xs">
            <div>
              <dt className="text-slate-500">正式标签覆盖</dt>
              <dd className="mt-0.5 font-bold text-slate-800">
                <EvidenceValue value={quality.formal_label_coverage_percent} suffix="%" />
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">最低标签覆盖</dt>
              <dd className="mt-0.5 font-bold text-slate-800">
                {quality.minimum_manual_review_label_coverage_percent}%
              </dd>
            </div>
          </dl>
          <p className="mt-3 text-[11px] leading-5 text-slate-500">
            达到门槛也只进入人工复核，automatic promotion 始终关闭。
          </p>
        </article>
      </div>

      {data.alerts.length ? (
        <div>
          <h4 className="mb-2 text-sm font-bold text-slate-900">采集与证据提示</h4>
          <ul className="grid gap-2">
            {data.alerts.map((alert) => (
              <AlertRow key={alert.code} alert={alert} />
            ))}
          </ul>
        </div>
      ) : null}

      <ul className="space-y-1 text-[11px] leading-5 text-slate-500">
        {data.notices.map((notice) => (
          <li key={notice}>· {notice}</li>
        ))}
      </ul>
    </div>
  );
}


export function EvidenceMaturityPanel({ enabled }: { enabled: boolean }) {
  const [data, setData] = useState<EvidenceMaturityStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retrySequence, setRetrySequence] = useState(0);

  useEffect(() => {
    if (!enabled || data) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchEvidenceMaturityStatus()
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((reason) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : "加载证据成熟度失败");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [enabled, data, retrySequence]);

  if (!enabled) return null;
  if (loading) return <InlineNotice tone="info" message="正在读取采集链与证据成熟度…" />;
  if (error) {
    return (
      <InlineNotice
        tone="error"
        message={`证据成熟度加载失败：${error}`}
        action={{
          label: "重试",
          onClick: () => setRetrySequence((value) => value + 1),
        }}
      />
    );
  }
  if (!data) return <InlineNotice tone="info" message="尚无证据成熟度快照。" />;
  return <MaturityContent data={data} />;
}
