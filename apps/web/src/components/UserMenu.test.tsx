// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

const mocks = vi.hoisted(() => ({
  logout: vi.fn(),
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock("@/components/AuthProvider", () => ({
  useAuth: () => ({
    user: { id: "user-1", username: "灵析用户", userAccount: "user@example.com" },
    logout: mocks.logout,
  }),
}));

import { UserMenu } from "@/components/UserMenu";

afterEach(() => {
  cleanup();
  mocks.logout.mockReset();
  mocks.push.mockReset();
});

describe("UserMenu", () => {
  it("moves focus through the menu and restores it on Escape", () => {
    render(<UserMenu onNavigate={vi.fn()} />);
    const trigger = screen.getByRole("button", { name: "打开账号菜单" });
    fireEvent.click(trigger);

    const settings = screen.getByRole("menuitem", { name: "账号设置" });
    const history = screen.getByRole("menuitem", { name: "历史日报" });
    const logout = screen.getByRole("menuitem", { name: "退出登录" });
    expect(settings).toHaveFocus();
    fireEvent.keyDown(document, { key: "ArrowDown" });
    expect(history).toHaveFocus();
    fireEvent.keyDown(document, { key: "End" });
    expect(logout).toHaveFocus();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("routes and invokes actions from 44px menu items", () => {
    const onNavigate = vi.fn();
    render(<UserMenu onNavigate={onNavigate} />);
    const trigger = screen.getByRole("button", { name: "打开账号菜单" });
    fireEvent.click(trigger);
    const settings = screen.getByRole("menuitem", { name: "账号设置" });
    expect(settings).toHaveClass("min-h-11");
    fireEvent.click(settings);
    expect(mocks.push).toHaveBeenCalledWith("/settings");

    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole("menuitem", { name: "历史日报" }));
    expect(onNavigate).toHaveBeenCalledWith("history");

    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole("menuitem", { name: "退出登录" }));
    expect(mocks.logout).toHaveBeenCalledTimes(1);
  });
});
