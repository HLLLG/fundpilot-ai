// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FactorIcStatusBadge } from "@/components/FactorIcStatusBadge";


function jsonResponse(payload: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  } as Response;
}


afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});


describe("FactorIcStatusBadge", () => {
  it("renders a quiet loading state", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => undefined)));
    render(<FactorIcStatusBadge />);
    expect(screen.getByRole("status")).toHaveTextContent("IC 回测加载中");
  });

  it("renders a fresh snapshot and uses the authenticated API helper", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        available: true,
        run_date: "2026-07-10",
        age_days: 0,
        stale: false,
        stale_after_days: 30,
        source: "database",
        universe_size: 300,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<FactorIcStatusBadge />);

    expect(await screen.findByText("IC 回测：7月10日 · 300只基金")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/api/diagnostics/factor-ic-status",
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("renders an explicit stale warning", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          available: true,
          run_date: "2026-05-01",
          age_days: 70,
          stale: true,
          stale_after_days: 30,
          source: "database",
          universe_size: 300,
        }),
      ),
    );

    render(<FactorIcStatusBadge />);

    expect(await screen.findByText("IC 回测已超过30天，系统将继续自动重试")).toBeInTheDocument();
  });

  it("distinguishes an unavailable snapshot", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          available: false,
          stale_after_days: 30,
          source: "unavailable",
        }),
      ),
    );

    render(<FactorIcStatusBadge />);

    expect(await screen.findByText("IC 回测数据未接入")).toBeInTheDocument();
  });

  it("distinguishes request errors from unavailable data", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ detail: "offline" }, 503)),
    );

    render(<FactorIcStatusBadge />);

    await waitFor(() => {
      expect(screen.getByText("IC 状态暂不可用")).toBeInTheDocument();
    });
  });
});
