"use client";

import type { Components } from "react-markdown";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const components: Components = {
  p: ({ children }) => <p className="mb-3 last:mb-0 leading-7">{children}</p>,
  ul: ({ children }) => (
    <ul className="mb-3 list-disc space-y-1.5 pl-5 last:mb-0">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="mb-3 list-decimal space-y-1.5 pl-5 last:mb-0">{children}</ol>
  ),
  li: ({ children }) => <li className="leading-7">{children}</li>,
  strong: ({ children }) => <strong className="font-bold text-slate-900">{children}</strong>,
  em: ({ children }) => <em className="italic text-slate-800">{children}</em>,
  h1: ({ children }) => (
    <h3 className="mb-2 mt-1 text-base font-black text-slate-950">{children}</h3>
  ),
  h2: ({ children }) => (
    <h4 className="mb-2 mt-1 text-sm font-black text-slate-950">{children}</h4>
  ),
  h3: ({ children }) => (
    <h5 className="mb-1.5 mt-1 text-sm font-bold text-slate-900">{children}</h5>
  ),
  blockquote: ({ children }) => (
    <blockquote className="mb-3 border-l-4 border-[var(--info-border)] pl-3 text-slate-600 last:mb-0">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-4 border-slate-200" />,
  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="font-medium text-blue-600 underline decoration-blue-300 underline-offset-2 hover:text-[var(--info-fg)]"
    >
      {children}
    </a>
  ),
  code: ({ className, children }) => {
    const isBlock = Boolean(className);
    if (isBlock) {
      return (
        <code className="block overflow-x-auto rounded-lg bg-slate-100 px-3 py-2 text-xs leading-6 text-slate-800">
          {children}
        </code>
      );
    }
    return (
      <code className="rounded bg-slate-100 px-1.5 py-0.5 text-[0.85em] text-slate-800">
        {children}
      </code>
    );
  },
  pre: ({ children }) => (
    <pre className="mb-3 overflow-x-auto rounded-lg bg-slate-100 p-3 text-xs leading-6 last:mb-0">
      {children}
    </pre>
  ),
  table: ({ children }) => (
    <div
      className="mb-3 overflow-x-auto rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--brand)] focus-visible:ring-offset-2 last:mb-0"
      role="region"
      aria-label="对话数据表格，可左右滚动查看"
      tabIndex={0}
    >
      <table className="min-w-max w-full border-collapse text-left text-xs">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border border-slate-200 bg-slate-50 px-2 py-1.5 font-bold">{children}</th>
  ),
  td: ({ children }) => (
    <td className="border border-slate-200 px-2 py-1.5">{children}</td>
  ),
};

export type ChatMarkdownProps = {
  content: string;
};

export function ChatMarkdownRenderer({ content }: ChatMarkdownProps) {
  return (
    <div className="chat-markdown text-sm text-slate-700">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
