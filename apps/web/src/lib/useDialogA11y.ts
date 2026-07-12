"use client";

import { useEffect, useRef, type RefObject } from "react";

const dialogStack: symbol[] = [];
let bodyLockCount = 0;
let bodyOverflowBeforeLock = "";

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

function lockBodyScroll() {
  if (bodyLockCount === 0) {
    bodyOverflowBeforeLock = document.body.style.overflow;
    document.body.style.overflow = "hidden";
  }
  bodyLockCount += 1;
}

function unlockBodyScroll() {
  bodyLockCount = Math.max(0, bodyLockCount - 1);
  if (bodyLockCount === 0) {
    document.body.style.overflow = bodyOverflowBeforeLock;
  }
}

function focusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (element) =>
      element.tabIndex >= 0 &&
      element.getAttribute("aria-hidden") !== "true" &&
      !element.closest('[aria-hidden="true"]'),
  );
}

type UseDialogA11yOptions = {
  open: boolean;
  onClose: () => void;
  initialFocusRef?: RefObject<HTMLElement | null>;
  closeOnEscape?: boolean;
};

export function useDialogA11y<T extends HTMLElement>({
  open,
  onClose,
  initialFocusRef,
  closeOnEscape = true,
}: UseDialogA11yOptions) {
  const dialogRef = useRef<T>(null);
  const dialogIdRef = useRef(Symbol("dialog"));
  const onCloseRef = useRef(onClose);
  const closeOnEscapeRef = useRef(closeOnEscape);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    closeOnEscapeRef.current = closeOnEscape;
  }, [closeOnEscape]);

  useEffect(() => {
    if (!open) {
      return;
    }

    const dialogId = dialogIdRef.current;
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialogStack.push(dialogId);
    lockBodyScroll();

    const initialTarget =
      initialFocusRef?.current ??
      (dialogRef.current ? focusableElements(dialogRef.current)[0] : null) ??
      dialogRef.current;
    initialTarget?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (dialogStack[dialogStack.length - 1] !== dialogId || !dialogRef.current) {
        return;
      }

      if (event.key === "Escape" && closeOnEscapeRef.current) {
        event.preventDefault();
        event.stopImmediatePropagation();
        onCloseRef.current();
        return;
      }

      if (event.key !== "Tab") {
        return;
      }

      const focusable = focusableElements(dialogRef.current);
      if (!focusable.length) {
        event.preventDefault();
        dialogRef.current.focus();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && (document.activeElement === first || !dialogRef.current.contains(document.activeElement))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !dialogRef.current.contains(document.activeElement))) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      const stackIndex = dialogStack.lastIndexOf(dialogId);
      if (stackIndex >= 0) {
        dialogStack.splice(stackIndex, 1);
      }
      unlockBodyScroll();
      if (previouslyFocused?.isConnected) {
        previouslyFocused.focus();
      }
    };
  }, [initialFocusRef, open]);

  return dialogRef;
}
