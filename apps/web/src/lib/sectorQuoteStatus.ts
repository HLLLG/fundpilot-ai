import type { RefreshSectorQuotesResult, SectorQuoteMeta } from "@/lib/api";

export function isEstimateFallbackMeta(meta?: SectorQuoteMeta | null): boolean {
  return meta?.provider === "tiantian-fund-estimate";
}

/** 板块行情拉取时间（后端 UTC ISO → 本地 HH:mm，供持仓校对展示） */
export function formatSectorQuoteFetchedAt(iso: string | null | undefined): string | null {
  if (!iso?.trim()) {
    return null;
  }
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso.length >= 16 ? iso.slice(11, 16) : null;
  }
  return date.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

/** 后台自动/手动刷新成功后的统计文案，无需打扰用户。 */
export function isRoutineSectorRefreshMessage(message: string | null | undefined): boolean {
  if (!message?.trim()) {
    return false;
  }
  return message.startsWith("已刷新 ") || message.startsWith("已用上次快照更新 ");
}

export function sectorQuoteBadgeLabel(meta?: SectorQuoteMeta | null): string | null {
  if (!meta) {
    return null;
  }
  if (isEstimateFallbackMeta(meta)) {
    return "估值兜底";
  }
  if (meta.source === "live") {
    return "实时板块";
  }
  if (meta.confidence === "low") {
    return "待选映射";
  }
  if (meta.confidence === "none") {
    return "未匹配";
  }
  return "OCR";
}

export function buildSectorRefreshNotice(
  result?: RefreshSectorQuotesResult | null,
): { tone: "amber" | "blue" | "slate"; title: string; description: string } | null {
  if (!result) {
    return null;
  }

  const estimateFallback = result.summary.estimate_fallback ?? 0;
  const boardMatched = result.summary.board_matched ?? 0;
  if (estimateFallback > 0) {
    const partialReal =
      boardMatched > 0 &&
      (result.provider_path === "relay_live" ||
        result.provider_path === "browser_live" ||
        result.provider_path === "eastmoney_live");
    if (partialReal) {
      return {
        tone: "amber",
        title: "部分基金仍使用天天基金估值兜底",
        description: `${boardMatched} 只已用真实关联板块涨跌，${estimateFallback} 只仍未匹配到板块行情，已改用天天基金估值补位。可运行诊断脚本检查东财/中继/浏览器链路。`,
      };
    }
    return {
      tone: "amber",
      title: "当前使用天天基金估值兜底",
      description:
        "这次刷新里有基金没有取到真实关联板块涨跌，已改用天天基金估值补位。它刷新更稳更快，但不等同于真实板块行情；系统仍会优先尝试东财直连、服务端中继和浏览器命令链路。",
    };
  }

  if (result.provider_path === "stale_cache") {
    return {
      tone: "blue",
      title: "当前显示的是上次可用快照",
      description: "本次没有取到新的实时板块行情，所以保留了上次可用快照数据。你仍然可以继续校对持仓和生成日报。",
    };
  }

  if (result.provider_path === "relay_live") {
    return {
      tone: "blue",
      title: "当前通过服务端中继刷新真实板块行情",
      description: "这次板块涨跌来自中继/转发链路，适合 PC 直连东财受限的网络环境。",
    };
  }

  if (result.provider_path === "browser_live") {
    return {
      tone: "blue",
      title: "当前通过浏览器命令刷新真实板块行情",
      description: "这次板块涨跌来自浏览器态命令链路，适合接入本机浏览器会话或自动化抓取脚本。",
    };
  }

  return null;
}
