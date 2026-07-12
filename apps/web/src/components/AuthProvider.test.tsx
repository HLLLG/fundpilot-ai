// @vitest-environment jsdom

import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import { AuthProvider } from "@/components/AuthProvider";

const mocks = vi.hoisted(() => ({
  pathname: "/",
  token: "saved-token" as string | null,
  replace: vi.fn(),
  fetchCurrentUser: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => mocks.pathname,
  useRouter: () => ({ replace: mocks.replace }),
}));

vi.mock("@/lib/auth", () => ({
  getAccessToken: () => mocks.token,
  saveAccessToken: (token: string) => {
    mocks.token = token;
  },
  clearAccessToken: () => {
    mocks.token = null;
  },
}));

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
    }
  },
  fetchCurrentUser: mocks.fetchCurrentUser,
}));

beforeEach(() => {
  vi.useFakeTimers();
  mocks.pathname = "/";
  mocks.token = "saved-token";
  mocks.replace.mockReset();
  mocks.fetchCurrentUser.mockReset();
  mocks.fetchCurrentUser.mockRejectedValue(new Error("offline"));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

it("keeps the public landing page usable when session recovery fails", async () => {
  render(
    <AuthProvider>
      <main>公开首页内容</main>
    </AuthProvider>,
  );

  expect(screen.getByText("公开首页内容")).toBeInTheDocument();
  await act(async () => {
    await vi.runAllTimersAsync();
  });
  expect(screen.getByText("公开首页内容")).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("公开页面仍可正常浏览");
  expect(screen.getByRole("button", { name: "重试连接" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "清除失效登录" })).toBeInTheDocument();
});

it("shows recovery actions instead of private content when session recovery fails", async () => {
  mocks.pathname = "/settings";
  render(
    <AuthProvider>
      <main>私有账号内容</main>
    </AuthProvider>,
  );

  expect(screen.getByRole("status")).toHaveTextContent("正在恢复登录状态");
  await act(async () => {
    await vi.runAllTimersAsync();
  });
  expect(screen.queryByText("私有账号内容")).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "暂时无法恢复登录" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "返回公开首页" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "清除失效登录状态" })).toBeInTheDocument();
});
