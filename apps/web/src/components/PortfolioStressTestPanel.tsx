"use client";

import { InlineNotice } from "@/components/InlineNotice";
import type { PortfolioStressScenario } from "@/lib/api";
import {
  fetchPortfolioFeeEvidence,
  fetchPortfolioStressTest,
} from "@/lib/api";
import { useLazyAsyncResource } from "@/lib/useLazyAsyncResource";


const money = new Intl.NumberFormat("zh-CN", {
  style: "currency",
  currency: "CNY",
  maximumFractionDigits: 0,
});


const REASON_LABELS: Record<string, string> = {
  positive_current_holdings_unavailable: "暂无正金额持仓",
  holding_count_exceeds_bounded_fetch_limit: "持仓数量超过单次压力测试上限",
  holding_fund_code_invalid: "存在无法识别的基金代码",
  holding_fund_code_duplicated: "持仓基金代码重复",
  holding_nav_history_incomplete: "至少一只持仓缺少可核验净值历史",
  common_return_sample_insufficient: "所有持仓共同覆盖的交易日不足 60 天",
};


function scenarioPeriod(scenario: PortfolioStressScenario): string {
  return scenario.start_date === scenario.end_date
    ? scenario.start_date
    : `${scenario.start_date} 至 ${scenario.end_date}`;
}


export function PortfolioStressTestPanel({ enabled }: { enabled: boolean }) {
  const stress = useLazyAsyncResource({
    enabled,
    load: fetchPortfolioStressTest,
    errorMessage: "历史压力测试加载失败",
  });
  const fees = useLazyAsyncResource({
    enabled,
    load: fetchPortfolioFeeEvidence,
    errorMessage: "实际费用证据加载失败",
  });

  if (!enabled) {
    return null;
  }

  return (
    <div className="mt-3 space-y-3">
      {stress.error ? (
        <InlineNotice
          tone="error"
          message={`历史压力测试加载失败：${stress.error}`}
          action={{ label: "重试", onClick: stress.retry }}
        />
      ) : null}
      {fees.error ? (
        <InlineNotice
          tone="error"
          message={`实际费用证据加载失败：${fees.error}`}
          action={{ label: "重试", onClick: fees.retry }}
        />
      ) : null}
      {(stress.loading || fees.loading) && !stress.data && !fees.data ? (
        <div className="risk-corr-note" role="status">
          正在对齐持仓净值并读取逐笔费用证据…
        </div>
      ) : null}

      {stress.data ? (
        <section className="rounded-2xl border border-slate-200 bg-slate-50/70 p-3">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <h3 className="text-sm font-black text-slate-900">当前权重历史压力重放</h3>
              <p className="mt-1 text-xs leading-5 text-slate-500">
                不是未来预测；缺任一持仓时整包不出数，也不会自动调仓。
              </p>
            </div>
            <span className="rounded-full bg-slate-200 px-2 py-1 text-[10px] font-bold text-slate-600">
              {stress.data.model_version}
            </span>
          </div>

          {!stress.data.available ? (
            <div className="mt-3 rounded-xl bg-white px-3 py-3 text-xs leading-5 text-slate-600">
              暂不生成压力数字：
              {stress.data.reason_codes
                .map((code) => REASON_LABELS[code] ?? code)
                .join("；") || "证据不足"}
              。空值不代表零风险。
            </div>
          ) : (
            <>
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                {stress.data.scenarios.map((scenario) => (
                  <div key={scenario.scenario_id} className="rounded-xl bg-white p-3 shadow-sm">
                    <div className="text-xs font-bold text-slate-600">{scenario.label}</div>
                    <div className="mt-1 text-lg font-black tabular-nums text-rose-700">
                      {scenario.return_percent.toFixed(2)}%
                    </div>
                    <div className="mt-1 text-xs text-slate-600">
                      按当前金额估算损失 {money.format(scenario.estimated_loss_yuan)}
                    </div>
                    <div className="mt-1 text-[10px] text-slate-400">
                      历史区间 {scenarioPeriod(scenario)}
                    </div>
                  </div>
                ))}
              </div>
              <p className="mt-2 text-[11px] leading-5 text-slate-500">
                共同样本 {stress.data.sample.common_return_days} 个交易日 · 当前持仓金额
                {" "}{money.format(stress.data.sample.total_current_holding_amount_yuan)}
              </p>
            </>
          )}
        </section>
      ) : null}

      {fees.data ? (
        <section className="rounded-2xl border border-slate-200 bg-white p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-black text-slate-900">逐笔实际手续费证据</h3>
            <span className="text-xs font-bold text-slate-600">
              {fees.data.known_fee_coverage_percent == null
                ? "尚无已确认交易"
                : `覆盖 ${fees.data.known_fee_coverage_percent.toFixed(1)}%`}
            </span>
          </div>
          <p className="mt-2 text-xs leading-5 text-slate-600">
            已确认 {fees.data.confirmed_transaction_count} 笔，其中
            {" "}{fees.data.known_fee_transaction_count} 笔记录了原平台实际手续费，
            {fees.data.unknown_fee_transaction_count} 笔仍未知。
          </p>
          {fees.data.total_recorded_fee_yuan != null ? (
            <p className="mt-1 text-xs leading-5 text-slate-600">
              已知子样本累计手续费 {money.format(fees.data.total_recorded_fee_yuan)}
              {fees.data.weighted_recorded_fee_percent != null
                ? ` · 相对已知交易金额 ${fees.data.weighted_recorded_fee_percent.toFixed(3)}%`
                : ""}
            </p>
          ) : null}
          <p className="mt-1 text-[11px] leading-5 text-slate-500">
            新增或同步交易时可选填实际手续费；未知请留空，系统不会按 0。历史费用只用于核账，
            不会直接外推为新基金的未来渠道费率。
          </p>
        </section>
      ) : null}
    </div>
  );
}
