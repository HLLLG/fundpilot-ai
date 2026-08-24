import type { RefreshSectorQuotesResult } from "@/lib/api";

/** 板块行情拉取时间（后端 UTC ISO → 本地 HH:mm，供持仓校对展示） */
// 曾经这里有 isRoutineSectorRefreshMessage()，用来判断"这条刷新统计文案是否例行、
// 可以不打扰用户"。现在刷新结果一律不再弹全局提示（失败走行内 refreshError，
// 成功侧的口径说明由持仓看板的披露承载），这个过滤器已无调用方。

export function buildSectorRefreshNotice(
  result?: RefreshSectorQuotesResult | null,
): { tone: "amber" | "blue" | "slate"; title: string; description: string } | null {
  if (!result) {
    return null;
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
