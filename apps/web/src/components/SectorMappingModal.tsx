"use client";

import { useEffect, useRef } from "react";
import type { SectorMappingCandidate } from "@/lib/api";
import { useDialogA11y } from "@/lib/useDialogA11y";

type SectorMappingModalProps = {
  open: boolean;
  fundName: string;
  sectorName?: string | null;
  candidates: SectorMappingCandidate[];
  onClose: () => void;
  onSelect: (candidate: SectorMappingCandidate) => void;
};

const sourceLabel = {
  index: "指数",
  concept: "概念",
  industry: "行业",
};

export function SectorMappingModal({
  open,
  fundName,
  sectorName,
  candidates,
  onClose,
  onSelect,
}: SectorMappingModalProps) {
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useDialogA11y<HTMLDivElement>({
    open,
    onClose,
    initialFocusRef: closeButtonRef,
  });

  useEffect(() => {
    if (!open) {
      return;
    }
    const frame = window.requestAnimationFrame(() => closeButtonRef.current?.focus());
    return () => window.cancelAnimationFrame(frame);
  }, [fundName, open, sectorName]);

  if (!open) {
    return null;
  }

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-slate-950/40 p-4"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onClose();
        }
      }}
      role="presentation"
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="sector-mapping-title"
        aria-describedby="sector-mapping-description"
        className="max-h-[80vh] w-full max-w-lg overflow-hidden rounded-3xl bg-white shadow-2xl"
      >
        <div className="border-b border-slate-100 px-5 py-4">
          <h2 id="sector-mapping-title" className="text-lg font-black text-slate-950">
            选择板块映射
          </h2>
          <p id="sector-mapping-description" className="mt-1 text-sm text-slate-600">
            {fundName} · OCR 板块「{sectorName || "—"}」对应多个东财行情项，请选择与养基宝一致的一项。
          </p>
        </div>
        <div className="max-h-96 overflow-y-auto p-3">
          {candidates.map((candidate) => (
            <button
              key={`${candidate.source_type}-${candidate.source_name}`}
              type="button"
              onClick={() => onSelect(candidate)}
              className="mb-2 flex min-h-11 w-full items-center justify-between rounded-2xl border border-slate-200 px-4 py-3 text-left transition hover:border-blue-300 hover:bg-[var(--info-bg)]"
            >
              <div>
                <div className="text-sm font-bold text-slate-950">{candidate.source_name}</div>
                <div className="mt-0.5 text-xs text-slate-500">
                  {sourceLabel[candidate.source_type]} · 实时 {candidate.change_percent > 0 ? "+" : ""}
                  {candidate.change_percent}%
                </div>
              </div>
              <span className="text-xs font-bold text-[var(--info-fg)]">选用</span>
            </button>
          ))}
        </div>
        <div className="border-t border-slate-100 px-5 py-3 text-right">
          <button
            ref={closeButtonRef}
            type="button"
            onClick={onClose}
            className="min-h-11 rounded-xl border border-slate-200 px-4 py-2 text-sm font-bold text-slate-600 hover:bg-slate-50"
          >
            稍后
          </button>
        </div>
      </div>
    </div>
  );
}
