import type { FundDiscoveryReport } from "@/lib/api";

/** 流式扫描静默多久后就认为连接已经死了。 */
export const DISCOVERY_STREAM_SILENCE_MS = 45_000;
/** 恢复检查的轮询间隔（只在页面可见时跑）。 */
export const DISCOVERY_RECOVERY_POLL_MS = 8_000;

type ReportLike = Pick<FundDiscoveryReport, "id" | "created_at">;

export function sortReportsByCreatedAtDesc<T extends ReportLike>(reports: readonly T[]): T[] {
  return [...reports].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  );
}

/**
 * 判断后台是否已经产出了一份新报告。
 *
 * 手机浏览器切到后台时会挂起 fetch 的 reader，`streamDiscovery` 那个 promise 可能永远
 * 不 settle（`finally` 不执行，`isSubmitting` 和 `streamingDiscovery` 就一直留着），
 * 而流式路径又从不登记 `discoveryJobId`，所以没有任何轮询会去问服务端"到底完了没"。
 * 结果就是页面永远停在「扫描进行中…」，而报告其实早就生成了（换台设备就能看到）。
 *
 * 这里刻意**不**比较时间戳：`created_at` 来自服务端时钟，`startedAt` 来自浏览器时钟，
 * 两者有偏差时会既漏判也误判。改成比较"扫描开始那一刻最新报告的 id"——只要列表最前面
 * 那份变了，就说明后台确实又写了一份新的。这个判断与时钟无关。
 *
 * @param reports 已按 created_at 降序排好的报告列表
 * @param knownLatestId 扫描开始时最新一份报告的 id（当时没有历史则为 null）
 */
export function detectCompletedScan<T extends ReportLike>({
  reports,
  knownLatestId,
}: {
  reports: readonly T[];
  knownLatestId: string | null;
}): T | null {
  const latest = reports[0];
  if (!latest) {
    return null;
  }
  return latest.id === knownLatestId ? null : latest;
}

/** 流最近一次有动静距今是否已经超过静默阈值。 */
export function streamLooksDead(
  lastActivityAtMs: number,
  nowMs: number,
  silenceMs = DISCOVERY_STREAM_SILENCE_MS,
): boolean {
  return nowMs - lastActivityAtMs >= silenceMs;
}
