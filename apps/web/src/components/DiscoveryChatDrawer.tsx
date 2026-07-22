"use client";

import { useId, useRef, type MouseEvent } from "react";
import { MessageCircle, X } from "lucide-react";
import { DiscoveryChatPanel } from "@/components/DiscoveryChatPanel";
import { useDialogA11y } from "@/lib/useDialogA11y";

type DiscoveryChatDrawerProps = {
  open: boolean;
  onClose: () => void;
  reportId: string;
  reportTitle?: string;
  id?: string;
};

export function DiscoveryChatDrawer({
  open,
  onClose,
  reportId,
  reportTitle,
  id,
}: DiscoveryChatDrawerProps) {
  const titleId = useId();
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useDialogA11y<HTMLDivElement>({
    open,
    onClose,
    initialFocusRef: closeButtonRef,
  });

  if (!open) {
    return null;
  }

  const handleBackdropClick = (event: MouseEvent<HTMLDivElement>) => {
    if (event.target === event.currentTarget) {
      onClose();
    }
  };

  return (
    <div
      className="report-chat-backdrop fixed inset-0 z-[80] bg-slate-950/40 backdrop-blur-[2px]"
      data-testid="discovery-chat-backdrop"
      onClick={handleBackdropClick}
    >
      <div
        ref={dialogRef}
        id={id}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="report-chat-drawer fixed inset-0 flex min-h-0 w-full flex-col bg-white shadow-2xl outline-none sm:inset-y-0 sm:left-auto sm:right-0 sm:w-full sm:max-w-md sm:border-l sm:border-slate-200"
        data-testid="discovery-chat-drawer"
      >
        <header className="flex min-h-[72px] shrink-0 items-center gap-3 border-b border-slate-200 px-4 pb-3 pt-[max(0.75rem,env(safe-area-inset-top))] sm:px-5">
          <span className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[var(--info-bg)] text-[var(--brand-strong)]">
            <MessageCircle size={19} aria-hidden />
          </span>
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="text-base font-black text-slate-950">
              追问本次推荐
            </h2>
            {reportTitle ? (
              <p className="mt-0.5 truncate text-xs text-slate-500">{reportTitle}</p>
            ) : null}
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            onClick={onClose}
            className="touch-target inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-slate-500 transition hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--brand)] focus-visible:ring-offset-2"
            aria-label="关闭追问面板"
          >
            <X size={20} aria-hidden />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-hidden pb-[env(safe-area-inset-bottom)]">
          <DiscoveryChatPanel reportId={reportId} reportTitle={reportTitle} variant="drawer" />
        </div>
      </div>
    </div>
  );
}
