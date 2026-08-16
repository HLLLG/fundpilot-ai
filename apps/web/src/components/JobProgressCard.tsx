"use client";

import { X } from "lucide-react";
import type { ReactNode } from "react";

export type JobProgressTone = "info" | "success" | "neutral" | "danger";

type JobProgressCardProps = {
  tone: JobProgressTone;
  /** 左侧状态图标（转圈 / 对勾 / 叉）。 */
  icon: ReactNode;
  title: string;
  /**
   * 阶段细节与「可切换页面」这类说明。手机上刻意不显示：它是这四张卡片里最长的一段，
   * 一条就能把浮层从一行撑到三行。信息本身在对应页面的内联进度里都有。
   */
  detail?: ReactNode;
  /** 主操作（查看进度 / 查看报告 / 重试）。 */
  primaryAction?: { label: string; icon?: ReactNode; onClick: () => void };
  /** 次要操作（关闭），仅在给出时渲染成按钮。 */
  secondaryAction?: { label: string; onClick: () => void };
  /** 右上角的 X。与 secondaryAction 二选一，别同时给。 */
  onDismiss?: () => void;
  dismissLabel?: string;
  testId?: string;
};

const TONE_RING: Record<JobProgressTone, string> = {
  info: "border-[var(--info-border)]",
  success: "border-[var(--success-border)]",
  neutral: "border-[var(--line)]",
  danger: "border-[var(--danger-border)]",
};

const TONE_BUTTON: Record<JobProgressTone, string> = {
  info: "bg-[var(--brand)] hover:bg-[var(--brand-strong)]",
  success: "bg-[var(--success-icon)] hover:bg-[var(--success-fg)]",
  neutral: "bg-[var(--brand)] hover:bg-[var(--brand-strong)]",
  danger: "bg-[var(--brand)] hover:bg-[var(--brand-strong)]",
};

/**
 * 后台任务浮层的统一外壳。
 *
 * 四个调用方（日报流式 / 荐基流式 / 日报轮询 / 荐基轮询）以前各自复制了一份几乎相同的
 * JSX，都是 `p-4` + 两行文字 + 一整行 `min-h-11` 按钮，手机上单张卡片就有 100px 以上，
 * 两三张叠起来能吃掉小半屏。这里收敛成一个组件，并按断点给两套布局：
 *
 * - 手机 / 平板（<1024px，也就是有底栏的场合）：紧凑单行 —— 图标、标题、操作按钮同排，
 *   隐藏 detail，整条约 44px 高，仍满足 44px 的可点击区域。
 * - 桌面（lg+）：保持原来信息量更足的卡片，标题下带 detail，操作独占一行。
 */
export function JobProgressCard({
  tone,
  icon,
  title,
  detail,
  primaryAction,
  secondaryAction,
  onDismiss,
  dismissLabel,
  testId,
}: JobProgressCardProps) {
  return (
    <div
      className={`w-full rounded-xl border bg-[var(--panel)]/97 px-3 py-2 shadow-lg backdrop-blur lg:rounded-2xl lg:px-4 lg:py-4 lg:shadow-[var(--shadow-md)] ${TONE_RING[tone]}`}
      data-testid={testId}
    >
      <div className="flex items-center gap-2 lg:items-start lg:gap-3">
        <span className="flex shrink-0 items-center lg:mt-0.5">{icon}</span>

        <div className="min-w-0 flex-1">
          <div className="truncate text-[0.8125rem] font-bold text-[var(--brand-deep)] lg:whitespace-normal lg:text-sm">
            {title}
          </div>
          {detail ? (
            <div className="mt-0.5 hidden text-xs text-[var(--muted)] lg:block">{detail}</div>
          ) : null}
        </div>

        {/* 手机上主操作缩成同排的小按钮；桌面下移到独立一行 */}
        {primaryAction ? (
          <button
            type="button"
            onClick={primaryAction.onClick}
            className={`inline-flex min-h-9 shrink-0 items-center justify-center gap-1 rounded-lg px-2.5 text-xs font-bold text-white lg:hidden ${TONE_BUTTON[tone]}`}
          >
            {primaryAction.icon}
            {primaryAction.label}
          </button>
        ) : null}

        {secondaryAction ? (
          <button
            type="button"
            onClick={secondaryAction.onClick}
            className="inline-flex min-h-9 shrink-0 items-center justify-center rounded-lg border border-[var(--line)] px-2.5 text-xs font-bold text-[var(--muted)] hover:bg-[var(--surface-muted)] lg:hidden"
          >
            {secondaryAction.label}
          </button>
        ) : null}

        {onDismiss ? (
          <button
            type="button"
            onClick={onDismiss}
            className="inline-flex min-h-9 min-w-9 shrink-0 items-center justify-center rounded-full text-[var(--muted)] hover:bg-[var(--surface-muted)] lg:min-h-11 lg:min-w-11"
            aria-label={dismissLabel}
          >
            <X size={16} />
          </button>
        ) : null}
      </div>

      {primaryAction || secondaryAction ? (
        <div className="mt-3 hidden gap-2 lg:flex">
          {primaryAction ? (
            <button
              type="button"
              onClick={primaryAction.onClick}
              className={`inline-flex min-h-11 flex-1 items-center justify-center gap-1.5 rounded-xl px-3 py-2 text-xs font-bold text-white ${TONE_BUTTON[tone]}`}
            >
              {primaryAction.icon}
              {primaryAction.label}
            </button>
          ) : null}
          {secondaryAction ? (
            <button
              type="button"
              onClick={secondaryAction.onClick}
              className="min-h-11 rounded-xl border border-[var(--line)] px-3 py-2 text-xs font-bold text-[var(--muted)] hover:bg-[var(--surface-muted)]"
            >
              {secondaryAction.label}
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
