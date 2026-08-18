"use client";

import { useRef } from "react";
import { X } from "lucide-react";
import { HoldingProfitTable } from "@/components/HoldingProfitTable";
import type { HoldingProfitPoint } from "@/lib/holdingProfitTrend";
import { useDialogA11y } from "@/lib/useDialogA11y";

type HoldingProfitHistoryModalProps = {
  fundName: string;
  points: HoldingProfitPoint[];
  onClose: () => void;
};

export function HoldingProfitHistoryModal({
  fundName,
  points,
  onClose,
}: HoldingProfitHistoryModalProps) {
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useDialogA11y<HTMLDivElement>({
    open: true,
    onClose,
    initialFocusRef: closeButtonRef,
  });

  return (
    <div
      className="modal-backdrop fixed inset-0 z-[85] flex items-end justify-center p-0 sm:items-center sm:p-4"
      onClick={onClose}
      role="presentation"
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        className="modal-sheet flex max-h-[88vh] w-full max-w-lg flex-col overflow-hidden rounded-t-[var(--radius-card)] sm:rounded-[var(--radius-card)]"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="holding-profit-history-title"
        aria-describedby="holding-profit-history-fund-name"
      >
        <div className="flex items-center justify-between border-b border-[var(--line)] px-4 py-3">
          <div className="min-w-0">
            <h2
              id="holding-profit-history-title"
              className="truncate text-base font-bold text-[var(--brand-deep)]"
            >
              历史收益
            </h2>
            <p
              id="holding-profit-history-fund-name"
              className="truncate text-xs text-[var(--muted)]"
            >
              {fundName}
            </p>
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            onClick={onClose}
            className="touch-target inline-flex items-center justify-center rounded-full text-slate-500 hover:bg-slate-100 hover:text-slate-600"
            aria-label="关闭"
          >
            <X size={18} />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          <HoldingProfitTable points={points} maxRows={points.length} />
        </div>
      </div>
    </div>
  );
}
