// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  useAuth: vi.fn(),
}));

vi.mock("@/components/AuthProvider", () => ({
  useAuth: mocks.useAuth,
}));

vi.mock("next/dynamic", () => ({
  default: () => function DashboardStub() {
    return <div>工作台内容</div>;
  },
}));

import { HomeClient } from "@/components/HomeClient";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("HomeClient authentication bootstrap", () => {
  it("keeps the public landing page hidden while an existing session is being restored", () => {
    mocks.useAuth.mockReturnValue({ user: null, loading: true });

    render(<HomeClient landing={<div>公开落地页</div>} />);

    expect(screen.getByRole("status")).toHaveTextContent("正在恢复工作台…");
    expect(screen.queryByText("公开落地页")).not.toBeInTheDocument();
  });

  it("shows the dashboard after recovery and the landing page only after anonymous bootstrap", () => {
    mocks.useAuth.mockReturnValue({ user: { id: 7 }, loading: false });
    const { rerender } = render(<HomeClient landing={<div>公开落地页</div>} />);
    expect(screen.getByText("工作台内容")).toBeInTheDocument();

    mocks.useAuth.mockReturnValue({ user: null, loading: false });
    rerender(<HomeClient landing={<div>公开落地页</div>} />);
    expect(screen.getByText("公开落地页")).toBeInTheDocument();
  });
});
