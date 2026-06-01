"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Download, Gauge, Loader2, MessageCircle, Send, Zap } from "lucide-react";
import type { ReportChatMessage, ReportChatMode } from "@/lib/api";
import { ChatMarkdown } from "@/components/ChatMarkdown";
import { fetchReportChatHistory, fetchReportChatMarkdown, streamReportChat } from "@/lib/api";
import { loadReportChatMode, saveReportChatMode } from "@/lib/storage";

type ReportChatPanelProps = {
  reportId: string;
  reportTitle?: string;
};

type LocalMessage = ReportChatMessage & { pending?: boolean };

const SUGGESTED_PROMPTS = [
  "哪只基金风险最高？",
  "如果今天收盘前只能动一只，优先哪只？",
  "新闻里对持仓影响最大的是哪条？",
];

export function ReportChatPanel({ reportId, reportTitle }: ReportChatPanelProps) {
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [input, setInput] = useState("");
  const [chatMode, setChatMode] = useState<ReportChatMode>("fast");
  const [statusHint, setStatusHint] = useState<string | null>(null);
  const [isLoadingHistory, setIsLoadingHistory] = useState(true);
  const [isStreaming, setIsStreaming] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const draftAssistantId = useRef<string | null>(null);

  useEffect(() => {
    setChatMode(loadReportChatMode("fast"));
  }, []);

  const scrollToBottom = useCallback(() => {
    const node = scrollRef.current;
    if (node) {
      node.scrollTop = node.scrollHeight;
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    setIsLoadingHistory(true);
    setError(null);
    void fetchReportChatHistory(reportId)
      .then((history) => {
        if (!cancelled) {
          setMessages(history);
        }
      })
      .catch((loadError) => {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "加载对话失败");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setIsLoadingHistory(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [reportId]);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  const handleModeChange = (mode: ReportChatMode) => {
    setChatMode(mode);
    saveReportChatMode(mode);
  };

  const sendMessage = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || isStreaming) {
      return;
    }
    setError(null);
    setStatusHint(null);
    setIsStreaming(true);
    setInput("");
    draftAssistantId.current = null;

    try {
      await streamReportChat(reportId, trimmed, chatMode, {
        onUserMessage: (message) => {
          draftAssistantId.current = `pending-assistant-${message.id}`;
          setMessages((prev) => [
            ...prev,
            message,
            {
              id: draftAssistantId.current!,
              report_id: reportId,
              role: "assistant",
              content: "",
              created_at: new Date().toISOString(),
              pending: true,
            },
          ]);
        },
        onStatus: (content) => setStatusHint(content),
        onToken: (chunk) => {
          setStatusHint(null);
          const assistantId = draftAssistantId.current;
          if (!assistantId) {
            return;
          }
          setMessages((prev) =>
            prev.map((item) =>
              item.id === assistantId ? { ...item, content: item.content + chunk } : item,
            ),
          );
        },
        onDone: (message) => {
          const assistantId = draftAssistantId.current;
          draftAssistantId.current = null;
          setStatusHint(null);
          setMessages((prev) =>
            prev.filter((item) => item.id !== assistantId).concat(message),
          );
        },
        onError: (message) => setError(message),
      });
    } catch (sendError) {
      setError(sendError instanceof Error ? sendError.message : "发送失败");
      draftAssistantId.current = null;
    } finally {
      setIsStreaming(false);
    }
  };

  const handleExportMarkdown = async () => {
    setIsExporting(true);
    setError(null);
    try {
      const markdown = await fetchReportChatMarkdown(reportId);
      const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      const slug = (reportTitle || "fund-report").replace(/[^\w\u4e00-\u9fff-]+/g, "-");
      anchor.download = `${slug}-chat.md`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (exportError) {
      setError(exportError instanceof Error ? exportError.message : "导出对话失败");
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <div
      className="flex h-[min(88vh,920px)] min-h-[680px] flex-col rounded-[20px] border border-slate-200 bg-slate-50/80 xl:min-h-[720px]"
      data-testid="report-chat-panel"
    >
      <div className="border-b border-slate-200 px-3 py-3">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <MessageCircle size={16} className="text-blue-600" />
            <span className="text-sm font-black text-slate-950">追问助手</span>
          </div>
          <button
            type="button"
            onClick={() => void handleExportMarkdown()}
            disabled={isExporting || isLoadingHistory}
            className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2 py-1 text-[11px] font-bold text-slate-600 transition hover:border-blue-300 hover:text-blue-700 disabled:opacity-50"
          >
            <Download size={12} />
            {isExporting ? "导出中" : "导出对话"}
          </button>
        </div>
        <div className="mt-2 grid grid-cols-2 gap-1.5">
          <button
            type="button"
            disabled={isStreaming}
            onClick={() => handleModeChange("fast")}
            className={`flex items-center gap-1.5 rounded-xl px-2 py-1.5 text-left text-[11px] font-bold transition ${
              chatMode === "fast"
                ? "bg-amber-500 text-white"
                : "bg-white text-slate-600 hover:bg-amber-50"
            }`}
          >
            <Zap size={12} />
            <span>
              快速
              <span
                className={`block text-[9px] font-semibold ${chatMode === "fast" ? "text-amber-100" : "text-slate-400"}`}
              >
                Flash
              </span>
            </span>
          </button>
          <button
            type="button"
            disabled={isStreaming}
            onClick={() => handleModeChange("deep")}
            className={`flex items-center gap-1.5 rounded-xl px-2 py-1.5 text-left text-[11px] font-bold transition ${
              chatMode === "deep"
                ? "bg-blue-600 text-white"
                : "bg-white text-slate-600 hover:bg-blue-50"
            }`}
          >
            <Gauge size={12} />
            <span>
              深度
              <span
                className={`block text-[9px] font-semibold ${chatMode === "deep" ? "text-blue-100" : "text-slate-400"}`}
              >
                Pro · 可拉新闻
              </span>
            </span>
          </button>
        </div>
      </div>

      <div
        ref={scrollRef}
        className="min-h-0 flex-1 space-y-3 overflow-y-auto overscroll-contain px-3 py-3"
      >
        {isLoadingHistory ? (
          <div className="flex items-center justify-center gap-2 py-8 text-sm text-slate-500">
            <Loader2 size={16} className="animate-spin" />
            加载历史对话…
          </div>
        ) : null}

        {!isLoadingHistory && messages.length === 0 ? (
          <div className="rounded-xl border border-dashed border-slate-200 bg-white/70 px-3 py-4 text-xs leading-6 text-slate-600">
            针对上方决策建议继续提问。深度模式可在需要时调用东方财富新闻 Tool 补充最新信息。
          </div>
        ) : null}

        {messages.map((message) =>
          message.role === "user" ? (
            <div
              key={message.id}
              className="ml-4 whitespace-pre-wrap rounded-2xl bg-blue-600 px-3.5 py-3.5 text-sm leading-7 text-white"
            >
              {message.content}
            </div>
          ) : (
            <div
              key={message.id}
              className="mr-1 rounded-2xl border border-slate-200 bg-white px-4 py-3.5"
            >
              {message.content ? (
                <ChatMarkdown content={message.content} />
              ) : message.pending ? (
                <p className="text-sm leading-7 text-slate-500">思考中…</p>
              ) : null}
            </div>
          ),
        )}

        {statusHint ? (
          <p className="text-center text-[11px] text-slate-500">{statusHint}</p>
        ) : null}
      </div>

      {!isLoadingHistory && messages.length === 0 ? (
        <div className="flex flex-wrap gap-2 px-3 pb-2">
          {SUGGESTED_PROMPTS.map((prompt) => (
            <button
              key={prompt}
              type="button"
              disabled={isStreaming}
              onClick={() => void sendMessage(prompt)}
              className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs text-slate-600 transition hover:border-blue-300 hover:text-blue-700 disabled:opacity-50"
            >
              {prompt}
            </button>
          ))}
        </div>
      ) : null}

      {error ? <p className="px-3 text-xs text-rose-600">{error}</p> : null}

      <form
        className="flex gap-2 border-t border-slate-200 p-3"
        onSubmit={(event) => {
          event.preventDefault();
          void sendMessage(input);
        }}
      >
        <input
          type="text"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder={chatMode === "deep" ? "深度追问（可拉最新新闻）…" : "快速追问…"}
          disabled={isStreaming || isLoadingHistory}
          className="min-w-0 flex-1 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none ring-blue-200 focus:ring-2 disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={isStreaming || isLoadingHistory || !input.trim()}
          className="inline-flex items-center justify-center rounded-xl bg-slate-950 px-3 py-2 text-white transition hover:bg-blue-700 disabled:opacity-50"
          aria-label="发送"
        >
          {isStreaming ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
        </button>
      </form>
    </div>
  );
}
