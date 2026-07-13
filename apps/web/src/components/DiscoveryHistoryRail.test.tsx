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

  it("bounds pagination and keeps an off-page active recommendation available exactly once", () => {
    const manyReports = Array.from({ length: 5 }, (_, index) =>
      makeReport(`discovery-${index + 1}`, `推荐${index + 1}`),
    );
    render(
      <DiscoveryHistoryRail
        reports={manyReports}
        activeReportId="discovery-5"
        initialLimit={2}
        onRefresh={vi.fn()}
        onSelect={vi.fn()}
      />,
    );

    expect(screen.getAllByTestId("discovery-history-item")).toHaveLength(3);
    expect(screen.getByText("推荐5").closest("button")).toHaveAttribute("aria-current", "true");
    expect(screen.getAllByText("推荐5")).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "再显示 2 份" }));

    expect(screen.getAllByTestId("discovery-history-item")).toHaveLength(5);
    expect(screen.getAllByText("推荐5")).toHaveLength(1);
  });

  it("reports partial batch failures and only publishes successful deletions", async () => {
    vi.mocked(deleteDiscoveryReport).mockImplementation((reportId) =>
      reportId === "discovery-2" ? Promise.reject(new Error("offline")) : Promise.resolve(),
    );
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

    fireEvent.click(screen.getByRole("button", { name: "管理" }));
    fireEvent.click(screen.getByRole("button", { name: "全选" }));
    fireEvent.click(screen.getByRole("button", { name: "删除(2)" }));
    fireEvent.click(
      within(
        screen.getByRole("alertdialog", { name: "删除选中的 2 份推荐报告？" }),
      ).getByRole("button", { name: "确认删除" }),
    );

    await waitFor(() => expect(deleteDiscoveryReport).toHaveBeenCalledTimes(2));
    expect(onDeleted).toHaveBeenCalledTimes(1);
    expect(onDeleted).toHaveBeenCalledWith("discovery-1");
    expect(onDeleted).not.toHaveBeenCalledWith("discovery-2");
    expect(onRefresh).toHaveBeenCalledOnce();
    expect(await screen.findByText(/1 份删除失败，其余已删除/)).toHaveTextContent(
      "1 份删除失败，其余已删除",
    );
    await waitFor(() => expect(screen.queryAllByRole("checkbox")).toHaveLength(0));
  });

  it("prunes selected ids when recommendations are removed externally", async () => {
    const threeReports = [...reports, makeReport("discovery-3", "推荐丙")];
    const view = render(
      <DiscoveryHistoryRail
        reports={threeReports}
        onRefresh={vi.fn()}
        onSelect={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "管理" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "选择推荐报告 推荐丙" }));
    expect(screen.getByRole("button", { name: "删除(1)" })).toBeEnabled();

    view.rerender(
      <DiscoveryHistoryRail reports={reports} onRefresh={vi.fn()} onSelect={vi.fn()} />,
    );

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "删除(0)" })).toBeDisabled(),
    );
    expect(
      screen.queryByRole("checkbox", { name: "选择推荐报告 推荐丙" }),
    ).not.toBeInTheDocument();
  });

  it("preserves full-list select-all semantics after drawer filtering", () => {
    render(
      <DiscoveryHistoryRail
        reports={reports}
        variant="drawer"
        onRefresh={vi.fn()}
        onSelect={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByRole("searchbox", { name: "搜索历史推荐" }), {
      target: { value: "推荐甲" },
    });
    fireEvent.click(screen.getByRole("button", { name: "管理" }));
    expect(screen.getAllByTestId("discovery-history-item")).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "全选" }));

    expect(screen.getByRole("button", { name: "删除(2)" })).toBeEnabled();
  });
});
