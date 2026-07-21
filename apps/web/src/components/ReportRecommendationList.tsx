import type { Report } from "@/lib/api";
import {
  displayFundRecommendations,
  groupFundRecommendations,
} from "@/lib/reportPresentation";
import { FundRecommendationCard } from "@/components/FundRecommendationCard";

type ReportRecommendationListProps = {
  report: Report;
  recommendations?: Report["fund_recommendations"];
};

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

function DecisionReadinessNotice({ report }: { report: Report }) {
  const facts = report.analysis_facts as EvidenceGuardFacts | undefined;
  const executionBlocked = Boolean(facts?.data_evidence_guard?.execution_blocked);
  if (!executionBlocked) return null;

  const reasons = evidenceBlockingReasons(report);
  const ledgerIncomplete = reasons.has("incomplete_or_unsettled_position_ledger");
  const ledgerOnlyExecutionBlock = ledgerIncomplete && [...reasons].every(
    (reason) => reason === "incomplete_or_unsettled_position_ledger",
  );
  return (
    <div className="mb-4 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-amber-950">
      <h3 className="text-sm font-black">
        {ledgerOnlyExecutionBlock ? "这份报告生成时为什么只有“观察”？" : "为什么现在只有“观察”？"}
      </h3>
      <p className="mt-1 text-xs leading-5 text-amber-900">
        {ledgerOnlyExecutionBlock
          ? "这份报告使用了旧版份额规则。现在系统会直接使用截图中的持仓估值；重新生成日报即可获得相对当前持仓的百分比建议。"
          : "部分持仓或行情信息还不够完整、或更新时间不够新。为避免用旧数据误导操作，本次只展示观察和风险提示。"}
      </p>
    </div>
  );
}

export function ReportRecommendationList({
  report,
  recommendations,
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
      <DecisionReadinessNotice report={report} />
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
                />
              );
            })}
          </div>
        </div>
      ) : null}
    </section>
  );
}
