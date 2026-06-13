"use client";

import { useEffect, useState } from "react";
import { Loader2, Sparkles, Target } from "lucide-react";
import type {
  AnalysisMode,
  DiscoverySectorHeat,
  FundDiscoveryReport,
  Holding,
  InvestorProfile,
} from "@/lib/api";
import { fetchDiscoverySectors, startDiscoveryJob } from "@/lib/api";
import { DiscoveryReportPanel } from "@/components/DiscoveryReportPanel";
import { DiscoveryJobStatusFloat } from "@/components/DiscoveryJobStatusFloat";
import { displayableHoldings } from "@/lib/holdingMetrics";

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
  const [budgetYuan, setBudgetYuan] = useState<string>("");
  const [report, setReport] = useState<FundDiscoveryReport | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadingSectors, setLoadingSectors] = useState(true);

  useEffect(() => {
    void fetchDiscoverySectors()
      .then(setSectors)
      .catch(() => setSectors([]))
      .finally(() => setLoadingSectors(false));
  }, []);

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
      });
      setActiveJobId(jobId);
    } catch (scanError) {
      setError(scanError instanceof Error ? scanError.message : "提交失败");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="grid min-w-0 gap-4">
      <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex items-center gap-2">
          <Target size={18} className="text-indigo-600" />
          <h2 className="text-base font-black text-slate-950">推荐基金</h2>
        </div>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          基于板块热度与组合缺口，从受控候选池中精选 3~5 只值得关注的新基金。仅供参考，不构成投资建议。
        </p>

        <div className="mt-4">
          <div className="mb-2 text-xs font-semibold text-slate-700">
            关注方向（可选，最多 3 个）
          </div>
          {loadingSectors ? (
            <div className="flex items-center gap-2 text-xs text-slate-500">
              <Loader2 size={14} className="animate-spin" />
              加载板块热度…
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              {sectors.map((sector) => {
                const selected = focusSectors.includes(sector.sector_label);
                const change = sector.change_1d_percent;
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
                      <span className={selected ? "text-indigo-100" : "text-slate-500"}>
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

      {report ? <DiscoveryReportPanel report={report} /> : null}

      <DiscoveryJobStatusFloat
        jobId={activeJobId}
        onComplete={(completed) => {
          setReport(completed);
          setActiveJobId(null);
        }}
        onClose={() => setActiveJobId(null)}
        onRetry={() => void handleScan()}
      />
    </div>
  );
}
