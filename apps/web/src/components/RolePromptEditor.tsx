"use client";

import { useState } from "react";
import { Eye, PencilLine } from "lucide-react";
import { ChatMarkdown } from "@/components/ChatMarkdown";

const MAX_ROLE_PROMPT_LENGTH = 4000;

type RolePromptMode = "preview" | "edit";

type RolePromptEditorProps = {
  value: string;
  onChange: (value: string) => void;
};

export function RolePromptEditor({ value, onChange }: RolePromptEditorProps) {
  const [mode, setMode] = useState<RolePromptMode>("preview");

  return (
    <div className="p-3">
      <div className="mb-2 flex items-center justify-end gap-1">
        {(
          [
            ["preview", "预览", Eye],
            ["edit", "编辑", PencilLine],
          ] as const
        ).map(([id, label, Icon]) => (
          <button
            key={id}
            type="button"
            onClick={() => setMode(id)}
            className={`inline-flex items-center gap-1 rounded-lg px-2.5 py-1 text-[11px] font-bold transition ${
              mode === id
                ? "bg-[var(--brand-soft)] text-[var(--brand-strong)]"
                : "text-slate-500 hover:bg-slate-100 hover:text-slate-700"
            }`}
          >
            <Icon size={12} />
            {label}
          </button>
        ))}
      </div>

      {mode === "preview" ? (
        <div
          className="role-prompt-markdown max-h-72 overflow-y-auto rounded-lg border border-slate-100 bg-gradient-to-b from-slate-50/90 to-white px-3 py-2.5 [&_.chat-markdown]:text-xs [&_.chat-markdown_h4]:text-xs [&_.chat-markdown_h5]:text-[11px] [&_.chat-markdown_li]:leading-6 [&_.chat-markdown_p]:mb-2 [&_.chat-markdown_p]:leading-6 [&_.chat-markdown_table]:text-[11px] [&_.chat-markdown_ul]:mb-2"
          data-testid="analysis-role-prompt-preview"
        >
          <ChatMarkdown content={value || "_暂无内容，请切换到编辑填写角色设定。_"} />
        </div>
      ) : (
        <label className="block">
          <span className="sr-only">大模型通用 Prompt 角色设定</span>
          <textarea
            value={value}
            onChange={(event) => onChange(event.target.value)}
            maxLength={MAX_ROLE_PROMPT_LENGTH}
            rows={12}
            data-testid="analysis-role-prompt"
            className="w-full resize-y rounded-lg border border-slate-200 bg-white px-3 py-2 font-mono text-xs leading-5 text-slate-800 outline-none focus:border-[var(--brand)]"
            placeholder="支持 Markdown：可用 ## 标题、- 列表、`代码` 等格式…"
          />
        </label>
      )}

      <div className="mt-2 flex items-center justify-between gap-2 text-[11px] text-slate-500">
        <span>
          {mode === "preview"
            ? "预览为渲染效果；修改请切到「编辑」。系统仍会追加新闻规则与 JSON 约束。"
            : "支持 Markdown 语法；切到「预览」查看排版效果。"}
        </span>
        <span className="shrink-0 tabular-nums">
          {value.length}/{MAX_ROLE_PROMPT_LENGTH}
        </span>
      </div>
    </div>
  );
}
