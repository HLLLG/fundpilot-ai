"use client";

import { AlertTriangle } from "lucide-react";
import { useId, useRef } from "react";

import type { HistoryDeleteIntent, HistoryRailItem } from "@/lib/useHistoryRailController";
import { useDialogA11y } from "@/lib/useDialogA11y";

export type HistoryDeleteDialogCopy = {
  singleTitle: string;
  batchTitle: (count: number) => string;
  description: string;
  additionalItems: (count: number) => string;
};

type HistoryDeleteDialogProps<T extends HistoryRailItem> = {
  intent: HistoryDeleteIntent<T>;
  copy: HistoryDeleteDialogCopy;
  onClose: () => void;
  onConfirm: () => void | Promise<unknown>;
};

export function HistoryDeleteDialog<T extends HistoryRailItem>({
  intent,
  copy,
  onClose,
  onConfirm,
}: HistoryDeleteDialogProps<T>) {
  const cancelButtonRef = useRef<HTMLButtonElement>(null);
  const dialogId = useId();
  const titleId = `${dialogId}-title`;
  const descriptionId = `${dialogId}-description`;
  const dialogRef = useDialogA11y<HTMLDivElement>({
    open: true,
    onClose,
    initialFocusRef: cancelButtonRef,
  });
  const deleteCount = intent.reports.length;
  const deletePreview = intent.reports.slice(0, 3);

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-950/45 p-4"
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
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        className="w-full max-w-sm rounded-[24px] bg-white p-5 shadow-2xl"
      >
        <div className="flex items-start gap-3">
          <span
            className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-rose-50 text-rose-600"
            aria-hidden="true"
          >
            <AlertTriangle size={20} />
          </span>
          <div className="min-w-0">
            <h2 id={titleId} className="text-base font-black text-slate-950">
              {intent.kind === "batch" ? copy.batchTitle(deleteCount) : copy.singleTitle}
            </h2>
            <p id={descriptionId} className="mt-1 text-sm leading-6 text-slate-600">
              {copy.description}
            </p>
          </div>
        </div>

        <ul className="mt-4 space-y-2 rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-700">
          {deletePreview.map((report) => (
            <li key={report.id} className="truncate">
              {report.title}
            </li>
          ))}
          {deleteCount > deletePreview.length ? (
            <li className="text-xs font-semibold text-slate-500">
              {copy.additionalItems(deleteCount - deletePreview.length)}
            </li>
          ) : null}
        </ul>

        <div className="mt-5 grid grid-cols-2 gap-3">
          <button
            ref={cancelButtonRef}
            type="button"
            onClick={onClose}
            className="btn-secondary min-h-11"
          >
            取消
          </button>
          <button
            type="button"
            onClick={() => void onConfirm()}
            className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-rose-600 px-4 py-2.5 text-sm font-bold text-white transition hover:bg-rose-700"
          >
            确认删除
          </button>
        </div>
      </div>
    </div>
  );
}
