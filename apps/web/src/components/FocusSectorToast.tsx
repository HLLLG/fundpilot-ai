"use client";

import { useEffect, useState } from "react";
import { DISCOVERY_FOCUS_TOAST_EVENT } from "@/lib/discoveryFocusSectors";

export function FocusSectorToast() {
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let timer: number | null = null;
    const onToast = (event: Event) => {
      const detail = (event as CustomEvent<string>).detail;
      if (!detail) {
        return;
      }
      setMessage(detail);
      if (timer != null) {
        window.clearTimeout(timer);
      }
      timer = window.setTimeout(() => setMessage(null), 3200);
    };
    window.addEventListener(DISCOVERY_FOCUS_TOAST_EVENT, onToast);
    return () => {
      window.removeEventListener(DISCOVERY_FOCUS_TOAST_EVENT, onToast);
      if (timer != null) {
        window.clearTimeout(timer);
      }
    };
  }, []);

  if (!message) {
    return null;
  }

  return (
    <div
      role="status"
      // 与底栏共用 `--bottom-nav-h`，避免硬编码的 bottom 值和导航/任务浮层抢位置。
      className="pointer-events-none fixed bottom-[calc(var(--bottom-nav-h,3.25rem)+env(safe-area-inset-bottom,0px)+0.5rem)] left-1/2 z-[60] max-w-[min(92vw,24rem)] -translate-x-1/2 rounded-xl border border-slate-200 bg-slate-900 px-4 py-2.5 text-center text-sm font-medium text-white shadow-lg lg:bottom-6"
    >
      {message}
    </div>
  );
}
