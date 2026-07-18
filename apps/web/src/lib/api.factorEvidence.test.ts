import { expect, it } from "vitest";

import {
  fetchEvidenceMaturityStatus as facadeEvidenceFetch,
  fetchFactorIcStatus as facadeFactorFetch,
} from "@/lib/api";
import {
  fetchEvidenceMaturityStatus as domainEvidenceFetch,
  fetchFactorIcStatus as domainFactorFetch,
} from "@/lib/api/factorEvidence";
import {
  fetchFundReturnDistribution as facadeDistributionFetch,
  fetchMarketBreadth as facadeBreadthFetch,
  fetchSectorSignalBacktest as facadeBacktestFetch,
  fetchShadowEscalationDigest as facadeShadowFetch,
} from "@/lib/api";
import {
  fetchFundReturnDistribution as domainDistributionFetch,
  fetchMarketBreadth as domainBreadthFetch,
  fetchSectorSignalBacktest as domainBacktestFetch,
  fetchShadowEscalationDigest as domainShadowFetch,
} from "@/lib/api/marketDiagnostics";
import {
  fetchPortfolioFeeEvidence as facadeFeeEvidenceFetch,
  fetchPortfolioRiskMetrics as facadeRiskMetricsFetch,
  fetchPortfolioStressTest as facadeStressFetch,
} from "@/lib/api";
import {
  fetchPortfolioFeeEvidence as domainFeeEvidenceFetch,
  fetchPortfolioRiskMetrics as domainRiskMetricsFetch,
  fetchPortfolioStressTest as domainStressFetch,
} from "@/lib/api/portfolioRisk";


it("keeps the legacy API facade bound to the factor evidence domain module", () => {
  expect(facadeEvidenceFetch).toBe(domainEvidenceFetch);
  expect(facadeFactorFetch).toBe(domainFactorFetch);
});


it("keeps the legacy API facade bound to the market diagnostics module", () => {
  expect(facadeDistributionFetch).toBe(domainDistributionFetch);
  expect(facadeBreadthFetch).toBe(domainBreadthFetch);
  expect(facadeBacktestFetch).toBe(domainBacktestFetch);
  expect(facadeShadowFetch).toBe(domainShadowFetch);
});


it("keeps the legacy API facade bound to the portfolio risk module", () => {
  expect(facadeRiskMetricsFetch).toBe(domainRiskMetricsFetch);
  expect(facadeStressFetch).toBe(domainStressFetch);
  expect(facadeFeeEvidenceFetch).toBe(domainFeeEvidenceFetch);
});
