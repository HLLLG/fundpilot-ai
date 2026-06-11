"use client";

import { useState } from "react";
import { ChevronDown } from "lucide-react";

type DiagnosticsAccordionProps = {
  children: React.ReactNode;
};

export function DiagnosticsAccordion({ children }: DiagnosticsAccordionProps) {
  const [open, setOpen] = useState(false);

  return (
    <div className="section-card overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center justify-between px-4 py-3 text-left text-sm font-bold text-slate-600 transition hover:bg-slate-50"
      >
        <span>投研诊断工具</span>
        <ChevronDown size={16} className={`transition ${open ? "rotate-180" : ""}`} />
      </button>
      {open ? <div className="grid gap-4 border-t border-slate-100 p-4">{children}</div> : null}
    </div>
  );
}
