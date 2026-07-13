"use client";

import { useMemo } from "react";

import { MarketBreadthGauge } from "@/components/MarketBreadthGauge";
import { NewsPreviewPanel } from "@/components/NewsPreviewPanel";
import { RecommendationAccuracyPanel } from "@/components/RecommendationAccuracyPanel";
import { SectorSignalBacktestPanel } from "@/components/SectorSignalBacktestPanel";
import { ShadowEscalationDigestCard } from "@/components/ShadowEscalationDigestCard";
import type { Holding, InvestorProfile } from "@/lib/api";

type ReportDiagnosticsProps = {
  holdings: Holding[];
  profile: InvestorProfile;
};

export function ReportDiagnostics({ holdings, profile }: ReportDiagnosticsProps) {
  const sectorLabels = useMemo(
    () => [
      ...new Set(
        holdings
          .map((item) => item.sector_name?.trim())
          .filter((name): name is string => Boolean(name)),
      ),
    ],
    [holdings],
  );

  return (
    <div className="grid gap-4" data-testid="diagnostics-content">
      <MarketBreadthGauge compact />
      <ShadowEscalationDigestCard />
      <NewsPreviewPanel holdings={holdings} profile={profile} />
      <RecommendationAccuracyPanel />
      <SectorSignalBacktestPanel sectorLabels={sectorLabels} />
    </div>
  );
}
