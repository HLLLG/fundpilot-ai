"use client";

import { Activity, FileText, LayoutList, PieChart, Search } from "lucide-react";
import type { DashboardTabId } from "@/lib/storage";

export type PrimaryDashboardTab = Extract<
  DashboardTabId,
  "holdings" | "dashboard" | "market" | "discovery" | "report"
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
 * 桌面与移动端共用同一份扁平标签表。
 *
 * 历史实现把「发现基金」「生成日报」藏在移动端底栏的「更多」弹层里，等于给两个最常用
 * 的入口各加了一次点击；弹层还是绝对定位在底栏上方 z-index 60，正好落在后台任务卡片
 * 的位置上互相打架。现在五个标签直接平铺，和桌面完全一致，也不再需要弹层的
 * 外部点击 / Escape / 方向键漫游焦点那一整套逻辑。
 */
const NAV_TABS: NavTab[] = [
  { id: "holdings", label: "持仓", mobileLabel: "持仓", icon: LayoutList },
  { id: "dashboard", label: "分析", mobileLabel: "分析", icon: PieChart },
  { id: "market", label: "市场", mobileLabel: "市场", icon: Activity },
  { id: "discovery", label: "发现", mobileLabel: "发现", icon: Search },
  { id: "report", label: "日报", mobileLabel: "日报", icon: FileText },
];

function isPrimaryTab(tab: DashboardTabId): tab is PrimaryDashboardTab {
  return tab !== "history";
}

/** 历史抽屉挂在日报页下，打开它时底栏仍应高亮「日报」。 */
function isTabActive(tab: PrimaryDashboardTab, activeTab: DashboardTabId): boolean {
  return tab === activeTab || (tab === "report" && activeTab === "history");
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
  const highlightedDesktop = isPrimaryTab(activeTab) ? activeTab : null;

  return (
    <>
      {/* Desktop top tabs — phones & tablets use bottom nav only */}
      <nav className="dashboard-top-nav hidden min-w-0 lg:block" aria-label="主导航">
        <div className="tab-segment overflow-x-auto">
          {NAV_TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => onSelect(tab.id)}
              aria-current={tab.id === highlightedDesktop ? "page" : undefined}
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

      {/* Mobile bottom nav — same five tabs, no overflow menu */}
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
