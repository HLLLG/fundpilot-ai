"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, MessageCircle, Send, Sparkles, Zap } from "lucide-react";
import type { AnalysisMode, DiscoveryChatMessage } from "@/lib/api";
import { ChatMarkdown } from "@/components/ChatMarkdown";
import { fetchDiscoveryChatHistory, streamDiscoveryChat } from "@/lib/api";
import { loadReportChatMode, saveReportChatMode } from "@/lib/storage";

type DiscoveryChatPanelProps = {
  reportId: string;
  reportTitle?: string;
};

type LocalMessage = DiscoveryChatMessage & { pending?: boolean };

const SUGGESTED_PROMPTS = [
  "只要 ETF 联接基金可以吗？",
  "如果预算降到 3000 元怎么配？",
  "哪只风险最高，为什么？",
];

export function DiscoveryChatPanel({ reportId, reportTitle }: DiscoveryChatPanelProps) {
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [input, setInput] = useState("");
  const [chatMode, setChatMode] = useState<AnalysisMode>("fast");
  const [isLoadingHistory, setIsLoadingHistory] = useState(true);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setChatMode(loadReportChatMode("fast"));
  }, []);

  const scrollToBottom = useCallback(() => {
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, []);

  useEffect(() => {
    let cancelled = false;
    setIsLoadingHistory(true);
    void fetchDiscoveryChatHistory(reportId)
      .then((history) => {
        if (!cancelled) setMessages(history);
      })
      .catch((loadError) => {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "加载对话失败");
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoadingHistory(false);
      });
    return () => {
      cancelled = true;
    };
  }, [reportId]);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  const handleSend = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || isStreaming) return;
    setError(null);
    setIsStreaming(true);
    const draftId = `draft-${Date.now()}`;
    setMessages((prev) => [
      ...prev,
      {
        id: `user-${Date.now()}`,
        discovery_report_id: reportId,
        role: "user",
        content: trimmed,
        created_at: new Date().toISOString(),
      },
      {
        id: draftId,
        discovery_report_id: reportId,
        role: "assistant",
        content: "",
        created_at: new Date().toISOString(),
        pending: true,
      },
    ]);
    setInput("");
    try {
      await streamDiscoveryChat(reportId, trimmed, chatMode, (event) => {
        if (event.type === "token") {
          setMessages((prev) =>
            prev.map((item) =>
              item.id === draftId ? { ...item, content: item.content + event.content } : item,
            ),
          );
        }
        if (event.type === "done") {
          setMessages((prev) =>
            prev.map((item) => (item.id === draftId ? { ...event.message, pending: false } : item)),
          );
        }
        if (event.type === "error") {
          setError(event.message);
        }
      });
    } catch (sendError) {
      setError(sendError instanceof Error ? sendError.message : "发送失败");
      setMessages((prev) => prev.filter((item) => item.id !== draftId));
    } finally {
      setIsStreaming(false);
    }
  };

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="mb-3 flex items-center gap-2">
        <MessageCircle size={18} className="text-indigo-600" />
        <h3 className="text-sm font-bold text-slate-900">追问推荐报告</h3>
        {reportTitle ? (
          <span className="truncate text-xs text-slate-500">{reportTitle}</span>
        ) : null}
      </div>

      <div className="mb-3 flex flex-wrap gap-2">
        {SUGGESTED_PROMPTS.map((prompt) => (
          <button
            key={prompt}
            type="button"
            disabled={isStreaming}
            onClick={() => void handleSend(prompt)}
            className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs text-slate-600 hover:bg-slate-100 disabled:opacity-50"
          >
            {prompt}
          </button>
        ))}
      </div>

      <div
        ref={scrollRef}
        className="mb-3 max-h-64 space-y-3 overflow-y-auto rounded-xl border border-slate-100 bg-slate-50/80 p-3"
      >
        {isLoadingHistory ? (
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <Loader2 size={14} className="animate-spin" />
            加载历史对话…
          </div>
        ) : null}
        {!isLoadingHistory && messages.length === 0 ? (
          <p className="text-xs text-slate-500">可追问方向筛选、预算调整或单只基金风险。</p>
        ) : null}
        {messages.map((message) => (
          <div
            key={message.id}
            className={
              message.role === "user"
                ? "ml-8 rounded-xl bg-indigo-600 px-3 py-2 text-sm text-white"
                : "mr-8 rounded-xl bg-white px-3 py-2 text-sm text-slate-800 shadow-sm"
            }
          >
            {message.role === "assistant" ? (
              <ChatMarkdown content={message.content || (message.pending ? "…" : "")} />
            ) : (
              message.content
            )}
          </div>
        ))}
      </div>

      {error ? <p className="mb-2 text-xs text-red-600">{error}</p> : null}

      <div className="flex items-center gap-2">
        <div className="flex rounded-lg border border-slate-200 p-0.5 text-xs">
          <button
            type="button"
            onClick={() => {
              setChatMode("fast");
              saveReportChatMode("fast");
            }}
            className={`flex items-center gap-1 rounded-md px-2 py-1 ${chatMode === "fast" ? "bg-slate-900 text-white" : "text-slate-600"}`}
          >
            <Zap size={12} />
            快速
          </button>
          <button
            type="button"
            onClick={() => {
              setChatMode("deep");
              saveReportChatMode("deep");
            }}
            className={`flex items-center gap-1 rounded-md px-2 py-1 ${chatMode === "deep" ? "bg-slate-900 text-white" : "text-slate-600"}`}
          >
            <Sparkles size={12} />
            深度
          </button>
        </div>
        <input
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void handleSend(input);
            }
          }}
          placeholder="继续追问…"
          className="min-w-0 flex-1 rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-indigo-400"
          disabled={isStreaming}
        />
        <button
          type="button"
          disabled={isStreaming || !input.trim()}
          onClick={() => void handleSend(input)}
          className="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50"
          aria-label="发送"
        >
          {isStreaming ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
        </button>
      </div>
    </section>
  );
}
