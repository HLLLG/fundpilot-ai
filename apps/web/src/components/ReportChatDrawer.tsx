"use client";

import { useEffect, useRef, useState } from "react";
import { MessageCircle, X } from "lucide-react";

import { ReportChatPanel } from "@/components/ReportChatPanel";
import { useMediaQuery } from "@/lib/useMediaQuery";

const DESKTOP_QUERY = "(min-width: 1280px)";

type ReportChatDrawerProps = {
  reportId: string;
  reportTitle?: string;
};

export function ReportChatDrawer({ reportId, reportTitle }: ReportChatDrawerProps) {
  const [open, setOpen] = useState(false);
  const isDesktop = useMediaQuery(DESKTOP_QUERY);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const drawerId = `report-chat-${reportId}`;
  const titleId = `${drawerId}-title`;

  useEffect(() => {
    if (!open) return;

    const trigger = triggerRef.current;
    closeRef.current?.focus();

    return () => trigger?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };

    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

  useEffect(() => {
    if (!open || isDesktop) return;

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [isDesktop, open]);

  useEffect(() => {
    if (!open || isDesktop) return;

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Tab" || !dialogRef.current) return;

      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      );
      if (!focusable.length) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!dialogRef.current.contains(document.activeElement)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [isDesktop, open]);

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen(true)}
        aria-expanded={open}
        aria-controls={drawerId}
        tabIndex={open ? -1 : undefined}
        className={`fixed bottom-[calc(5rem+env(safe-area-inset-bottom))] right-4 z-30 inline-flex min-h-11 items-center gap-2 rounded-full bg-[var(--brand-strong)] px-4 text-sm font-black text-white shadow-lg shadow-blue-950/15 transition hover:-translate-y-0.5 hover:shadow-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 focus-visible:ring-offset-2 motion-reduce:transform-none lg:bottom-6 ${
          open ? "invisible pointer-events-none" : ""
        }`}
      >
        <MessageCircle aria-hidden="true" size={16} />
        追问这份日报
      </button>

      {open ? (
        <div
          data-testid="report-chat-layer"
          className="report-chat-layer pointer-events-none fixed inset-0 z-50 flex items-end justify-end sm:items-stretch"
        >
          {!isDesktop ? (
            <div
              key="backdrop"
              data-testid="report-chat-backdrop"
              className="report-chat-backdrop pointer-events-auto fixed inset-0 bg-slate-950/35 backdrop-blur-[2px]"
              onMouseDown={() => setOpen(false)}
              onClick={() => setOpen(false)}
            />
          ) : null}
          <section
            key="drawer"
            ref={dialogRef}
            id={drawerId}
            role={isDesktop ? "complementary" : "dialog"}
            aria-modal={isDesktop ? undefined : true}
            aria-labelledby={titleId}
            tabIndex={-1}
            className="report-chat-drawer pointer-events-auto relative z-[1] flex h-[min(82dvh,720px)] w-full flex-col overflow-hidden rounded-t-3xl bg-white shadow-2xl shadow-slate-950/20 outline-none sm:h-[100dvh] sm:w-[420px] sm:rounded-none"
          >
            <header className="flex min-h-14 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-4">
              <h2 id={titleId} className="text-base font-black text-slate-950">
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
