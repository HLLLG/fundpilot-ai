"use client";

import { Children, type ReactNode } from "react";

type BackgroundJobsStackProps = {
  children: ReactNode;
};

/**
 * 后台任务浮层容器（日报 / 荐基进度）。
 *
 * 定位全部交给 `.background-jobs-stack`（dashboard.css）：手机与平板上贴在底栏正上方
 * 通栏一条并压在底栏 z-index 之下，桌面回到右下角 18rem 卡片堆叠。之所以写在 CSS 里
 * 而不是 Tailwind 任意值，是因为它必须和 `.dashboard-bottom-nav` 共用
 * `--bottom-nav-h` 这个高度常量；两边各自硬编码迟早会漂移，浮层压住导航按钮就是这么
 * 来的。
 *
 * `column-reverse`：DOM 里靠前的浮层显示在最下方（最靠近底栏）。
 */
export function BackgroundJobsStack({ children }: BackgroundJobsStackProps) {
  const items = Children.toArray(children).filter(Boolean);
  if (!items.length) {
    return null;
  }

  return (
    <div className="background-jobs-stack" aria-live="polite">
      {items.map((item) => (
        <div
          key={(item as { key?: string | null }).key ?? String(item)}
          className="pointer-events-auto"
        >
          {item}
        </div>
      ))}
    </div>
  );
}
