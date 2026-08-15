// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

const push = vi.fn();
const logout = vi.fn();
const authState = {
  user: {
    id: 1,
    userRole: "user",
    username: "河图",
    userAccount: "he@example.com",
    bio: "",
    avatarUrl: "",
  },
  logout,
};

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

vi.mock("@/components/AuthProvider", () => ({
  useAuth: () => authState,
}));

import { MePage } from "@/components/MePage";

afterEach(() => {
  cleanup();
  push.mockReset();
  logout.mockReset();
  authState.user.userRole = "user";
  vi.unstubAllGlobals();
});

it("shows the avatar, username, and opens settings from the profile row", () => {
  render(<MePage onOpenAnalysis={vi.fn()} />);

  expect(screen.getByText("河图")).toBeInTheDocument();
  expect(screen.getByText("he@example.com")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "账号设置" }));
  expect(push).toHaveBeenCalledWith("/settings");
});

it("opens the original analysis page from 盈亏分析", () => {
  const onOpenAnalysis = vi.fn();
  render(<MePage onOpenAnalysis={onOpenAnalysis} />);

  fireEvent.click(screen.getByRole("button", { name: "盈亏分析" }));
  expect(onOpenAnalysis).toHaveBeenCalledTimes(1);
});

it("hides admin destinations for ordinary users", () => {
  render(<MePage onOpenAnalysis={vi.fn()} />);

  expect(screen.queryByRole("button", { name: "用户管理中心" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "运维监控" })).not.toBeInTheDocument();
});

it("opens admin destinations from 我的", () => {
  authState.user.userRole = "admin";
  render(<MePage onOpenAnalysis={vi.fn()} />);

  fireEvent.click(screen.getByRole("button", { name: "用户管理中心" }));
  expect(push).toHaveBeenCalledWith("/admin/users");
  fireEvent.click(screen.getByRole("button", { name: "运维监控" }));
  expect(push).toHaveBeenCalledWith("/admin/ops");
});

it("asks before logging out", () => {
  vi.stubGlobal("confirm", vi.fn(() => true));
  render(<MePage onOpenAnalysis={vi.fn()} />);

  fireEvent.click(screen.getByRole("button", { name: "退出登录" }));
  expect(logout).toHaveBeenCalledTimes(1);
});
