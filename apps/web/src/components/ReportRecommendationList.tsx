import type { Holding, ParsedTransaction, Report } from "@/lib/api";
import {
  displayFundRecommendations,
  groupFundRecommendations,
} from "@/lib/reportPresentation";
import { FundRecommendationCard } from "@/components/FundRecommendationCard";

type ReportRecommendationListProps = {
  report: Report;
  recommendations?: Report["fund_recommendations"];
  currentHoldings?: Holding[];
  onConfirmLedgerBaseline?: () => void;
  onApplyTransaction?: (transaction: ParsedTransaction) => Promise<unknown>;
};

function currentHoldingFor(
  item: Report["fund_recommendations"][number],
  holdings: Holding[] | undefined,
): Holding | undefined {
  if (!holdings?.length) return undefined;
  const exact = holdings.find(
    (holding) => holding.fund_code === item.fund_code && holding.fund_name === item.fund_name,
  );
  if (exact) return exact;
  const codeMatches = holdings.filter((holding) => holding.fund_code === item.fund_code);
  return codeMatches.length === 1 ? codeMatches[0] : undefined;
}

type EvidenceGuardFacts = {
  data_evidence?: { blocking_reasons?: string[] };
  data_evidence_guard?: {
    execution_blocked?: boolean;
    reasons_by_fund?: Record<string, string[]>;
  };
};

function evidenceBlockingReasons(report: Report): Set<string> {
  const facts = report.analysis_facts as EvidenceGuardFacts | undefined;
  const reasons = new Set(facts?.data_evidence?.blocking_reasons ?? []);
  for (const values of Object.values(facts?.data_evidence_guard?.reasons_by_fund ?? {})) {
    for (const value of values) reasons.add(value);
  }
  return reasons;
}

function DecisionReadinessNotice({
  report,
  onConfirmLedgerBaseline,
}: {
  report: Report;
  onConfirmLedgerBaseline?: () => void;
}) {
  const facts = report.analysis_facts as EvidenceGuardFacts | undefined;
  if (!facts?.data_evidence_guard?.execution_blocked) return null;

  const reasons = evidenceBlockingReasons(report);
  const ledgerIncomplete = reasons.has("incomplete_or_unsettled_position_ledger");
  return (
    <div className="mb-4 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-amber-950">
      <h3 className="text-sm font-black">为什么现在只有“观察”？</h3>
      <p className="mt-1 text-xs leading-5 text-amber-900">
        {ledgerIncomplete
          ? "系统还不能确认每只基金的实际份额和成本，所以先不提供加减仓或买卖金额。到“持仓”里核对一次账本基线后，后续日报就能继续判断是否可操作。"
          : "部分持仓或行情信息还不够完整、或更新时间不够新。为避免用旧数据误导操作，本次只展示观察和风险提示。"}
      </p>
      {ledgerIncomplete && onConfirmLedgerBaseline ? (
        <button
          type="button"
          onClick={onConfirmLedgerBaseline}
          className="mt-3 min-h-10 rounded-xl border border-amber-300 bg-white px-3 text-xs font-black text-amber-950 transition hover:bg-amber-100"
        >
          去确认账本基线
        </button>
      ) : null}
    </div>
  );
}

export function ReportRecommendationList({
  report,
  recommendations,
  currentHoldings,
  onConfirmLedgerBaseline,
  onApplyTransaction,
}: ReportRecommendationListProps) {
  const items = recommendations ?? displayFundRecommendations(report);
  const { needsAction, observing } = groupFundRecommendations(items);

  if (!needsAction.length && !observing.length) {
    return (
      <p className="report-panel p-5 text-sm text-slate-600">
        这份历史日报没有可解析的逐基金建议
      </p>
    );
  }

  return (
    <section className="report-panel min-w-0 p-4 sm:p-5">
      <DecisionReadinessNotice
        report={report}
        onConfirmLedgerBaseline={onConfirmLedgerBaseline}
      />
      {needsAction.length ? (
        <div className="min-w-0">
          <h3 className="text-base font-black text-slate-950">需要处理</h3>
          <p className="mt-1 text-xs text-slate-500">
            {needsAction.length} 只基金存在明确仓位动作
          </p>
          <div className="mt-3 min-w-0 space-y-3">
            {needsAction.map((item) => {
              const recommendationIndex = items.indexOf(item);
              return (
                <FundRecommendationCard
                  key={`${report.id}:${recommendationIndex}:${item.fund_code}`}
                  item={item}
                  report={report}
                  recommendationIndex={recommendationIndex}
                  defaultExpanded
                  currentHolding={currentHoldingFor(item, currentHoldings)}
                  onApplyTransaction={onApplyTransaction}
                />
              );
            })}
          </div>
        </div>
      ) : null}
      {observing.length ? (
        <div className={`min-w-0 ${needsAction.length ? "mt-6" : ""}`}>
          <h3 className="text-base font-black text-slate-950">继续观察</h3>
          <p className="mt-1 text-xs text-slate-500">
            {observing.length} 只基金暂无立即交易动作
          </p>
          <div className="mt-3 min-w-0 space-y-2">
            {observing.map((item) => {
              const recommendationIndex = items.indexOf(item);
              return (
                <FundRecommendationCard
                  key={`${report.id}:${recommendationIndex}:${item.fund_code}`}
                  item={item}
                  report={report}
                  recommendationIndex={recommendationIndex}
                  defaultExpanded={false}
                  currentHolding={currentHoldingFor(item, currentHoldings)}
                  onApplyTransaction={onApplyTransaction}
                />
              );
            })}
          </div>
        </div>
      ) : null}
    </section>
  );
}
