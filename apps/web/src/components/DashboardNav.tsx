"use client";

import {
  Activity,
  FileText,
  LayoutList,
  MoreHorizontal,
  PieChart,
  Search,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
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

const DESKTOP_TABS: Array<{ id: PrimaryDashboardTab; label: string }> = [
  { id: "holdings", label: "持仓" },
  { id: "dashboard", label: "分析" },
  { id: "market", label: "市场" },
  { id: "discovery", label: "发现" },
  { id: "report", label: "日报" },
];

const MOBILE_PRIMARY: Array<{
  id: PrimaryDashboardTab;
  label: string;
  icon: typeof LayoutList;
}> = [
  { id: "holdings", label: "持仓", icon: LayoutList },
  { id: "dashboard", label: "分析", icon: PieChart },
  { id: "market", label: "市场", icon: Activity },
];

const MOBILE_MORE: Array<{
  id: PrimaryDashboardTab;
  label: string;
  icon: typeof LayoutList;
}> = [
  { id: "discovery", label: "发现基金", icon: Search },
  { id: "report", label: "生成日报", icon: FileText },
];

function isPrimaryTab(tab: DashboardTabId): tab is PrimaryDashboardTab {
  return tab !== "history";
}

function isMobileMoreActive(activeTab: DashboardTabId): boolean {
  return activeTab === "discovery" || activeTab === "report" || activeTab === "history";
}

export function DashboardNav({
  activeTab,
  reportTabUnread = false,
  discoveryTabUnread = false,
  onSelect,
}: DashboardNavProps) {
  const moreMenuUnread = reportTabUnread || discoveryTabUnread;
  const [moreOpen, setMoreOpen] = useState(false);
  const moreRef = useRef<HTMLDivElement>(null);
  const moreTriggerRef = useRef<HTMLButtonElement>(null);
  const moreMenuRef = useRef<HTMLDivElement>(null);

  const highlightedDesktop = isPrimaryTab(activeTab) ? activeTab : null;

  useEffect(() => {
    if (!moreOpen) {
      return;
    }
    const trigger = moreTriggerRef.current;
    const onPointerDown = (event: MouseEvent) => {
      if (!moreRef.current?.contains(event.target as Node)) {
        setMoreOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMoreOpen(false);
        return;
      }
      if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
        return;
      }
      const items = Array.from(
        moreMenuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [],
      );
      if (!items.length) {
        return;
      }
      event.preventDefault();
      const currentIndex = items.indexOf(document.activeElement as HTMLElement);
      if (event.key === "Home") {
        items[0].focus();
      } else if (event.key === "End") {
        items[items.length - 1].focus();
      } else {
        const delta = event.key === "ArrowDown" ? 1 : -1;
        const nextIndex = currentIndex < 0 ? 0 : (currentIndex + delta + items.length) % items.length;
        items[nextIndex].focus();
      }
    };
    moreMenuRef.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
      if (trigger?.isConnected) {
        trigger.focus();
      }
    };
  }, [moreOpen]);

  return (
    <>
      {/* Desktop top tabs — phones & tablets use bottom nav only */}
      <nav className="dashboard-top-nav hidden min-w-0 lg:block" aria-label="主导航">
        <div className="tab-segment overflow-x-auto">
          {DESKTOP_TABS.map((tab) => (
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

      {/* Mobile bottom nav */}
      <nav className="dashboard-bottom-nav" aria-label="主导航">
        {MOBILE_PRIMARY.map(({ id, label, icon: Icon }) => {
          const active = activeTab === id;
          return (
            <button
              key={id}
              type="button"
              onClick={() => onSelect(id)}
              aria-current={active ? "page" : undefined}
              className="dashboard-bottom-nav-btn"
            >
              <Icon size={20} strokeWidth={active ? 2.5 : 2} />
              <span>{label}</span>
            </button>
          );
        })}

        <div ref={moreRef} className="relative flex flex-1">
          <button
            ref={moreTriggerRef}
            type="button"
            onClick={() => setMoreOpen((v) => !v)}
            aria-current={isMobileMoreActive(activeTab) ? "page" : undefined}
            aria-expanded={moreOpen}
            aria-haspopup="menu"
            aria-controls="dashboard-more-menu"
            aria-label={moreMenuUnread ? "更多导航，有新内容" : "更多导航"}
            className="dashboard-bottom-nav-btn relative w-full"
          >
            {moreMenuUnread ? (
              <span
                className="absolute right-5 top-1 h-2 w-2 rounded-full bg-[var(--danger-icon)]"
                data-testid="more-tab-badge-mobile"
                aria-hidden
              />
            ) : null}
            <MoreHorizontal size={20} strokeWidth={isMobileMoreActive(activeTab) ? 2.5 : 2} />
            <span>更多</span>
            {moreMenuUnread ? <span className="sr-only">有新内容</span> : null}
          </button>

          {moreOpen ? (
            <div
              ref={moreMenuRef}
              id="dashboard-more-menu"
              className="dashboard-more-sheet"
              role="menu"
              aria-label="更多页面"
            >
              {MOBILE_MORE.map(({ id, label, icon: Icon }) => (
                <button
                  key={id}
                  type="button"
                  role="menuitem"
                  className="dashboard-more-item"
                  onClick={() => {
                    setMoreOpen(false);
                    onSelect(id);
                  }}
                >
                  <Icon size={18} strokeWidth={2.2} />
                  <span>{label}</span>
                </button>
              ))}
            </div>
          ) : null}
        </div>
      </nav>
    </>
  );
}
