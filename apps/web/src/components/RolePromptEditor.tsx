"use client";

import { useState } from "react";
import { Eye, PencilLine } from "lucide-react";
import { ChatMarkdown } from "@/components/ChatMarkdown";

const MAX_USER_APPENDIX_LENGTH = 2000;

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
            aria-pressed={mode === id}
            className={`inline-flex min-h-11 items-center gap-1 rounded-lg px-2.5 text-[11px] font-bold transition ${
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
          {value ? (
            <ChatMarkdown content={value} />
          ) : (
            <p className="text-xs italic text-slate-500">
              未添加分析偏好；系统将使用内置安全契约。
            </p>
          )}
        </div>
      ) : (
        <label className="block">
          <span className="sr-only">大模型分析偏好附录</span>
          <textarea
            value={value}
            onChange={(event) => onChange(event.target.value)}
            maxLength={MAX_USER_APPENDIX_LENGTH}
            rows={12}
            data-testid="analysis-role-prompt"
            className="w-full resize-y rounded-lg border border-slate-200 bg-white px-3 py-2 font-mono text-xs leading-5 text-slate-800 outline-none focus:border-[var(--brand)]"
            placeholder="例如：优先说明回撤与费用；结论先行、语言简洁。不能修改系统事实、动作、金额或输出格式。"
          />
        </label>
      )}

      <div className="mt-2 flex items-center justify-between gap-2 text-[11px] text-slate-500">
        <span>
          {mode === "preview"
            ? "这里只预览用户偏好附录；系统事实、动作、金额、引用与 JSON 契约始终由服务端固定。"
            : "附录只影响表达与关注角度；冲突内容会被忽略，旧版自定义全文也会按附录安全包裹。"}
        </span>
        <span className="shrink-0 tabular-nums">
          {value.length}/{MAX_USER_APPENDIX_LENGTH}
        </span>
      </div>
    </div>
  );
}
