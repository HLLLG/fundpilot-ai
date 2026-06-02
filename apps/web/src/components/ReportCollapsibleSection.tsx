"use client";

import { useState, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";

type ReportCollapsibleSectionProps = {
  title: string;
  icon?: ReactNode;
  defaultOpen?: boolean;
  children: ReactNode;
  className?: string;
};

export function ReportCollapsibleSection({
  title,
  icon,
  defaultOpen = false,
  children,
  className = "",
}: ReportCollapsibleSectionProps) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className={`rounded-[24px] bg-white p-5 shadow-sm ${className}`}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center justify-between gap-3 text-left"
        aria-expanded={open}
      >
        <div className="flex items-center gap-2 text-sm font-black text-slate-950">
          {icon}
          {title}
        </div>
        <ChevronDown
          size={18}
          className={`shrink-0 text-slate-400 transition ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open ? <div className="mt-4">{children}</div> : null}
    </div>
  );
}
