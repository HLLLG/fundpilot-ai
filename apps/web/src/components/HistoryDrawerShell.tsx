"use client";

import { ArrowLeft, X } from "lucide-react";
import { useRef, type ReactNode } from "react";

import { useDialogA11y } from "@/lib/useDialogA11y";

type HistoryDrawerShellProps = {
  open: boolean;
  title: string;
  description?: string;
  onClose: () => void;
  children: ReactNode;
  labelledById: string;
};

export function HistoryDrawerShell({
  open,
  title,
  description,
  onClose,
  children,
  labelledById,
}: HistoryDrawerShellProps) {
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useDialogA11y<HTMLElement>({
    open,
    onClose,
    initialFocusRef: closeButtonRef,
  });

  if (!open) return null;

  return (
    <div
      className="history-drawer-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledById}
        aria-describedby={description ? `${labelledById}-description` : undefined}
        className="history-drawer-shell"
      >
        <div className="history-drawer-handle" aria-hidden="true" />
        <header className="history-drawer-header">
          <button
            ref={closeButtonRef}
            type="button"
            onClick={onClose}
            className="history-drawer-back min-h-11 min-w-11"
            aria-label={`返回并关闭${title}`}
          >
            <ArrowLeft size={18} />
            <span>返回</span>
          </button>
          <div className="min-w-0 flex-1">
            <h2 id={labelledById} className="font-display truncate text-lg font-extrabold text-slate-950">
              {title}
            </h2>
            {description ? (
              <p id={`${labelledById}-description`} className="mt-0.5 text-xs leading-5 text-slate-500">
                {description}
              </p>
            ) : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="touch-target history-drawer-close"
            aria-label={`关闭${title}`}
          >
            <X size={18} />
          </button>
        </header>
        <div className="history-drawer-scroll" data-testid="history-drawer-scroll-region">
          {children}
        </div>
      </section>
    </div>
  );
}
