// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import { ReportChatDrawer } from "@/components/ReportChatDrawer";
import { ReportChatPanel } from "@/components/ReportChatPanel";
import type { ReportChatMessage } from "@/lib/api";
import { installMatchMedia, type MatchMediaController } from "@/test/matchMedia";

const DESKTOP_QUERY = "(min-width: 1280px)";

let matchMedia: MatchMediaController;

type StreamHandlers = Parameters<typeof import("@/lib/api").streamReportChat>[3];

const apiMocks = vi.hoisted(() => ({
  fetchReportChatHistory: vi.fn(),
  fetchReportChatMarkdown: vi.fn(),
  streamReportChat: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchReportChatHistory: apiMocks.fetchReportChatHistory,
    fetchReportChatMarkdown: apiMocks.fetchReportChatMarkdown,
    streamReportChat: apiMocks.streamReportChat,
  };
});

beforeEach(() => {
  matchMedia = installMatchMedia({ [DESKTOP_QUERY]: false });
  apiMocks.fetchReportChatHistory.mockResolvedValue([]);
  apiMocks.fetchReportChatMarkdown.mockResolvedValue("# chat");
  apiMocks.streamReportChat.mockImplementation(() => new Promise<void>(() => undefined));
  Object.defineProperty(HTMLElement.prototype, "scrollTo", {
    configurable: true,
    value: vi.fn(),
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  window.localStorage.clear();
  document.body.style.overflow = "";
  matchMedia.restore();
});

async function sendQuestion(question: string) {
  const input = screen.getByPlaceholderText("快速追问…");
  await waitFor(() => expect(input).toBeEnabled());
  fireEvent.change(input, { target: { value: question } });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  await waitFor(() => expect(apiMocks.streamReportChat).toHaveBeenCalled());
}

describe("ReportChatPanel stream lifecycle", () => {
  it("aborts the active report-chat stream when the panel unmounts", async () => {
    const view = render(<ReportChatPanel reportId="report-1" variant="drawer" />);

    await sendQuestion("第一问");
    const signal = apiMocks.streamReportChat.mock.calls[0]?.[4] as AbortSignal | undefined;
    expect(signal).toBeInstanceOf(AbortSignal);
    expect(signal?.aborted).toBe(false);

    view.unmount();

    expect(signal?.aborted).toBe(true);
  });

  it("cancels the closed drawer stream before a reopened drawer starts another", async () => {
    render(<ReportChatDrawer reportId="report-1" reportTitle="日报" />);
    const trigger = screen.getByRole("button", { name: "追问这份日报" });
    fireEvent.click(trigger);

    await sendQuestion("关闭前的问题");
    const firstSignal = apiMocks.streamReportChat.mock.calls[0]?.[4] as
      | AbortSignal
      | undefined;
    fireEvent.click(screen.getByRole("button", { name: "关闭追问助手" }));

    expect(firstSignal).toBeInstanceOf(AbortSignal);
    expect(firstSignal?.aborted).toBe(true);

    fireEvent.click(trigger);
    await sendQuestion("重开后的问题");
    const signals = apiMocks.streamReportChat.mock.calls.map((call) => call[4] as AbortSignal);

    expect(apiMocks.streamReportChat).toHaveBeenCalledTimes(2);
    expect(signals.filter((signal) => !signal.aborted)).toHaveLength(1);
  });

  it("preserves one history and stream instance across a live desktop breakpoint", async () => {
    render(<ReportChatDrawer reportId="report-1" reportTitle="日报" />);
    fireEvent.click(screen.getByRole("button", { name: "追问这份日报" }));

    await waitFor(() => expect(apiMocks.fetchReportChatHistory).toHaveBeenCalledTimes(1));
    const panel = screen.getByTestId("report-chat-panel");
    await sendQuestion("跨断点继续的问题");
    const signal = apiMocks.streamReportChat.mock.calls[0]?.[4] as AbortSignal | undefined;
    expect(signal?.aborted).toBe(false);

    act(() => matchMedia.setMatches(DESKTOP_QUERY, true));

    expect(screen.getByRole("complementary", { name: "追问这份日报" })).toBeInTheDocument();
    expect(screen.getByTestId("report-chat-panel")).toBe(panel);
    expect(apiMocks.fetchReportChatHistory).toHaveBeenCalledTimes(1);
    expect(signal?.aborted).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "关闭追问助手" }));
    expect(signal?.aborted).toBe(true);
  });

  it("aborts and resets chat state when an open drawer switches reports", async () => {
    let resolveNewHistory!: (messages: ReportChatMessage[]) => void;
    const newHistory = new Promise<ReportChatMessage[]>((resolve) => {
      resolveNewHistory = resolve;
    });
    apiMocks.fetchReportChatHistory.mockImplementation((reportId: string) =>
      reportId === "report-2" ? newHistory : Promise.resolve([]),
    );

    const view = render(<ReportChatDrawer reportId="report-1" reportTitle="旧日报" />);
    fireEvent.click(screen.getByRole("button", { name: "追问这份日报" }));
    await waitFor(() =>
      expect(apiMocks.fetchReportChatHistory).toHaveBeenNthCalledWith(1, "report-1"),
    );

    await sendQuestion("旧报告问题");
    const oldPanel = screen.getByTestId("report-chat-panel");
    const oldSignal = apiMocks.streamReportChat.mock.calls[0]?.[4] as AbortSignal | undefined;
    const oldHandlers = apiMocks.streamReportChat.mock.calls[0]?.[3] as StreamHandlers;
    act(() => {
      oldHandlers.onUserMessage?.({
        id: "old-user-message",
        report_id: "report-1",
        role: "user",
        content: "旧报告问题",
        created_at: "2026-07-11T12:00:00Z",
      });
    });
    expect(screen.getByText("旧报告问题")).toBeInTheDocument();
    expect(oldSignal?.aborted).toBe(false);

    view.rerender(<ReportChatDrawer reportId="report-2" reportTitle="新日报" />);
    await waitFor(() =>
      expect(apiMocks.fetchReportChatHistory).toHaveBeenNthCalledWith(2, "report-2"),
    );

    expect(oldSignal?.aborted).toBe(true);
    expect(screen.getByTestId("report-chat-panel")).not.toBe(oldPanel);
    expect(screen.queryByText("旧报告问题")).not.toBeInTheDocument();

    act(() => oldHandlers.onToken("旧流残片"));
    expect(screen.queryByText("旧流残片")).not.toBeInTheDocument();

    act(() => {
      resolveNewHistory([
        {
          id: "new-assistant-message",
          report_id: "report-2",
          role: "assistant",
          content: "新报告历史",
          created_at: "2026-07-11T12:01:00Z",
        },
      ]);
    });
    expect(await screen.findByText("新报告历史")).toBeInTheDocument();
    expect(apiMocks.fetchReportChatHistory).toHaveBeenCalledTimes(2);
    expect(screen.queryByText("旧报告问题")).not.toBeInTheDocument();
  });
});

describe("ReportChatPanel drawer touch targets", () => {
  it("keeps send, mode, and suggested-prompt controls at least 44px", async () => {
    render(<ReportChatPanel reportId="report-1" variant="drawer" />);

    const controls = [
      screen.getByRole("button", { name: /快速/ }),
      screen.getByRole("button", { name: /深度/ }),
      screen.getByRole("button", { name: "发送" }),
      await screen.findByRole("button", { name: "哪只基金风险最高？" }),
      screen.getByRole("button", { name: "如果今天收盘前只能动一只，优先哪只？" }),
      screen.getByRole("button", { name: "新闻里对持仓影响最大的是哪条？" }),
    ];

    for (const control of controls) {
      expect(control).toHaveClass("min-h-11", "min-w-11");
    }
  });
});
