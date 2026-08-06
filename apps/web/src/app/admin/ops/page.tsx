"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  Clock,
  Gauge,
  RefreshCw,
  Search,
  ShieldAlert,
} from "lucide-react";
import { BrandMark } from "@/components/BrandMark";
import { useAuth } from "@/components/AuthProvider";
import { OpsErrorDetailPanel } from "@/components/OpsErrorDetailPanel";
import { OpsLatencyChart } from "@/components/OpsLatencyChart";
import { OpsTrafficChart } from "@/components/OpsTrafficChart";
import {
  fetchOpsErrorGroups,
  fetchOpsOverview,
  type OpsErrorGroup,
  type OpsErrorGroupPage,
  type OpsOverview,
  type OpsSourceFilter,
  type OpsStatusFilter,
} from "@/lib/api/ops";
import { userFacingErrorMessage } from "@/lib/userFacingError";

const WINDOW_OPTIONS = [
  { hours: 1, label: "1 小时" },
  { hours: 6, label: "6 小时" },
  { hours: 24, label: "24 小时" },
  { hours: 72, label: "3 天" },
  { hours: 168, label: "7 天" },
] as const;

const SOURCE_OPTIONS: Array<{ value: OpsSourceFilter; label: string }> = [
  { value: "all", label: "全部来源" },
  { value: "frontend", label: "前端" },
  { value: "backend", label: "后端" },
  { value: "worker", label: "后台任务" },
];

const STATUS_OPTIONS: Array<{ value: OpsStatusFilter; label: string }> = [
  { value: "open", label: "未解决" },
  { value: "resolved", label: "已解决" },
  { value: "ignored", label: "已忽略" },
  { value: "all", label: "全部状态" },
];

const AUTO_REFRESH_MS = 30_000;
const PAGE_SIZE = 20;

const TIME_FORMATTER = new Intl.DateTimeFormat("zh-CN", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});

const SOURCE_LABELS: Record<string, string> = {
  frontend: "前端",
  backend: "后端",
  worker: "后台",
};

const EMPTY_PAGE: OpsErrorGroupPage = {
  contract_version: "",
  items: [],
  page: 1,
  pageSize: PAGE_SIZE,
  total: 0,
  totalPages: 1,
  window: { hours: 24, start: "" },
  filters: { source: "all", status: "open", query: "" },
};

function formatTime(value: string | null | undefined): string {
  if (!value) {
    return "暂无";
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : TIME_FORMATTER.format(date);
}

function formatMs(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return "—";
  }
  if (value >= 1000) {
    return `${(value / 1000).toFixed(value >= 10_000 ? 0 : 2)} s`;
  }
  return `${Math.round(value)} ms`;
}

function formatCount(value: number): string {
  return value.toLocaleString("zh-CN");
}

