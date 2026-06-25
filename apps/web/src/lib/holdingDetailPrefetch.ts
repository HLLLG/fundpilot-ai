import type { Holding, PortfolioSummary, SectorQuoteMeta } from "@/lib/api";
import { fetchHoldingDetail } from "@/lib/api";
import { displayableHoldings } from "@/lib/holdingMetrics";
import {
  isHoldingDetailCacheFresh,
  writeHoldingDetailCache,
} from "@/lib/holdingDetailCache";

const PREFETCH_STAGGER_MS = 450;

function resolveHoldingIndex(holdings: Holding[], target: Holding): number {
  const code = target.fund_code;
  const name = target.fund_name;
  return holdings.findIndex(
    (item) =>
      (code && item.fund_code === code) ||
      (!code && item.fund_name === name),
  );
}

/** 持仓列表加载后，低优先级 stagger 预拉各基金详情（命中则跳过）。 */
export function scheduleHoldingsDetailPrefetch(options: {
  userId: number | null | undefined;
  holdings: Holding[];
  portfolioSummary?: PortfolioSummary | null;
  sectorMetaByFundCode?: Record<string, SectorQuoteMeta | undefined>;
}): () => void {
  const { userId, holdings, portfolioSummary, sectorMetaByFundCode = {} } = options;
  const candidates = displayableHoldings(holdings).filter(
    (holding) => holding.fund_code && holding.fund_code !== "000000",
  );

  if (candidates.length === 0) {
    return () => {};
  }

  let cancelled = false;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let cursor = 0;

  const runNext = () => {
    if (cancelled) {
      return;
    }
    while (cursor < candidates.length) {
      const holding = candidates[cursor];
      cursor += 1;
      const fundCode = holding.fund_code;
      if (!fundCode) {
        continue;
      }
      if (isHoldingDetailCacheFresh(userId, fundCode)) {
        continue;
      }
      const index = resolveHoldingIndex(holdings, holding);
      if (index < 0) {
        continue;
      }
      void fetchHoldingDetail({
        holdings,
        index,
        portfolio_summary: portfolioSummary,
        sector_quote_meta: sectorMetaByFundCode[fundCode] ?? null,
      })
        .then((detail) => {
          if (cancelled || !detail.holding.fund_code) {
            return;
          }
          writeHoldingDetailCache(userId, detail.holding.fund_code, detail);
        })
        .catch(() => {
          // 预取失败静默忽略，用户点开时再重试
        })
        .finally(() => {
          if (!cancelled) {
            timer = setTimeout(runNext, PREFETCH_STAGGER_MS);
          }
        });
      return;
    }
  };

  timer = setTimeout(runNext, 800);

  return () => {
    cancelled = true;
    if (timer !== null) {
      clearTimeout(timer);
    }
  };
}
