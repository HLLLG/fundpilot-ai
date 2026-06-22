"use client";

import {
  Activity,
  FileText,
  History,
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
  onSelect: (tab: PrimaryDashboardTab) => void;
  onSelectHistory: () => void;
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
  id: PrimaryDashboardTab | "history";
  label: string;
  icon: typeof LayoutList;
}> = [
  { id: "discovery", label: "发现基金", icon: Search },
  { id: "report", label: "生成日报", icon: FileText },
  { id: "history", label: "历史日报", icon: History },
];

function isPrimaryTab(tab: DashboardTabId): tab is PrimaryDashboardTab {
  return tab !== "history";
}

function isMobileMoreActive(activeTab: DashboardTabId): boolean {
  return activeTab === "discovery" || activeTab === "report" || activeTab === "history";
}

export function DashboardNav({ activeTab, onSelect, onSelectHistory }: DashboardNavProps) {
  const [moreOpen, setMoreOpen] = useState(false);
  const moreRef = useRef<HTMLDivElement>(null);

  const highlightedDesktop = isPrimaryTab(activeTab) ? activeTab : null;

  useEffect(() => {
    if (!moreOpen) {
      return;
    }
    const onPointerDown = (event: MouseEvent) => {
      if (!moreRef.current?.contains(event.target as Node)) {
        setMoreOpen(false);
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [moreOpen]);

  return (
    <>
      {/* Desktop top tabs — phones & tablets use bottom nav only */}
      <div className="dashboard-top-nav mb-3 hidden lg:block">
        <div className="tab-segment overflow-x-auto">
          {DESKTOP_TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => onSelect(tab.id)}
              aria-current={tab.id === highlightedDesktop ? "page" : undefined}
              className="tab-segment-btn !px-3"
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

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
            type="button"
            onClick={() => setMoreOpen((v) => !v)}
            aria-current={isMobileMoreActive(activeTab) ? "page" : undefined}
            aria-expanded={moreOpen}
            className="dashboard-bottom-nav-btn w-full"
          >
            <MoreHorizontal size={20} strokeWidth={isMobileMoreActive(activeTab) ? 2.5 : 2} />
            <span>更多</span>
          </button>

          {moreOpen ? (
            <div className="dashboard-more-sheet" role="menu">
              {MOBILE_MORE.map(({ id, label, icon: Icon }) => (
                <button
                  key={id}
                  type="button"
                  role="menuitem"
                  className="dashboard-more-item"
                  onClick={() => {
                    setMoreOpen(false);
                    if (id === "history") {
                      onSelectHistory();
                      return;
                    }
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
