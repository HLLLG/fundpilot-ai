// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import type { Report } from "@/lib/api";
import { deleteReport } from "@/lib/api";
import { HistoryRail } from "@/components/HistoryRail";

vi.mock("@/lib/api", () => ({
  deleteReport: vi.fn(),
}));

function makeReport(id: string, title: string): Report {
  return {
    id,
    title,
    created_at: "2026-07-11T08:00:00Z",
    risk: {
      level: "medium",
      suggested_action: "watch",
      weighted_return_percent: 0,
      alerts: [],
    },
    holdings: [],
    snapshots: [],
    market_context: [],
    market_news: [],
    fund_recommendations: [],
    summary: "",
    recommendations: [],
    caveats: [],
    provider: "test",
  };
}

const reports = [makeReport("report-1", "日报甲"), makeReport("report-2", "日报乙")];

beforeEach(() => {
  vi.mocked(deleteReport).mockResolvedValue(undefined);
  vi.spyOn(window, "alert").mockImplementation(() => undefined);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  document.body.style.overflow = "";
});

describe("HistoryRail", () => {
  it("uses a keyboard-scoped alert dialog for a single destructive delete", async () => {
    const onRefresh = vi.fn();
    const onDeleted = vi.fn();
    render(
      <HistoryRail
        reports={reports}
        onRefresh={onRefresh}
        onSelect={vi.fn()}
        onDeleted={onDeleted}
      />,
    );

    const deleteTrigger = screen.getByRole("button", { name: "删除日报 日报甲" });
    deleteTrigger.focus();
    fireEvent.click(deleteTrigger);

    let dialog = screen.getByRole("alertdialog", { name: "删除这份日报？" });
    const cancelButton = within(dialog).getByRole("button", { name: "取消" });
    const confirmButton = within(dialog).getByRole("button", { name: "确认删除" });
    expect(cancelButton).toHaveFocus();
    expect(document.body.style.overflow).toBe("hidden");

    fireEvent.mouseDown(dialog);
    expect(screen.getByRole("alertdialog", { name: "删除这份日报？" })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(confirmButton).toHaveFocus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(cancelButton).toHaveFocus();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(deleteTrigger).toHaveFocus();
    expect(document.body.style.overflow).toBe("");
    expect(deleteReport).not.toHaveBeenCalled();

    fireEvent.click(deleteTrigger);
    dialog = screen.getByRole("alertdialog", { name: "删除这份日报？" });
    fireEvent.click(within(dialog).getByRole("button", { name: "确认删除" }));

    await waitFor(() => expect(deleteReport).toHaveBeenCalledWith("report-1"));
    expect(onDeleted).toHaveBeenCalledWith("report-1");
    expect(onRefresh).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("provides 44px hit targets and confirms the selected batch", async () => {
    const onRefresh = vi.fn();
    const onDeleted = vi.fn();
    render(
      <HistoryRail
        reports={reports}
        onRefresh={onRefresh}
        onSelect={vi.fn()}
        onDeleted={onDeleted}
      />,
    );

    expect(screen.getByRole("button", { name: "刷新历史日报" })).toHaveClass("h-11", "w-11");
    expect(screen.getByRole("button", { name: "删除日报 日报甲" })).toHaveClass(
      "min-h-11",
      "w-11",
    );
    expect(screen.getByText("日报甲").closest("button")).toHaveClass("min-h-11");

    const batchModeButton = screen.getByRole("button", { name: "管理" });
    expect(batchModeButton).toHaveClass("min-h-11", "min-w-11");
    fireEvent.click(batchModeButton);

    expect(screen.getByRole("button", { name: "全选" })).toHaveClass("min-h-11", "min-w-11");
    expect(screen.getByRole("button", { name: "退出批量删除" })).toHaveClass("h-11", "w-11");
    const firstCheckbox = screen.getByRole("checkbox", { name: "选择日报 日报甲" });
    expect(firstCheckbox).toHaveClass("h-5", "w-5");
    expect(firstCheckbox.closest("label")).toHaveClass("min-h-11", "min-w-11");

    fireEvent.click(firstCheckbox);
    const batchDeleteButton = screen.getByRole("button", { name: "删除(1)" });
    expect(batchDeleteButton).toHaveClass("min-h-11", "min-w-11");
    batchDeleteButton.focus();
    fireEvent.click(batchDeleteButton);

    const dialog = screen.getByRole("alertdialog", { name: "删除选中的 1 份日报？" });
    expect(within(dialog).getByText("日报甲")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "取消" })).toHaveFocus();
    fireEvent.click(within(dialog).getByRole("button", { name: "确认删除" }));

    await waitFor(() => expect(deleteReport).toHaveBeenCalledWith("report-1"));
    expect(deleteReport).toHaveBeenCalledTimes(1);
    expect(onDeleted).toHaveBeenCalledWith("report-1");
    expect(onRefresh).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryAllByRole("checkbox")).toHaveLength(0));
  });

  it("keeps delete failures as a non-blocking inline alert", async () => {
    vi.mocked(deleteReport).mockRejectedValueOnce(new Error("offline"));
    render(
      <HistoryRail reports={reports} onRefresh={vi.fn()} onSelect={vi.fn()} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "删除日报 日报甲" }));
    fireEvent.click(
      within(screen.getByRole("alertdialog", { name: "删除这份日报？" })).getByRole(
        "button",
        { name: "确认删除" },
      ),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("删除失败，请稍后重试");
    expect(screen.getByRole("button", { name: "删除日报 日报甲" })).toBeEnabled();
  });
});
