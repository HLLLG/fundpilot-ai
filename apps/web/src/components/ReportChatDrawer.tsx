"use client";

import { useEffect, useRef, useState } from "react";
import { MessageCircle, X } from "lucide-react";

import { ReportChatPanel } from "@/components/ReportChatPanel";

type ReportChatDrawerProps = {
  reportId: string;
  reportTitle?: string;
};

export function ReportChatDrawer({ reportId, reportTitle }: ReportChatDrawerProps) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;

    const trigger = triggerRef.current;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
      if (event.key !== "Tab" || !dialogRef.current) return;

      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      );
      if (!focusable.length) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
      trigger?.focus();
    };
  }, [open]);

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen(true)}
        className="fixed bottom-[calc(5rem+env(safe-area-inset-bottom))] right-4 z-30 inline-flex min-h-11 items-center gap-2 rounded-full bg-[var(--brand-strong)] px-4 text-sm font-black text-white shadow-lg shadow-blue-950/15 transition hover:-translate-y-0.5 hover:shadow-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 focus-visible:ring-offset-2 motion-reduce:transform-none lg:bottom-6"
      >
        <MessageCircle aria-hidden="true" size={16} />
        追问这份日报
      </button>

      {open ? (
        <div
          data-testid="report-chat-backdrop"
          className="report-chat-backdrop fixed inset-0 z-50 flex items-end justify-end bg-slate-950/35 backdrop-blur-[2px] sm:items-stretch"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setOpen(false);
          }}
        >
          <section
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="report-chat-drawer-title"
            className="report-chat-drawer flex h-[min(82dvh,720px)] w-full flex-col overflow-hidden rounded-t-3xl bg-white shadow-2xl shadow-slate-950/20 sm:h-[100dvh] sm:w-[420px] sm:rounded-none"
          >
            <header className="flex min-h-14 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-4">
              <h2 id="report-chat-drawer-title" className="text-base font-black text-slate-950">
                追问这份日报
              </h2>
              <button
                ref={closeRef}
                type="button"
                onClick={() => setOpen(false)}
                aria-label="关闭追问助手"
                className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-full text-slate-500 transition hover:bg-blue-50 hover:text-blue-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400"
              >
                <X aria-hidden="true" size={20} />
              </button>
            </header>
            <div className="min-h-0 flex-1 pb-[env(safe-area-inset-bottom)] sm:pb-0">
              <ReportChatPanel reportId={reportId} reportTitle={reportTitle} variant="drawer" />
            </div>
          </section>
        </div>
      ) : null}
    </>
  );
}
