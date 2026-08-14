import type { Report } from "@/lib/api";
import {
  buildClientCacheKey,
  deleteClientCache,
  deleteClientCachesWhere,
  peekClientCacheAgeMs,
  readClientCache,
  writeClientCache,
} from "@/lib/clientCache";

/** 日报上一份完整正文：切走再回来 30 分钟内不重新拉详情。 */
export const DAILY_REPORT_DETAIL_STALE_MS = 30 * 60 * 1000;

const DETAIL_PREFIX = "daily-report-detail";
const LATEST_PREFIX = "daily-report-latest";
const LIST_PREFIX = "daily-reports-list";

type CacheUserId = number | null | undefined;

function detailCacheKey(userId: CacheUserId, reportId: string): string {
  return buildClientCacheKey(DETAIL_PREFIX, userId ?? "anon", reportId);
}

function latestCacheKey(userId: CacheUserId): string {
  return buildClientCacheKey(LATEST_PREFIX, userId ?? "anon");
}

function listCacheKey(userId: CacheUserId): string {
  return buildClientCacheKey(LIST_PREFIX, userId ?? "anon");
}

export function isDailyReportDetailCacheFresh(
  userId: CacheUserId,
  reportId: string | null | undefined,
): boolean {
  if (!reportId) {
    return false;
  }
  const ageMs = peekClientCacheAgeMs(detailCacheKey(userId, reportId), "memory");
  return ageMs != null && ageMs <= DAILY_REPORT_DETAIL_STALE_MS;
}

export function readDailyReportDetailCache(
  userId: CacheUserId,
  reportId: string | null | undefined,
): Report | null {
  if (!reportId) {
    return null;
  }
  return readClientCache<Report>(detailCacheKey(userId, reportId), -1, "memory");
}

export function writeDailyReportDetailCache(
  userId: CacheUserId,
  report: Report,
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

export function readFreshLatestDailyReport(userId: CacheUserId): Report | null {
  const reportId = readClientCache<string>(
    latestCacheKey(userId),
    DAILY_REPORT_DETAIL_STALE_MS,
    "memory",
  );
  if (!reportId || !isDailyReportDetailCacheFresh(userId, reportId)) {
    return null;
  }
  return readDailyReportDetailCache(userId, reportId);
}

export function deleteDailyReportDetailCache(
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

export function isDailyReportsListCacheFresh(userId: CacheUserId): boolean {
  const ageMs = peekClientCacheAgeMs(listCacheKey(userId), "memory");
  return ageMs != null && ageMs <= DAILY_REPORT_DETAIL_STALE_MS;
}

export function readDailyReportsListCache(userId: CacheUserId): Report[] | null {
  return readClientCache<Report[]>(listCacheKey(userId), -1, "memory");
}

export function writeDailyReportsListCache(userId: CacheUserId, reports: Report[]): void {
  writeClientCache(listCacheKey(userId), reports, "memory");
}

export function resetDailyReportCacheForTests(): void {
  deleteClientCachesWhere(
    (key) =>
      key.startsWith(`${DETAIL_PREFIX}:`) ||
      key.startsWith(`${LATEST_PREFIX}:`) ||
      key.startsWith(`${LIST_PREFIX}:`),
  );
}
