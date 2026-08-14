import type { FundDiscoveryReport } from "@/lib/api";
import {
  buildClientCacheKey,
  deleteClientCache,
  deleteClientCachesWhere,
  peekClientCacheAgeMs,
  readClientCache,
  writeClientCache,
} from "@/lib/clientCache";

/** 发现页上一份完整报告：切走再回来 30 分钟内不重新拉正文。 */
export const DISCOVERY_REPORT_DETAIL_STALE_MS = 30 * 60 * 1000;

const DETAIL_PREFIX = "discovery-report-detail";
const LATEST_PREFIX = "discovery-report-latest";

type CacheUserId = number | null | undefined;

function detailCacheKey(userId: CacheUserId, reportId: string): string {
  return buildClientCacheKey(DETAIL_PREFIX, userId ?? "anon", reportId);
}

function latestCacheKey(userId: CacheUserId): string {
  return buildClientCacheKey(LATEST_PREFIX, userId ?? "anon");
}

export function isDiscoveryReportDetailCacheFresh(
  userId: CacheUserId,
  reportId: string | null | undefined,
): boolean {
  if (!reportId) {
    return false;
  }
  const ageMs = peekClientCacheAgeMs(detailCacheKey(userId, reportId), "memory");
  return ageMs != null && ageMs <= DISCOVERY_REPORT_DETAIL_STALE_MS;
}

export function readDiscoveryReportDetailCache(
  userId: CacheUserId,
  reportId: string | null | undefined,
): FundDiscoveryReport | null {
  if (!reportId) {
    return null;
  }
  return readClientCache<FundDiscoveryReport>(detailCacheKey(userId, reportId), -1, "memory");
}

export function writeDiscoveryReportDetailCache(
  userId: CacheUserId,
  report: FundDiscoveryReport,
  options?: { asLatest?: boolean },
): void {
  if (!report.id) {
    return;
  }
  writeClientCache(detailCacheKey(userId, report.id), report, "memory");
  if (options?.asLatest) {
    writeClientCache(latestCacheKey(userId), report.id, "memory");
  }
}

export function readFreshLatestDiscoveryReport(userId: CacheUserId): FundDiscoveryReport | null {
  const reportId = readClientCache<string>(
    latestCacheKey(userId),
    DISCOVERY_REPORT_DETAIL_STALE_MS,
    "memory",
  );
  if (!reportId || !isDiscoveryReportDetailCacheFresh(userId, reportId)) {
    return null;
  }
  return readDiscoveryReportDetailCache(userId, reportId);
}

export function deleteDiscoveryReportDetailCache(
  userId: CacheUserId,
  reportId: string | null | undefined,
): void {
  if (!reportId) {
    return;
  }
  deleteClientCache(detailCacheKey(userId, reportId), "memory");
  const latestId = readClientCache<string>(latestCacheKey(userId), -1, "memory");
  if (latestId === reportId) {
    deleteClientCache(latestCacheKey(userId), "memory");
  }
}

export function resetDiscoveryReportCacheForTests(): void {
  deleteClientCachesWhere(
    (key) => key.startsWith(`${DETAIL_PREFIX}:`) || key.startsWith(`${LATEST_PREFIX}:`),
  );
}
