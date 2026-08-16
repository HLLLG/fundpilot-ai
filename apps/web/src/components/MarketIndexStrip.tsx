"use client";

import type { ReactNode } from "react";
import type { CnIndexOverview, UsMarketSnapshot } from "@/lib/api";
import { formatCnIndexCaption } from "@/lib/cnIndexOverview";
import { formatUsIndexCaption } from "@/lib/usMarketOverview";
import {
  formatIndexChange,
  formatIndexPercent,
  formatIndexPrice,
  indexTone,
  toCnIndexCards,
  toUsIndexCards,
  type IndexStripCard,
} from "@/lib/marketIndexStrip";

type MarketIndexStripProps = {
  cnData: CnIndexOverview | null;
  usData: UsMarketSnapshot | null;
  cnLoading: boolean;
  usLoading: boolean;
};

const TONE_CLASS = {
  up: "market-index-card--up",
  down: "market-index-card--down",
  flat: "market-index-card--flat",
} as const;

function IndexCard({ card }: { card: IndexStripCard }) {
  const tone = indexTone(card.changePercent ?? card.change, card.status);
  const unavailable = card.status === "unavailable";

  return (
    <article className={`market-index-card ${TONE_CLASS[tone]}`} data-testid={`index-card-${card.key}`}>
      <h3 className="market-index-card-name">{card.name}</h3>
      {unavailable ? (
        <p className="market-index-card-empty">暂不可用</p>
      ) : (
        <>
          <p className="market-index-card-price">{formatIndexPrice(card.lastPrice, card.priceDigits)}</p>
          <p className="market-index-card-change">
            <span>{formatIndexChange(card.change, card.changeDigits)}</span>
            <span>{formatIndexPercent(card.changePercent)}</span>
          </p>
        </>
      )}
    </article>
  );
}

function SkeletonCard({ index }: { index: number }) {
  return (
    <article className="market-index-card market-index-card--flat" aria-hidden data-testid={`index-card-skeleton-${index}`}>
      <div className="mx-auto h-3 w-12 rounded bg-slate-200/80" />
      <div className="mx-auto mt-2 h-5 w-16 rounded bg-slate-200/80" />
      <div className="mx-auto mt-2 h-3 w-20 rounded bg-slate-200/70" />
    </article>
  );
}

function IndexRow({
  kicker,
  meta,
  stripClassName,
  children,
}: {
  kicker: string;
  meta?: string | null;
  stripClassName: string;
  children: ReactNode;
}) {
  return (
    <div className="market-index-group">
      <p className="market-index-caption">
        <span className="market-index-kicker">{kicker}</span>
        {meta ? <span>{meta}</span> : null}
      </p>
      <div className={`market-index-strip ${stripClassName}`} role="list">
        {children}
      </div>
    </div>
  );
}

export function MarketIndexStrip({ cnData, usData, cnLoading, usLoading }: MarketIndexStripProps) {
  const cnCards = toCnIndexCards(cnData?.items);
  const usCards = toUsIndexCards(usData?.futures);
  const showCnSkeleton = cnLoading && cnCards.length === 0;
  const showUsSkeleton = usLoading && usCards.length === 0;
  const showUsRow = showUsSkeleton || usCards.length > 0;
  const usMeta = usData ? formatUsIndexCaption(usData) : null;

  return (
    <section className="market-index-board" aria-label="主要指数">
      <IndexRow kicker="A股" meta={formatCnIndexCaption(cnData)} stripClassName="market-index-strip--cn">
        {showCnSkeleton
          ? Array.from({ length: 5 }, (_, index) => <SkeletonCard key={`cn-${index}`} index={index} />)
          : cnCards.map((card) => <IndexCard key={card.key} card={card} />)}
      </IndexRow>

      {showUsRow ? (
        <IndexRow kicker="美股" meta={usMeta || null} stripClassName="market-index-strip--us">
          {showUsSkeleton
            ? Array.from({ length: 3 }, (_, index) => <SkeletonCard key={`us-${index}`} index={index + 7} />)
            : usCards.map((card) => <IndexCard key={card.key} card={card} />)}
        </IndexRow>
      ) : null}
    </section>
  );
}
