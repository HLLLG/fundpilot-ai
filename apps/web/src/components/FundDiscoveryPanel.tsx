"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, RotateCcw, Sparkles, Target } from "lucide-react";
import type {
  AnalysisMode,
  DiscoveryPromptConfig,
  DiscoveryRecommendation,
  DiscoverySectorHeat,
  FundDiscoveryReport,
  FundTypePreference,
  Holding,
  InvestorProfile,
  SelectionStrategy,
} from "@/lib/api";
import {
  fetchDiscoveryPrompt,
  fetchDiscoverySectors,
  listDiscoveryReports,
  saveDiscoveryPromptRemote,
  startDiscoveryJob,
} from "@/lib/api";
import { DiscoveryHistoryRail } from "@/components/DiscoveryHistoryRail";
import { DiscoveryJobStatusFloat } from "@/components/DiscoveryJobStatusFloat";
import { DiscoveryReportPanel } from "@/components/DiscoveryReportPanel";
import { RolePromptEditor } from "@/components/RolePromptEditor";
import { YangjibaoFundDetail } from "@/components/YangjibaoFundDetail";
import { displayableHoldings } from "@/lib/holdingMetrics";
import { loadDiscoveryPrompt, saveDiscoveryPrompt } from "@/lib/storage";

const FUND_TYPE_OPTIONS: { id: FundTypePreference; label: string }[] = [
  { id: "any", label: "不限" },
  { id: "etf_link", label: "ETF联接优先" },
  { id: "no_c_class", label: "排除C类" },
];

const SELECTION_STRATEGY_OPTIONS: { id: SelectionStrategy; label: string; hint: string }[] = [
  { id: "balanced", label: "均衡潜力", hint: "近3~6月走强、避免追年度冠军" },
  { id: "with_new_issue", label: "含新发观察", hint: "混入近6月新发 + 均衡老基" },
];

type FundDiscoveryPanelProps = {
  holdings: Holding[];
  profile: InvestorProfile;
  analysisMode: AnalysisMode;
  onAnalysisModeChange: (mode: AnalysisMode) => void;
};

