"use client";

import { useId, type ReactNode } from "react";
import { ChevronDown, CircleHelp } from "lucide-react";

type MethodologyNoteProps = {
  /** 触发器文案。默认「口径」——金融语境里读者一看就知道是计算/取数说明。 */
  label?: string;
  /** 说明正文。可以是纯文本，也可以是带 <strong> 的富文本。 */
  children: ReactNode;
  /** 让说明块在版式里靠右对齐（用于面板标题行）。 */
  align?: "start" | "end";
  className?: string;
};

/**
 * 口径说明：默认收起的渐进式披露。
 *
 * 这个组件的存在是为了解决一个具体问题 —— 产品里几乎每个面板都挂着一段
 * 「这个数怎么算的 / 它不代表什么」的免责或方法论文字。这些话本身是对的，
 * 但一直摊在屏幕上会有两个后果：
 *   1. 用户读不完，于是学会整段跳过，真正重要的风险提示反而失效；
 *   2. 数字与动作被文字挤走，首屏看不到结论。
 *
 * 所以把它们收进 <details>：文案一字不改地留在 DOM 里（可被搜索、可被读屏
 * 软件访问、也不会破坏依赖这些字符串的测试），但默认不占视觉带宽。
 * 注意用原生 <details> 而不是自管 state：无需 JS 即可展开，SSR 首帧就正确。
 */
export function MethodologyNote({
  label = "口径",
  children,
  align = "start",
  className = "",
}: MethodologyNoteProps) {
  const id = useId();
  return (
    <details
      className={`methodology-note ${align === "end" ? "is-end" : ""} ${className}`.trim()}
      data-testid="methodology-note"
    >
      <summary aria-describedby={id}>
        <CircleHelp size={13} aria-hidden="true" />
        <span>{label}</span>
        <ChevronDown size={12} aria-hidden="true" className="methodology-note-caret" />
      </summary>
      <div id={id} className="methodology-note-body">
        {children}
      </div>
    </details>
  );
}

type PanelHeaderProps = {
  title: string;
  /** 标题的 id，供 aria-labelledby 引用。 */
  titleId?: string;
  /** 标题右侧的紧凑计数，例如「3 只」。 */
  count?: ReactNode;
  /** 口径说明正文；给了才渲染触发器。 */
  note?: ReactNode;
  noteLabel?: string;
  /** 右侧操作区（展开/收起按钮等）。 */
  actions?: ReactNode;
  as?: "h2" | "h3";
  className?: string;
};

/**
 * 面板标题行：标题 + 计数 + 口径 + 操作，一行放完。
 * 替代原来「标题 / 一段说明文字 / 内容」的三段式，把说明降级为可选披露。
 */
export function PanelHeader({
  title,
  titleId,
  count,
  note,
  noteLabel,
  actions,
  as: Heading = "h3",
  className = "",
}: PanelHeaderProps) {
  return (
    <div className={`panel-header ${className}`.trim()}>
      <div className="panel-header-main">
        <Heading id={titleId} className="panel-header-title">
          {title}
        </Heading>
        {count != null ? <span className="panel-header-count">{count}</span> : null}
        {note ? <MethodologyNote label={noteLabel}>{note}</MethodologyNote> : null}
      </div>
      {actions ? <div className="panel-header-actions">{actions}</div> : null}
    </div>
  );
}
