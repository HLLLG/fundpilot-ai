// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

const mocks = vi.hoisted(() => ({
  loginUser: vi.fn(),
  registerUser: vi.fn(),
  replace: vi.fn(),
  setSession: vi.fn(),
  redirect: null as string | null,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mocks.replace }),
  useSearchParams: () => ({ get: (key: string) => (key === "redirect" ? mocks.redirect : null) }),
}));

vi.mock("@/components/AuthProvider", () => ({
  useAuth: () => ({ setSession: mocks.setSession }),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    loginUser: mocks.loginUser,
    registerUser: mocks.registerUser,
  };
});

import LoginPage from "@/app/login/page";
import RegisterPage from "@/app/register/page";
import { safeLoginRedirect } from "@/lib/authRedirect";

afterEach(() => {
  cleanup();
  mocks.loginUser.mockReset();
  mocks.registerUser.mockReset();
  mocks.replace.mockReset();
  mocks.setSession.mockReset();
  mocks.redirect = null;
});

describe("authentication pages", () => {
  it("announces login failures and marks the related fields invalid", async () => {
    mocks.loginUser.mockRejectedValue(new Error("账号或密码错误"));
    render(<LoginPage />);

    fireEvent.change(screen.getByRole("textbox", { name: "邮箱" }), {
      target: { value: "user@example.com" },
    });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "bad-password" } });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("账号或密码错误");
    expect(screen.getByRole("textbox", { name: "邮箱" })).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("密码")).toHaveAttribute("aria-describedby", "login-error");
  });

  it("keeps a password mismatch local and does not submit registration", async () => {
    render(<RegisterPage />);

    fireEvent.change(screen.getByRole("textbox", { name: "邮箱" }), {
      target: { value: "user@example.com" },
    });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "password-1" } });
    fireEvent.change(screen.getByLabelText("确认密码"), { target: { value: "password-2" } });
    fireEvent.click(screen.getByRole("button", { name: /免费注册/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent("两次输入的密码不一致");
    expect(mocks.registerUser).not.toHaveBeenCalled();
  });

  it("only permits same-origin relative post-login redirects", () => {
    expect(safeLoginRedirect("/settings?from=login#account")).toBe("/settings?from=login#account");
    expect(safeLoginRedirect("https://evil.example/phish")).toBe("/");
    expect(safeLoginRedirect("//evil.example/phish")).toBe("/");
    expect(safeLoginRedirect("javascript:alert(1)")).toBe("/");
  });

  it("uses the sanitized redirect after a successful login", async () => {
    mocks.redirect = "https://evil.example/phish";
    mocks.loginUser.mockResolvedValue({
      accessToken: "token",
      user: { id: "user-1", username: "用户", userAccount: "user@example.com" },
    });
    render(<LoginPage />);
    fireEvent.change(screen.getByRole("textbox", { name: "邮箱" }), {
      target: { value: "user@example.com" },
    });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "password-1" } });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));

    await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith("/"));
  });
});
