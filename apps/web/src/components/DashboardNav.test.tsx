// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import { DashboardNav } from "@/components/DashboardNav";

afterEach(cleanup);

const TABS = ["holdings", "market", "discovery", "report", "me"] as const;

describe("DashboardNav", () => {
  it("puts all five tabs one tap away on mobile", () => {
    // 「发现基金」和「生成日报」曾经藏在底栏的「更多」弹层里，两个最常用的入口
    // 各多一次点击；弹层还绝对定位在底栏上方，正好和后台任务浮层抢同一块地方。
    render(<DashboardNav activeTab="holdings" onSelect={vi.fn()} />);

    for (const tab of TABS) {
      expect(screen.getByTestId(`bottom-nav-${tab}`)).toBeInTheDocument();
    }
    expect(screen.queryByRole("button", { name: /更多导航/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("selects a tab directly from the bottom nav", () => {
    const onSelect = vi.fn();
    render(<DashboardNav activeTab="holdings" onSelect={onSelect} />);

    fireEvent.click(screen.getByTestId("bottom-nav-discovery"));
    expect(onSelect).toHaveBeenCalledWith("discovery");

    fireEvent.click(screen.getByTestId("bottom-nav-report"));
    expect(onSelect).toHaveBeenCalledWith("report");

    fireEvent.click(screen.getByTestId("bottom-nav-me"));
    expect(onSelect).toHaveBeenCalledWith("me");
  });

  it("marks the active tab on both navs", () => {
    render(<DashboardNav activeTab="discovery" onSelect={vi.fn()} />);

    expect(screen.getByTestId("bottom-nav-discovery")).toHaveAttribute("aria-current", "page");
    expect(screen.getByTestId("bottom-nav-holdings")).not.toHaveAttribute("aria-current");
    // 桌面顶栏与移动底栏共用同一份标签表，两个 nav 都会渲染（可见性靠 CSS 断点），
    // 所以按名字查按钮时必须限定在某一个 nav 内。
    const [desktopNav] = screen.getAllByRole("navigation", { name: "主导航" });
    expect(within(desktopNav).getByRole("button", { name: "发现" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("keeps 我的 highlighted while 盈亏分析 is open", () => {
    render(<DashboardNav activeTab="dashboard" onSelect={vi.fn()} />);

    expect(screen.getByTestId("bottom-nav-me")).toHaveAttribute("aria-current", "page");
    const [desktopNav] = screen.getAllByRole("navigation", { name: "主导航" });
    expect(within(desktopNav).getByRole("button", { name: "我的" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(desktopNav).queryByRole("button", { name: "分析" })).not.toBeInTheDocument();
  });

  it("keeps 日报 highlighted while the history drawer is open", () => {
    render(<DashboardNav activeTab="history" onSelect={vi.fn()} />);

    expect(screen.getByTestId("bottom-nav-report")).toHaveAttribute("aria-current", "page");
  });

  it("shows unread dots on the owning tab instead of a shared 更多 badge", () => {
    render(
      <DashboardNav
        activeTab="holdings"
        reportTabUnread
        discoveryTabUnread
        onSelect={vi.fn()}
      />,
    );

    expect(screen.getByTestId("bottom-nav-badge-report")).toBeInTheDocument();
    expect(screen.getByTestId("bottom-nav-badge-discovery")).toBeInTheDocument();
    expect(screen.queryByTestId("bottom-nav-badge-holdings")).not.toBeInTheDocument();
    expect(screen.getByTestId("bottom-nav-report")).toHaveAttribute("aria-label", "日报，有新内容");
  });

  it("omits unread dots when there is nothing new", () => {
    render(<DashboardNav activeTab="holdings" onSelect={vi.fn()} />);

    for (const tab of TABS) {
      expect(screen.queryByTestId(`bottom-nav-badge-${tab}`)).not.toBeInTheDocument();
      expect(screen.getByTestId(`bottom-nav-${tab}`)).not.toHaveAttribute("aria-label");
    }
  });
});
