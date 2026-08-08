"use client";

import { BrandMark } from "@/components/BrandMark";

/**
 * 工作台壳层骨架屏。
 *
 * 替换掉原来「屏幕正中一张小卡片 + 一个脉冲圆点」的加载态。原实现有两个问题：
 * 1. 首屏最大的可见元素是一张几十像素高的小卡，等真正的工作台挂载后整页结构
 *    突然出现，LCP 候选元素被替换、并伴随一次布局跳动；
 * 2. 用户在恢复登录态 + 加载 Dashboard chunk 这两段等待里看不到任何结构信息。
 *
 * 这里按工作台真实壳层的尺寸给出骨架：顶栏（4.25rem）+ 页头标题区 + 主内容卡片。
 * 真实工作台替换它时，外层容器、内边距与顶栏高度一致，所以不会产生新的位移。
 *
 * 注意：**只使用首屏 CSS 里已有的 Tailwind utilities**，不使用 `app-masthead` /
 * `app-page-heading` / `dashboard-shell` 这些类。那些规则住在按需加载的
 * `dashboard.css` 里，如果骨架屏依赖它们，就会把工作台样式重新拉回首屏，
 * 让 CSS 拆分白做，而且骨架自己会先闪一帧无样式。
 */
export function WorkspaceSkeleton({ message }: { message: string }) {
  return (
    <div className="premium-bg min-h-screen">
      <div
        className="mx-auto flex min-h-screen w-full max-w-[1240px] flex-col px-4 py-3 pb-24 sm:px-6 sm:py-4 lg:pb-6"
        role="status"
        aria-live="polite"
        aria-busy="true"
      >
        {/* 顶栏：高度与真实 app-masthead 的 min-height: 4.25rem 对齐 */}
        <header className="-mx-4 mb-3 flex min-h-[4.25rem] items-center justify-between gap-4 border-b border-[var(--line)] bg-[rgba(236,238,234,.95)] px-4 py-2.5 sm:-mx-6 sm:px-6">
          <BrandMark size="md" />
          <div className="hidden min-w-0 flex-1 items-center gap-2 lg:flex" aria-hidden="true">
            {[64, 64, 64, 64, 64].map((width, index) => (
              <span
                key={index}
                className="h-8 animate-pulse rounded-full bg-slate-200/70"
                style={{ width }}
              />
            ))}
          </div>
          <div className="flex shrink-0 items-center gap-2" aria-hidden="true">
            <span className="h-9 w-9 animate-pulse rounded-full bg-slate-200/70" />
            <span className="h-9 w-9 animate-pulse rounded-full bg-slate-200/70" />
          </div>
        </header>

        {/* 页头标题区：与 app-page-heading 的紧凑单行排布一致（标题 + 英文眉标，
            无描述段）。尺寸必须跟着 dashboard.css 里那三条规则一起改，否则
            骨架被真实内容替换时又会引入一次位移。 */}
        <section
          className="mt-2 mb-4 flex flex-wrap items-baseline gap-x-2.5 gap-y-1 pb-2.5"
          aria-hidden="true"
        >
          <span className="block h-6 w-28 animate-pulse rounded bg-slate-200/70" />
          <span className="block h-2.5 w-16 animate-pulse rounded bg-slate-200/60" />
        </section>

        {/* 主内容：一张与持仓看板同宽的卡片骨架 */}
        <div className="section-card min-w-0 flex-1 p-4 sm:p-5" aria-hidden="true">
          <div className="flex items-baseline justify-between gap-4">
            <span className="block h-4 w-28 animate-pulse rounded bg-slate-200/70" />
            <span className="block h-8 w-32 animate-pulse rounded bg-slate-200/70" />
          </div>
          <div className="mt-5 space-y-3">
            {[0, 1, 2, 3, 4].map((row) => (
              <div key={row} className="flex items-center gap-3">
                <span className="h-10 flex-1 animate-pulse rounded-lg bg-slate-100" />
                <span className="hidden h-10 w-24 animate-pulse rounded-lg bg-slate-100 sm:block" />
                <span className="hidden h-10 w-24 animate-pulse rounded-lg bg-slate-100 md:block" />
              </div>
            ))}
          </div>
        </div>

        {/* 文案只给辅助技术，不再占据视觉中心 */}
        <span className="sr-only">{message}</span>
      </div>
    </div>
  );
}