export default function AdminOpsPage() {
  const { user } = useAuth();
  const [hours, setHours] = useState<number>(24);
  const [overview, setOverview] = useState<OpsOverview | null>(null);
  const [groups, setGroups] = useState<OpsErrorGroupPage>(EMPTY_PAGE);
  const [source, setSource] = useState<OpsSourceFilter>("all");
  const [status, setStatus] = useState<OpsStatusFilter>("open");
  const [queryDraft, setQueryDraft] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);

  const load = useCallback(
    async (options: { silent?: boolean } = {}) => {
      if (options.silent) {
        setRefreshing(true);
      } else {
        setLoading(true);
      }
      setError(null);
      try {
        const [nextOverview, nextGroups] = await Promise.all([
          fetchOpsOverview(hours),
          fetchOpsErrorGroups({
            hours,
            source,
            status,
            query,
            page,
            pageSize: PAGE_SIZE,
          }),
        ]);
        setOverview(nextOverview);
        setGroups(nextGroups);
      } catch (nextError) {
        setError(userFacingErrorMessage(nextError, "无法读取运维数据"));
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [hours, page, query, source, status],
  );

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!autoRefresh) {
      return;
    }
    const timer = window.setInterval(() => {
      // 详情抽屉打开时不自动刷新：列表在读堆栈的过程中重排会打断排查。
      if (!selected) {
        void load({ silent: true });
      }
    }, AUTO_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [autoRefresh, load, selected]);

  const onStatusChange = useCallback((updated: OpsErrorGroup) => {
    setGroups((current) => ({
      ...current,
      items: current.items.map((item) =>
        item.fingerprint === updated.fingerprint ? updated : item,
      ),
    }));
  }, []);

  const totals = overview?.totals;
  const errorStats = overview?.errors;
  const capture = overview?.capture;

  const captureWarnings = useMemo(() => {
    if (!capture) {
      return [] as string[];
    }
    const warnings: string[] = [];
    if (!capture.errorCaptureEnabled) {
      warnings.push("错误采集已关闭，当前不会记录任何新报错。");
    }
    if (!capture.trafficCaptureEnabled) {
      warnings.push("流量采集已关闭，趋势图不会更新。");
    }
    if (!capture.clientIngestEnabled) {
      warnings.push("浏览器上报入口已关闭，前端报错不会上报。");
    }
    if (capture.droppedEventCount > 0) {
      warnings.push(`有 ${capture.droppedEventCount} 条错误因队列积压被丢弃。`);
    }
    if (capture.persistFailureCount > 0) {
      warnings.push(`落库失败 ${capture.persistFailureCount} 次，请检查数据库。`);
    }
    return warnings;
  }, [capture]);

  if (user && user.userRole !== "admin") {
    return (
      <main className="mx-auto max-w-3xl px-4 py-16">
        <p className="rounded-2xl border border-slate-200 bg-white px-4 py-6 text-center text-sm text-slate-600">
          仅管理员可以访问运维监控面板。
        </p>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-3 px-4 py-3">
          <Link
            href="/"
            className="flex min-h-9 items-center gap-1.5 rounded-lg px-2 py-1.5 text-sm font-bold text-slate-600 transition hover:bg-slate-100"
          >
            <ArrowLeft size={16} aria-hidden="true" />
            返回
          </Link>
          <BrandMark />
          <h1 className="text-base font-bold text-slate-900">运维监控</h1>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <label className="flex items-center gap-1.5 text-xs font-bold text-slate-600">
              <input
                type="checkbox"
                checked={autoRefresh}
                onChange={(event) => setAutoRefresh(event.target.checked)}
                className="h-4 w-4 rounded border-slate-300"
              />
              自动刷新（30 秒）
            </label>
            <button
              type="button"
              onClick={() => void load({ silent: true })}
              className="flex min-h-9 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm font-bold text-slate-700 transition hover:bg-slate-50"
            >
              <RefreshCw
                size={15}
                aria-hidden="true"
                className={refreshing ? "animate-spin" : ""}
              />
              刷新
            </button>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-7xl px-4 py-5">
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="时间窗口">
          {WINDOW_OPTIONS.map((option) => (
            <button
              key={option.hours}
              type="button"
              aria-pressed={hours === option.hours}
              onClick={() => {
                setHours(option.hours);
                setPage(1);
              }}
              className={`min-h-9 rounded-lg border px-3 py-1.5 text-sm font-bold transition ${
                hours === option.hours
                  ? "border-[var(--brand)] bg-[var(--info-bg)] text-[var(--brand-strong)]"
                  : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>

        {error ? (
          <p className="mt-4 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </p>
        ) : null}

        {overview && !overview.available ? (
          <p className="mt-4 flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" aria-hidden="true" />
            聚合查询失败，下面的数字并不代表真实为零，请检查数据库连通性。
          </p>
        ) : null}

        {captureWarnings.length > 0 ? (
          <ul className="mt-4 space-y-1.5">
            {captureWarnings.map((warning) => (
              <li
                key={warning}
                className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800"
              >
                <ShieldAlert size={16} className="mt-0.5 shrink-0" aria-hidden="true" />
                {warning}
              </li>
            ))}
          </ul>
        ) : null}

        <section className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
          <KpiCard
            icon={<Activity size={16} />}
            label="请求总数"
            value={totals ? formatCount(totals.request_count) : "—"}
            hint={totals ? `${totals.requests_per_minute.toFixed(2)} 次/分钟` : undefined}
          />
          <KpiCard
            icon={<CircleAlert size={16} />}
            label="服务端错误率"
            value={totals ? `${totals.server_error_rate_percent.toFixed(2)}%` : "—"}
            hint={
              totals
                ? `${formatCount(totals.server_error_count)} 次 5xx / ${formatCount(
                    totals.client_error_count,
                  )} 次 4xx`
                : undefined
            }
            tone={totals && totals.server_error_rate_percent > 1 ? "danger" : "default"}
          />
          <KpiCard
            icon={<Gauge size={16} />}
            label="P95 响应时间"
            value={formatMs(totals?.p95_ms)}
            hint={totals ? `平均 ${formatMs(totals.mean_ms)}` : undefined}
          />
          <KpiCard
            icon={<AlertTriangle size={16} />}
            label="未解决错误组"
            value={errorStats ? formatCount(errorStats.open_group_count) : "—"}
            hint={
              errorStats
                ? `窗口内新增 ${errorStats.new_group_count} 组 / 前端 ${errorStats.frontend_event_count} 次、后端 ${errorStats.backend_event_count} 次`
                : undefined
            }
            tone={errorStats && errorStats.open_group_count > 0 ? "danger" : "default"}
          />
        </section>

        <section className="mt-4 grid gap-3 lg:grid-cols-2">
          <div className="rounded-2xl border border-slate-200 bg-white p-4">
            <h2 className="text-sm font-bold text-slate-800">访问流量与错误</h2>
            <div className="mt-3">
              <OpsTrafficChart
                points={overview?.series ?? []}
                bucketSeconds={overview?.window.bucket_seconds ?? 60}
              />
            </div>
          </div>
          <div className="rounded-2xl border border-slate-200 bg-white p-4">
            <h2 className="text-sm font-bold text-slate-800">响应时间</h2>
            <div className="mt-3">
              <OpsLatencyChart points={overview?.series ?? []} />
            </div>
            {overview ? (
              <p className="mt-2 text-xs text-slate-500">{overview.notes.percentile_basis}</p>
            ) : null}
          </div>
        </section>

        <section className="mt-4 rounded-2xl border border-slate-200 bg-white">
          <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 px-4 py-3">
            <h2 className="text-sm font-bold text-slate-800">错误分组</h2>
            <span className="text-xs text-slate-500">共 {groups.total} 组</span>
            <div className="ml-auto flex flex-wrap items-center gap-2">
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  setQuery(queryDraft.trim());
                  setPage(1);
                }}
                className="flex items-center gap-1.5"
              >
                <label className="sr-only" htmlFor="ops-search">
                  搜索错误
                </label>
                <div className="relative">
                  <Search
                    size={14}
                    aria-hidden="true"
                    className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400"
                  />
                  <input
                    id="ops-search"
                    type="search"
                    value={queryDraft}
                    onChange={(event) => setQueryDraft(event.target.value)}
                    placeholder="搜索类型 / 文案 / 路由 / 指纹"
                    maxLength={120}
                    className="min-h-9 w-56 rounded-lg border border-slate-200 pl-8 pr-2.5 text-sm outline-none transition focus:border-[var(--brand)]"
                  />
                </div>
                <button
                  type="submit"
                  className="min-h-9 rounded-lg border border-slate-200 bg-white px-2.5 text-sm font-bold text-slate-700 transition hover:bg-slate-50"
                >
                  搜索
                </button>
              </form>
              <select
                aria-label="按来源筛选"
                value={source}
                onChange={(event) => {
                  setSource(event.target.value as OpsSourceFilter);
                  setPage(1);
                }}
                className="min-h-9 rounded-lg border border-slate-200 bg-white px-2 text-sm font-bold text-slate-700"
              >
                {SOURCE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
              <select
                aria-label="按状态筛选"
                value={status}
                onChange={(event) => {
                  setStatus(event.target.value as OpsStatusFilter);
                  setPage(1);
                }}
                className="min-h-9 rounded-lg border border-slate-200 bg-white px-2 text-sm font-bold text-slate-700"
              >
                {STATUS_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {loading && overview === null ? (
            <p className="px-4 py-10 text-center text-sm text-slate-500">正在加载…</p>
          ) : groups.items.length === 0 ? (
            <p className="px-4 py-10 text-center text-sm text-slate-500">
              该条件下没有错误记录。
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[820px] text-left text-sm">
                <thead>
                  <tr className="border-b border-slate-100 text-xs text-slate-500">
                    <th scope="col" className="px-4 py-2 font-bold">
                      错误
                    </th>
                    <th scope="col" className="px-3 py-2 font-bold">
                      来源
                    </th>
                    <th scope="col" className="px-3 py-2 text-right font-bold">
                      窗口内
                    </th>
                    <th scope="col" className="px-3 py-2 text-right font-bold">
                      累计
                    </th>
                    <th scope="col" className="px-3 py-2 text-right font-bold">
                      影响用户
                    </th>
                    <th scope="col" className="px-3 py-2 font-bold">
                      最近出现
                    </th>
                    <th scope="col" className="px-3 py-2 font-bold">
                      状态
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {groups.items.map((group) => (
                    <tr
                      key={group.fingerprint}
                      className="cursor-pointer border-b border-slate-50 transition hover:bg-slate-50"
                      onClick={() => setSelected(group.fingerprint)}
                    >
                      <td className="px-4 py-2.5">
                        <button
                          type="button"
                          className="block max-w-md text-left"
                          onClick={(event) => {
                            event.stopPropagation();
                            setSelected(group.fingerprint);
                          }}
                        >
                          <span className="block truncate font-bold text-slate-800">
                            {group.errorType}
                          </span>
                          <span className="block truncate text-xs text-slate-500">
                            {group.message}
                          </span>
                          {group.route ? (
                            <span className="block truncate font-mono text-xs text-slate-400">
                              {group.route}
                            </span>
                          ) : null}
                        </button>
                      </td>
                      <td className="px-3 py-2.5 text-xs text-slate-600">
                        {SOURCE_LABELS[group.source] ?? group.source}
                      </td>
                      <td className="px-3 py-2.5 text-right font-bold text-slate-800">
                        {formatCount(group.windowEventCount)}
                      </td>
                      <td className="px-3 py-2.5 text-right text-slate-600">
                        {formatCount(group.eventCount)}
                      </td>
                      <td className="px-3 py-2.5 text-right text-slate-600">
                        {formatCount(group.affectedUserCount)}
                      </td>
                      <td className="px-3 py-2.5 text-xs text-slate-600">
                        <span className="flex items-center gap-1">
                          <Clock size={12} aria-hidden="true" />
                          {formatTime(group.lastSeenAt)}
                        </span>
                      </td>
                      <td className="px-3 py-2.5">
                        <StatusBadge status={group.status} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {groups.totalPages > 1 ? (
            <div className="flex items-center justify-between border-t border-slate-200 px-4 py-2.5">
              <span className="text-xs text-slate-500">
                第 {groups.page} / {groups.totalPages} 页
              </span>
              <div className="flex gap-1.5">
                <button
                  type="button"
                  disabled={groups.page <= 1}
                  onClick={() => setPage((current) => Math.max(1, current - 1))}
                  className="flex min-h-9 items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 text-sm font-bold text-slate-700 transition hover:bg-slate-50 disabled:opacity-50"
                >
                  <ChevronLeft size={15} aria-hidden="true" />
                  上一页
                </button>
                <button
                  type="button"
                  disabled={groups.page >= groups.totalPages}
                  onClick={() => setPage((current) => current + 1)}
                  className="flex min-h-9 items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 text-sm font-bold text-slate-700 transition hover:bg-slate-50 disabled:opacity-50"
                >
                  下一页
                  <ChevronRight size={15} aria-hidden="true" />
                </button>
              </div>
            </div>
          ) : null}
        </section>

        <section className="mt-4 rounded-2xl border border-slate-200 bg-white">
          <div className="border-b border-slate-200 px-4 py-3">
            <h2 className="text-sm font-bold text-slate-800">接口耗时与错误</h2>
            <p className="mt-0.5 text-xs text-slate-500">按 P95 从高到低排列</p>
          </div>
          {(overview?.top_routes.length ?? 0) === 0 ? (
            <p className="px-4 py-10 text-center text-sm text-slate-500">
              该时间窗口内没有接口统计。
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px] text-left text-sm">
                <thead>
                  <tr className="border-b border-slate-100 text-xs text-slate-500">
                    <th scope="col" className="px-4 py-2 font-bold">
                      接口
                    </th>
                    <th scope="col" className="px-3 py-2 text-right font-bold">
                      请求数
                    </th>
                    <th scope="col" className="px-3 py-2 text-right font-bold">
                      5xx
                    </th>
                    <th scope="col" className="px-3 py-2 text-right font-bold">
                      错误率
                    </th>
                    <th scope="col" className="px-3 py-2 text-right font-bold">
                      平均
                    </th>
                    <th scope="col" className="px-3 py-2 text-right font-bold">
                      P95
                    </th>
                    <th scope="col" className="px-3 py-2 text-right font-bold">
                      最慢
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {overview?.top_routes.map((row) => (
                    <tr
                      key={`${row.method} ${row.route}`}
                      className="border-b border-slate-50"
                    >
                      <td className="px-4 py-2.5">
                        <span className="mr-1.5 rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs font-bold text-slate-600">
                          {row.method}
                        </span>
                        <span className="font-mono text-xs text-slate-700">{row.route}</span>
                      </td>
                      <td className="px-3 py-2.5 text-right text-slate-700">
                        {formatCount(row.request_count)}
                      </td>
                      <td className="px-3 py-2.5 text-right text-slate-700">
                        {formatCount(row.server_error_count)}
                      </td>
                      <td
                        className={`px-3 py-2.5 text-right font-bold ${
                          row.server_error_rate_percent > 1 ? "text-red-600" : "text-slate-700"
                        }`}
                      >
                        {row.server_error_rate_percent.toFixed(2)}%
                      </td>
                      <td className="px-3 py-2.5 text-right text-slate-700">
                        {formatMs(row.mean_ms)}
                      </td>
                      <td className="px-3 py-2.5 text-right font-bold text-slate-800">
                        {formatMs(row.p95_ms)}
                      </td>
                      <td className="px-3 py-2.5 text-right text-slate-500">
                        {formatMs(row.max_ms)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {capture ? (
          <p className="mt-4 text-xs text-slate-500">
            采集进程 {capture.instanceId} · 错误留存 {capture.errorRetentionDays} 天 · 流量留存{" "}
            {capture.trafficRetentionDays} 天 · 待写入 {capture.queueDepth} 条
            {overview ? ` · ${overview.notes.privacy}` : ""}
          </p>
        ) : null}
      </div>

      {selected ? (
        <OpsErrorDetailPanel
          fingerprint={selected}
          onClose={() => setSelected(null)}
          onStatusChange={onStatusChange}
        />
      ) : null}
    </main>
  );
}

function KpiCard({
  icon,
  label,
  value,
  hint,
  tone = "default",
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  hint?: string;
  tone?: "default" | "danger";
}) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4">
      <div className="flex items-center gap-1.5 text-xs font-bold text-slate-500">
        <span aria-hidden="true" className={tone === "danger" ? "text-red-500" : "text-slate-400"}>
          {icon}
        </span>
        {label}
      </div>
      <p
        className={`mt-1.5 text-2xl font-black tabular-nums ${
          tone === "danger" ? "text-red-600" : "text-slate-900"
        }`}
      >
        {value}
      </p>
      {hint ? <p className="mt-0.5 text-xs text-slate-500">{hint}</p> : null}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  if (status === "resolved") {
    return (
      <span className="rounded-md bg-emerald-50 px-2 py-0.5 text-xs font-bold text-emerald-700">
        已解决
      </span>
    );
  }
  if (status === "ignored") {
    return (
      <span className="rounded-md bg-slate-100 px-2 py-0.5 text-xs font-bold text-slate-500">
        已忽略
      </span>
    );
  }
  return (
    <span className="rounded-md bg-red-50 px-2 py-0.5 text-xs font-bold text-red-700">
      未解决
    </span>
  );
}
