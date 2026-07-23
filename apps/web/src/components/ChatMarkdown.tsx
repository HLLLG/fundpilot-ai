"use client";

import dynamic from "next/dynamic";

import type { ChatMarkdownProps } from "@/components/ChatMarkdownRenderer";

const DynamicChatMarkdown = dynamic(
  () =>
    import("@/components/ChatMarkdownRenderer").then(
      (module) => module.ChatMarkdownRenderer,
    ),
  {
    ssr: false,
    loading: () => (
      <div
        aria-label="正在渲染对话内容"
        className="h-5 w-28 animate-pulse rounded bg-slate-100"
      />
    ),
  },
);

export function ChatMarkdown(props: ChatMarkdownProps) {
  return <DynamicChatMarkdown {...props} />;
}
