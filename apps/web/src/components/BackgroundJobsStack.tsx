"use client";

import { Children, type ReactNode } from "react";

type BackgroundJobsStackProps = {
  children: ReactNode;
};

/** 右下角堆叠多个后台任务浮层，避免日报/荐基进度互相遮挡。 */
export function BackgroundJobsStack({ children }: BackgroundJobsStackProps) {
  const items = Children.toArray(children).filter(Boolean);
  if (!items.length) {
    return null;
  }

  return (
    <div
      className="pointer-events-none fixed bottom-6 right-6 z-50 flex w-72 flex-col-reverse gap-3"
      aria-live="polite"
    >
      {items.map((item) => (
        <div key={(item as { key?: string | null }).key ?? String(item)} className="pointer-events-auto">
          {item}
        </div>
      ))}
    </div>
  );
}