export function FundDiscoveryPanel({
  holdings,
  profile,
  analysisMode,
  onAnalysisModeChange,
}: FundDiscoveryPanelProps) {
  const [sectors, setSectors] = useState<DiscoverySectorHeat[]>([]);
  const [focusSectors, setFocusSectors] = useState<string[]>([]);
  const [fundTypePreference, setFundTypePreference] = useState<FundTypePreference>("any");
  const [selectionStrategy, setSelectionStrategy] = useState<SelectionStrategy>("balanced");
  const [budgetYuan, setBudgetYuan] = useState<string>("");
  const [report, setReport] = useState<FundDiscoveryReport | null>(null);
  const [historyReports, setHistoryReports] = useState<FundDiscoveryReport[]>([]);
  const [discoveryPrompt, setDiscoveryPrompt] = useState<DiscoveryPromptConfig>(() =>
    loadDiscoveryPrompt({
      role_prompt: "",
      default_role_prompt: "",
    }),
  );
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadingSectors, setLoadingSectors] = useState(true);
  const [sectorsError, setSectorsError] = useState<string | null>(null);
  const [previewHolding, setPreviewHolding] = useState<Holding | null>(null);
  const promptPersistReady = useRef(false);
  const [promptReady, setPromptReady] = useState(false);

  const loadSectors = useCallback(async () => {
    setLoadingSectors(true);
    setSectorsError(null);
    try {
      const rows = await fetchDiscoverySectors();
      setSectors(rows);
    } catch (loadError) {
      setSectors([]);
      setSectorsError(loadError instanceof Error ? loadError.message : "加载板块热度失败");
    } finally {
      setLoadingSectors(false);
    }
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      const rows = await listDiscoveryReports();
      setHistoryReports(rows);
    } catch {
      setHistoryReports([]);
    }
  }, []);

  useEffect(() => {
    void loadSectors();
    void loadHistory();
    void (async () => {
      try {
        const remote = await fetchDiscoveryPrompt();
        setDiscoveryPrompt(remote);
        saveDiscoveryPrompt(remote);
      } catch {
        setDiscoveryPrompt((current) => loadDiscoveryPrompt(current));
      } finally {
        promptPersistReady.current = true;
        setPromptReady(true);
      }
    })();
  }, [loadHistory, loadSectors]);

  useEffect(() => {
    if (!promptReady || !promptPersistReady.current) return;
    saveDiscoveryPrompt(discoveryPrompt);
    const storedValue = discoveryPrompt.is_custom ? discoveryPrompt.role_prompt : null;
    void saveDiscoveryPromptRemote(storedValue).catch(() => {
      // 离线时仍保留 localStorage
    });
  }, [discoveryPrompt, promptReady]);

  const toggleSector = (label: string) => {
    setFocusSectors((current) => {
      if (current.includes(label)) {
        return current.filter((item) => item !== label);
      }
      if (current.length >= 3) return current;
      return [...current, label];
    });
  };

  const handleScan = async () => {
    setIsSubmitting(true);
    setError(null);
    try {
      const parsedBudget = budgetYuan.trim() ? Number(budgetYuan) : null;
      const jobId = await startDiscoveryJob(displayableHoldings(holdings), profile, {
        analysisMode,
        focusSectors,
        budgetYuan: parsedBudget && !Number.isNaN(parsedBudget) ? parsedBudget : null,
        fundTypePreference,
        selectionStrategy,
        systemRolePrompt: discoveryPrompt.is_custom ? discoveryPrompt.role_prompt : null,
      });
      setActiveJobId(jobId);
    } catch (scanError) {
      setError(scanError instanceof Error ? scanError.message : "提交失败");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleOpenFund = (recommendation: DiscoveryRecommendation) => {
    setPreviewHolding({
      fund_code: recommendation.fund_code,
      fund_name: recommendation.fund_name,
      sector_name: recommendation.sector_name,
      holding_amount: 0,
      return_percent: 0,
      holding_profit: 0,
      holding_return_percent: 0,
    });
  };

  return (
    <div className="grid min-w-0 gap-6 xl:grid-cols-[minmax(0,1fr)_280px]">
      <div className="grid min-w-0 gap-4">
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex items-center gap-2">
            <Target size={18} className="text-indigo-600" />
            <h2 className="text-base font-black text-slate-950">推荐基金</h2>
          </div>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            基于板块热度与组合缺口，从受控候选池中精选 3~5 只值得关注的机会。默认「均衡潜力」避免追近1年冠军；可选「含新发观察」混入新发基金。仅供参考，不构成投资建议。
          </p>

          <div className="mt-4 overflow-hidden rounded-xl border border-slate-100">
            <div className="flex items-center justify-between gap-2 border-b border-slate-100 px-3 py-2.5">
              <div className="flex items-center gap-2">
                <Sparkles size={15} className="text-violet-600" />
                <span className="text-xs font-bold text-slate-700">AI 角色设定</span>
              </div>
              {discoveryPrompt.is_custom ? (
                <button
                  type="button"
                  onClick={() =>
                    setDiscoveryPrompt((current) => ({
                      ...current,
                      role_prompt: current.default_role_prompt,
                      is_custom: false,
                    }))
                  }
                  className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2 py-1 text-[11px] font-bold text-slate-600 transition hover:bg-slate-50"
                >
                  <RotateCcw size={12} />
                  恢复默认
                </button>
              ) : (
                <span className="text-[11px] font-semibold text-slate-400">默认模板</span>
              )}
            </div>
            <RolePromptEditor
              value={discoveryPrompt.role_prompt}
              onChange={(value) =>
                setDiscoveryPrompt((current) => ({
                  ...current,
                  role_prompt: value,
                  is_custom: value.trim() !== current.default_role_prompt.trim(),
                }))
              }
            />
          </div>

          <div className="mt-4">
            <div className="mb-2 text-xs font-semibold text-slate-700">
              关注方向（可选，最多 3 个）
            </div>
            {loadingSectors ? (
              <div className="flex items-center gap-2 text-xs text-slate-500">
                <Loader2 size={14} className="animate-spin" />
                加载板块热度…
              </div>
            ) : sectorsError ? (
              <div className="rounded-xl border border-red-100 bg-red-50 px-3 py-2 text-xs text-red-700">
                <p>{sectorsError}</p>
                <button
                  type="button"
                  onClick={() => void loadSectors()}
                  className="mt-2 font-semibold text-red-800 underline"
                >
                  重试
                </button>
              </div>
            ) : (
              <div className="flex flex-wrap gap-2">
                {sectors.map((sector) => {
                  const selected = focusSectors.includes(sector.sector_label);
                  const change = sector.change_1d_percent;
                  const changeClass =
                    change == null
                      ? "text-slate-500"
                      : change >= 0
                        ? selected
                          ? "text-rose-100"
                          : "text-rose-600"
                        : selected
                          ? "text-emerald-100"
                          : "text-emerald-600";
                  return (
                    <button
                      key={sector.sector_label}
                      type="button"
                      onClick={() => toggleSector(sector.sector_label)}
                      className={`rounded-full border px-3 py-1.5 text-xs font-medium transition ${
                        selected
                          ? "border-indigo-600 bg-indigo-600 text-white"
                          : "border-slate-200 bg-slate-50 text-slate-700 hover:bg-slate-100"
                      }`}
                    >
                      {sector.sector_label}
                      {change != null ? (
                        <span className={changeClass}>
                          {" "}
                          {change >= 0 ? "+" : ""}
                          {change.toFixed(2)}%
                        </span>
                      ) : null}
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          <div className="mt-4">
            <div className="mb-2 text-xs font-semibold text-slate-700">选基策略</div>
            <div className="flex flex-wrap gap-2">
              {SELECTION_STRATEGY_OPTIONS.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  title={option.hint}
                  onClick={() => setSelectionStrategy(option.id)}
                  className={`rounded-full border px-3 py-1.5 text-xs font-medium transition ${
                    selectionStrategy === option.id
                      ? "border-indigo-600 bg-indigo-600 text-white"
                      : "border-slate-200 bg-slate-50 text-slate-700 hover:bg-slate-100"
                  }`}
                >
                  {option.label}
                </button>
              ))}
            </div>
            <p className="mt-1.5 text-[11px] leading-5 text-slate-500">
              {SELECTION_STRATEGY_OPTIONS.find((item) => item.id === selectionStrategy)?.hint}
            </p>
          </div>

          <div className="mt-4">
            <div className="mb-2 text-xs font-semibold text-slate-700">基金类型偏好</div>
            <div className="flex flex-wrap gap-2">
              {FUND_TYPE_OPTIONS.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  onClick={() => setFundTypePreference(option.id)}
                  className={`rounded-full border px-3 py-1.5 text-xs font-medium transition ${
                    fundTypePreference === option.id
                      ? "border-slate-900 bg-slate-900 text-white"
                      : "border-slate-200 bg-slate-50 text-slate-700 hover:bg-slate-100"
                  }`}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <label className="block text-xs font-semibold text-slate-700">
              本次可投入预算（元，可选）
              <input
                type="number"
                min={0}
                step={500}
                value={budgetYuan}
                onChange={(event) => setBudgetYuan(event.target.value)}
                placeholder="默认按期望投入余额"
                className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-indigo-400"
              />
            </label>
            <div>
              <div className="text-xs font-semibold text-slate-700">分析模式</div>
              <div className="mt-1 flex rounded-xl border border-slate-200 p-1">
                {(["fast", "deep"] as const).map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => onAnalysisModeChange(mode)}
                    className={`flex-1 rounded-lg px-3 py-2 text-xs font-bold ${
                      analysisMode === mode ? "bg-slate-900 text-white" : "text-slate-600"
                    }`}
                  >
                    {mode === "fast" ? "快速" : "深度"}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {error ? <p className="mt-3 text-sm text-red-600">{error}</p> : null}

          <button
            type="button"
            data-testid="discovery-scan-button"
            disabled={isSubmitting || Boolean(activeJobId)}
            onClick={() => void handleScan()}
            className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-xl bg-indigo-600 px-4 py-3 text-sm font-bold text-white hover:bg-indigo-700 disabled:opacity-60 sm:w-auto"
          >
            {isSubmitting ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />}
            扫描今日机会
          </button>
        </section>

        {report ? (
          <DiscoveryReportPanel report={report} onOpenFund={handleOpenFund} />
        ) : null}

        <DiscoveryJobStatusFloat
          jobId={activeJobId}
          onComplete={(completed) => {
            setReport(completed);
            setActiveJobId(null);
            void loadHistory();
          }}
          onClose={() => setActiveJobId(null)}
          onRetry={() => void handleScan()}
        />
      </div>

      <DiscoveryHistoryRail
        reports={historyReports}
        activeReportId={report?.id}
        onRefresh={() => void loadHistory()}
        onSelect={(selected) => setReport(selected)}
        onDeleted={(reportId) => {
          if (report?.id === reportId) {
            setReport(null);
          }
        }}
      />

      {previewHolding ? (
        <YangjibaoFundDetail
          holding={previewHolding}
          holdingIndex={0}
          holdings={[previewHolding]}
          onClose={() => setPreviewHolding(null)}
          onNavigate={() => undefined}
        />
      ) : null}
    </div>
  );
}
