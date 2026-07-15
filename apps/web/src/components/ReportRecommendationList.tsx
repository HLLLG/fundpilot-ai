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
