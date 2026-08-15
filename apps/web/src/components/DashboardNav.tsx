"use client";

import { Activity, FileText, LayoutList, Search, UserRound } from "lucide-react";
import type { DashboardTabId } from "@/lib/storage";

export type PrimaryDashboardTab = Extract<
  DashboardTabId,
  "holdings" | "market" | "discovery" | "report" | "me"
>;

type DashboardNavProps = {
  activeTab: DashboardTabId;
  reportTabUnread?: boolean;
  discoveryTabUnread?: boolean;
  onSelect: (tab: PrimaryDashboardTab) => void;
};

type NavTab = {
  id: PrimaryDashboardTab;
  /** 桌面横排标签用的短名。 */
  label: string;
  /** 底栏图标下的名字；移动端要能在 1/5 屏宽内排开，所以统一 2 个字。 */
  mobileLabel: string;
  icon: typeof LayoutList;
};

/**
 * 桌面顶栏与移动端底栏共用同一份扁平标签表。
 * 「分析」已收进「我的 → 盈亏分析」，两端导航条目保持一致。
 */
const NAV_TABS: NavTab[] = [
  { id: "holdings", label: "持仓", mobileLabel: "持仓", icon: LayoutList },
  { id: "market", label: "市场", mobileLabel: "市场", icon: Activity },
  { id: "discovery", label: "发现", mobileLabel: "发现", icon: Search },
  { id: "report", label: "日报", mobileLabel: "日报", icon: FileText },
  { id: "me", label: "我的", mobileLabel: "我的", icon: UserRound },
];

/** 历史抽屉挂在日报下；盈亏分析挂在「我的」下，打开时底栏仍高亮所属标签。 */
function isTabActive(tab: PrimaryDashboardTab, activeTab: DashboardTabId): boolean {
  return (
    tab === activeTab ||
    (tab === "report" && activeTab === "history") ||
    (tab === "me" && activeTab === "dashboard")
  );
}

function unreadFor(
  tab: PrimaryDashboardTab,
  reportTabUnread: boolean,
  discoveryTabUnread: boolean,
): boolean {
  if (tab === "report") return reportTabUnread;
  if (tab === "discovery") return discoveryTabUnread;
  return false;
}

export function DashboardNav({
  activeTab,
  reportTabUnread = false,
  discoveryTabUnread = false,
  onSelect,
}: DashboardNavProps) {
  return (
    <>
      {/* Desktop top tabs — phones & tablets use bottom nav only */}
      <nav className="dashboard-top-nav hidden min-w-0 overflow-hidden lg:block" aria-label="主导航">
        <div className="tab-segment">
          {NAV_TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => onSelect(tab.id)}
              aria-current={isTabActive(tab.id, activeTab) ? "page" : undefined}
              className="tab-segment-btn relative !px-3"
            >
              {tab.label}
              {tab.id === "report" && reportTabUnread ? (
                <span
                  className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-[var(--danger-icon)]"
                  aria-label="有新日报"
                  data-testid="report-tab-badge"
                />
              ) : null}
              {tab.id === "discovery" && discoveryTabUnread ? (
                <span
                  className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-[var(--danger-icon)]"
                  aria-label="有新推荐报告"
                  data-testid="discovery-tab-badge"
                />
              ) : null}
            </button>
          ))}
        </div>
      </nav>

      {/* 手机底栏。定位和桌面隐藏在 globals.css，不依赖会随 chunk 热更新卸掉的
          dashboard.css，否则未定位的底栏会掉进顶栏左上角。 */}
      <nav className="dashboard-bottom-nav" aria-label="主导航">
        {NAV_TABS.map(({ id, mobileLabel, icon: Icon }) => {
          const active = isTabActive(id, activeTab);
          const unread = unreadFor(id, reportTabUnread, discoveryTabUnread);
          return (
            <button
              key={id}
              type="button"
              onClick={() => onSelect(id)}
              aria-current={active ? "page" : undefined}
              aria-label={unread ? `${mobileLabel}，有新内容` : undefined}
              className="dashboard-bottom-nav-btn relative"
              data-testid={`bottom-nav-${id}`}
            >
              <span className="dashboard-bottom-nav-icon">
                <Icon size={20} strokeWidth={active ? 2.5 : 2} />
                {unread ? (
                  <span
                    className="dashboard-bottom-nav-dot"
                    data-testid={`bottom-nav-badge-${id}`}
                    aria-hidden
                  />
                ) : null}
              </span>
              <span>{mobileLabel}</span>
            </button>
          );
        })}
      </nav>
    </>
  );
}
