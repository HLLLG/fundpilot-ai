// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import { DiscoveryHistoryRail } from "@/components/DiscoveryHistoryRail";
import type { FundDiscoveryReport } from "@/lib/api";
import { deleteDiscoveryReport } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  deleteDiscoveryReport: vi.fn(),
}));

function makeReport(id: string, title: string): FundDiscoveryReport {
  return {
    id,
    title,
    created_at: "2026-07-11T08:00:00Z",
    summary: "测试推荐摘要",
    focus_sectors: [],
    target_sectors: [],
    recommendations: [],
    caveats: [],
    provider: "test",
  };
}

const reports = [makeReport("discovery-1", "推荐甲"), makeReport("discovery-2", "推荐乙")];

beforeEach(() => {
  vi.mocked(deleteDiscoveryReport).mockResolvedValue(undefined);
  vi.spyOn(window, "alert").mockImplementation(() => undefined);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  document.body.style.overflow = "";
});

describe("DiscoveryHistoryRail", () => {
  it("uses a keyboard-scoped alert dialog for a single delete", async () => {
    const onRefresh = vi.fn(async () => undefined);
    const onDeleted = vi.fn();
    render(
      <DiscoveryHistoryRail
        reports={reports}
        onRefresh={onRefresh}
        onSelect={vi.fn()}
        onDeleted={onDeleted}
      />,
    );

    const deleteTrigger = screen.getByRole("button", { name: "删除推荐报告 推荐甲" });
    deleteTrigger.focus();
    fireEvent.click(deleteTrigger);

    let dialog = screen.getByRole("alertdialog", { name: "删除这份推荐报告？" });
    const cancelButton = within(dialog).getByRole("button", { name: "取消" });
    const confirmButton = within(dialog).getByRole("button", { name: "确认删除" });
    expect(cancelButton).toHaveFocus();
    expect(document.body.style.overflow).toBe("hidden");

    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(confirmButton).toHaveFocus();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(deleteTrigger).toHaveFocus();
    expect(deleteDiscoveryReport).not.toHaveBeenCalled();

    fireEvent.click(deleteTrigger);
    dialog = screen.getByRole("alertdialog", { name: "删除这份推荐报告？" });
    fireEvent.click(within(dialog).getByRole("button", { name: "确认删除" }));

    await waitFor(() => expect(deleteDiscoveryReport).toHaveBeenCalledWith("discovery-1"));
    expect(onDeleted).toHaveBeenCalledWith("discovery-1");
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it("confirms a selected batch without a native confirm prompt", async () => {
    const onRefresh = vi.fn(async () => undefined);
    const onDeleted = vi.fn();
    const confirmSpy = vi.spyOn(window, "confirm");
    render(
      <DiscoveryHistoryRail
        reports={reports}
        onRefresh={onRefresh}
        onSelect={vi.fn()}
        onDeleted={onDeleted}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "管理" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "选择推荐报告 推荐甲" }));
    fireEvent.click(screen.getByRole("button", { name: "删除(1)" }));

    const dialog = screen.getByRole("alertdialog", {
      name: "删除选中的 1 份推荐报告？",
    });
    expect(within(dialog).getByText("推荐甲")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "取消" })).toHaveFocus();
    fireEvent.click(within(dialog).getByRole("button", { name: "确认删除" }));

    await waitFor(() => expect(deleteDiscoveryReport).toHaveBeenCalledWith("discovery-1"));
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(onDeleted).toHaveBeenCalledWith("discovery-1");
    expect(onRefresh).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryAllByRole("checkbox")).toHaveLength(0));
  });

  it("reports a delete failure inline without a blocking alert", async () => {
    vi.mocked(deleteDiscoveryReport).mockRejectedValueOnce(new Error("offline"));
    render(
      <DiscoveryHistoryRail
        reports={reports}
        onRefresh={vi.fn()}
        onSelect={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "删除推荐报告 推荐甲" }));
    fireEvent.click(
      within(screen.getByRole("alertdialog", { name: "删除这份推荐报告？" })).getByRole(
        "button",
        { name: "确认删除" },
      ),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("删除失败，请稍后重试");
    expect(screen.getByRole("button", { name: "删除推荐报告 推荐甲" })).toBeEnabled();
  });
});
