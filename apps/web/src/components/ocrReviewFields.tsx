"use client";

import { Search } from "lucide-react";

export function ReviewEditRow({
  label,
  value,
  ariaLabel,
  onChange,
  inputMode,
  className,
}: {
  label: string;
  value: string;
  ariaLabel: string;
  onChange: (value: string) => void;
  inputMode?: "decimal" | "text";
  className?: string;
}) {
  return (
    <label className="mb-2 flex min-h-11 items-center gap-3 rounded-xl bg-[#f0f2f5] px-4 py-3.5 last:mb-0">
      <span className="shrink-0 text-[15px] font-medium text-slate-800">{label}</span>
      <input
        value={value}
        aria-label={ariaLabel}
        onChange={(event) => onChange(event.target.value)}
        inputMode={inputMode}
        className={`min-w-0 flex-1 bg-transparent text-right text-[14px] outline-none ${className ?? "text-slate-900"}`}
      />
    </label>
  );
}

export function FundCodeSearchButton({
  code,
  fundName,
  unresolved,
  buttonRef,
  onClick,
}: {
  code: string;
  fundName: string;
  unresolved: boolean;
  buttonRef: (node: HTMLButtonElement | null) => void;
  onClick: () => void;
}) {
  return (
    <button
      ref={buttonRef}
      type="button"
      onClick={onClick}
      aria-label={`基金代码：${fundName || code || "待匹配"}`}
      className={`inline-flex min-h-11 min-w-11 items-center gap-1 rounded-lg px-1.5 text-left text-[12px] font-semibold tabular-nums transition ${
        unresolved
          ? "text-[var(--warn-fg)] hover:bg-[var(--warn-bg)]"
          : "text-slate-500 hover:bg-slate-100 hover:text-[var(--info-fg)]"
      }`}
    >
      <span>{code || "待匹配"}</span>
      <Search size={12} strokeWidth={2.25} />
    </button>
  );
}
