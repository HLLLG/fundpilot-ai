// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

vi.mock("@/components/AuthProvider", () => ({
  useAuth: () => ({
    user: {
      id: 1,
      userRole: "user",
      username: "河图",
      userAccount: "he@example.com",
      bio: "",
      avatarUrl: "",
    },
  }),
}));

import { UserMenu } from "@/components/UserMenu";

afterEach(() => {
  cleanup();
});

it("opens 我的 instead of a dropdown", () => {
  const onOpenMe = vi.fn();
  render(<UserMenu onOpenMe={onOpenMe} />);

  const trigger = screen.getByRole("button", { name: "打开我的" });
  expect(screen.getByText("河图")).toBeInTheDocument();
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  fireEvent.click(trigger);
  expect(onOpenMe).toHaveBeenCalledTimes(1);
});
