"use client";

import Link from "next/link";
import {
  ScanLine,
  Activity,
  FileText,
  ShieldCheck,
  Lock,
  Sparkles,
  ArrowRight,
  Check,
  Bell,
  Crown,
  TrendingUp,
} from "lucide-react";
import { BrandMark } from "@/components/BrandMark";

const FEATURES = [
  {
    icon: ScanLine,
    title: "拍图识别持仓",
    desc: "支付宝 / 养基宝截图一拍，自动识别基金代码、份额与收益，告别手动录入。",
  },
  {
    icon: Activity,
    title: "实时追踪板块冷暖",
    desc: "自动关联你的基金所属板块，盘中实时涨跌、连涨天数一目了然。",
  },
  {
    icon: FileText,
    title: "听得懂的投研日报",
    desc: "AI 每天为你的每只持仓生成一份说人话的操作建议，不堆术语。",
  },
];

const TRUST = [
  {
    icon: Lock,
    title: "不上传原始截图",
    desc: "截图仅在本地识别，发往 AI 的只有结构化的持仓与行情摘要。",
  },
  {
    icon: ShieldCheck,
    title: "数据按账号隔离",
    desc: "你的持仓只属于你，私有部署、按用户隔离，干净清爽。",
  },
  {
    icon: Sparkles,
    title: "只为已有持仓服务",
    desc: "聚焦你真实持有的基金，不推销、不诱导，做你身边的明白人。",
  },
];

const STATS = [
  { value: "30s", label: "截图到看懂" },
  { value: "0", label: "手动录入" },
  { value: "每日", label: "AI 简报" },
];

const STEPS = [
  { step: "01", title: "上传截图", desc: "支付宝 / 养基宝持仓一拍即识" },
  { step: "02", title: "看懂持仓", desc: "收益、板块涨跌、风险一眼明白" },
  { step: "03", title: "每日简报", desc: "AI 说人话，告诉你该不该动" },
];

const PERSONAS = [
  { quote: "终于不用一个个手打基金代码了，截图 30 秒全进来。", who: "上班族基民" },
  { quote: "日报不说术语，直接告诉我今天要不要加仓。", who: "理财小白" },
  { quote: "板块涨跌和持仓自动关联，盘中心里更有数。", who: "波段爱好者" },
];

const FREE_FEATURES = ["截图识别持仓", "板块实时涨跌", "每日 1 份投研日报", "盈亏分析看板"];

const PRO_FEATURES = [
  "无限次深度 AI 日报",
  "盘中波段提醒 · 桌面推送",
  "多账户 / 多组合管理",
  "板块信号历史回测",
  "一键导出投研报告",
];

export function LandingPage() {
  return (
    <main className="landing-hero-bg min-h-screen overflow-hidden">
      <div className="mx-auto flex w-full max-w-6xl flex-col px-5 py-5">
        {/* 顶部品牌头 */}
        <header className="reveal reveal-1 flex items-center justify-between gap-3">
          <BrandMark size="md" showEnglish />
          <nav className="flex items-center gap-2">
            <Link href="/login" className="btn-ghost">
              登录
            </Link>
            <Link href="/register" className="btn-primary !px-5 !py-2.5 !text-sm">
              免费注册
            </Link>
          </nav>
        </header>

        {/* 主视觉：左文案 + 右产品预览 */}
        <section className="grid items-center gap-10 pb-16 pt-12 sm:pt-20 lg:grid-cols-[1.05fr_0.95fr] lg:gap-6">
          <div className="reveal reveal-2 flex flex-col items-start text-left">
            <span className="eyebrow mb-5">
              <span className="eyebrow-dot">
                <Sparkles size={11} strokeWidth={2.6} />
              </span>
              AI 基金投研助手
            </span>
            <h1 className="font-display max-w-xl text-[2.6rem] font-extrabold leading-[1.1] tracking-tight text-slate-950 sm:text-6xl">
              截个图，
              <br className="hidden sm:block" />
              <span className="landing-gradient-text">就懂你的基金</span>
            </h1>
            <p className="mt-6 max-w-lg text-base leading-7 text-slate-500 sm:text-lg">
              不用记代码、不用盯盘到眼花。上传一张持仓截图，好基灵帮你理清持仓、追踪板块，每天推送一份
              <span className="font-semibold text-slate-700">听得懂的 AI 简报</span>。
            </p>
            <div className="mt-9 flex w-full flex-col items-stretch gap-3 sm:w-auto sm:flex-row sm:items-center">
              <Link href="/register" className="btn-primary w-full justify-center sm:w-auto">
                免费开始，30 秒上手
                <ArrowRight size={18} />
              </Link>
              <Link href="/login" className="btn-secondary w-full justify-center sm:w-auto">
                已有账号登录
              </Link>
            </div>

            {/* 信任条 + 能力指标 */}
            <div className="trust-strip mt-6">
              <span>无需绑卡</span>
              <span className="dot" />
              <span>本地识别截图</span>
              <span className="dot" />
              <span>按账号隔离</span>
            </div>
            <div className="mt-8 flex items-stretch gap-6 border-t border-slate-200/70 pt-6">
              {STATS.map((stat) => (
                <div key={stat.label} className="flex flex-col">
                  <span className="stat-value text-2xl sm:text-3xl">{stat.value}</span>
                  <span className="mt-1.5 text-xs font-medium text-slate-500">{stat.label}</span>
                </div>
              ))}
            </div>
          </div>

          {/* 右侧：手机产品预览 */}
          <div className="reveal reveal-3 relative mx-auto w-full max-w-sm">
            <DevicePreview />
          </div>
        </section>

        {/* 三步上手 */}
        <section className="reveal reveal-2 pb-14">
          <div className="landing-steps section-card grid gap-4 p-5 sm:grid-cols-3 sm:p-6">
            {STEPS.map(({ step, title, desc }) => (
              <div key={step} className="flex flex-col gap-2">
                <span className="font-display text-xs font-extrabold tracking-widest text-[var(--accent-strong)]">
                  {step}
                </span>
                <h3 className="text-base font-bold text-slate-900">{title}</h3>
                <p className="text-sm leading-6 text-slate-500">{desc}</p>
              </div>
            ))}
          </div>
        </section>

        {/* 三个核心能力 */}
        <section className="reveal reveal-2 pb-16">
          <div className="mb-7 flex flex-col gap-1.5">
            <span className="section-eyebrow">核心能力</span>
            <h2 className="font-display text-2xl font-extrabold tracking-tight text-slate-900 sm:text-3xl">
              三步，把一团乱的持仓理明白
            </h2>
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            {FEATURES.map(({ icon: Icon, title, desc }, i) => (
              <div key={title} className="section-card feature-card card-hover flex flex-col gap-3 p-6">
                <div className="flex items-center justify-between">
                  <span className="empty-state-icon !h-12 !w-12">
                    <Icon size={22} strokeWidth={2.2} />
                  </span>
                  <span className="feature-index">0{i + 1}</span>
                </div>
                <h3 className="text-lg font-bold text-slate-900">{title}</h3>
                <p className="text-sm leading-6 text-slate-500">{desc}</p>
              </div>
            ))}
          </div>
        </section>

        {/* 用户场景 */}
        <section className="reveal reveal-2 pb-16">
          <div className="mb-7 text-center">
            <span className="section-eyebrow">真实场景</span>
            <h2 className="font-display mt-1.5 text-2xl font-extrabold tracking-tight text-slate-900 sm:text-3xl">
              基民们这样用好基灵
            </h2>
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            {PERSONAS.map(({ quote, who }) => (
              <blockquote key={who} className="section-card flex flex-col gap-3 p-6">
                <p className="text-sm leading-7 text-slate-700">&ldquo;{quote}&rdquo;</p>
                <footer className="text-xs font-bold text-[var(--muted)]">— {who}</footer>
              </blockquote>
            ))}
          </div>
        </section>

        {/* 会员 / Pro 价值展示 */}
        <section className="reveal reveal-2 pb-16">
          <div className="mb-8 text-center">
            <span className="section-eyebrow">会员方案</span>
            <h2 className="font-display mt-1.5 text-2xl font-extrabold tracking-tight text-slate-900 sm:text-3xl">
              先免费用顺手，再决定要不要升级
            </h2>
            <p className="mx-auto mt-3 max-w-xl text-sm text-slate-500">
              基础能力永久免费。需要更勤快的盯盘、更深的分析时，Pro 帮你多想一步。
            </p>
          </div>
          <div className="mx-auto grid max-w-3xl gap-5 sm:grid-cols-2">
            {/* 免费 */}
            <div className="plan-card">
              <div className="flex items-center gap-2">
                <span className="text-base font-bold text-slate-900">免费版</span>
              </div>
              <div className="mt-3 flex items-end gap-1">
                <span className="plan-price text-4xl">¥0</span>
                <span className="mb-1 text-sm font-medium text-slate-400">/ 永久</span>
              </div>
              <p className="mt-2 text-sm text-slate-500">看懂自己的基金，足够用。</p>
              <ul className="mt-5 flex flex-col gap-3">
                {FREE_FEATURES.map((f) => (
                  <li key={f} className="plan-feature">
                    <span className="check">
                      <Check size={12} strokeWidth={3} />
                    </span>
                    {f}
                  </li>
                ))}
              </ul>
              <Link href="/register" className="btn-secondary mt-7 w-full justify-center">
                免费注册
              </Link>
            </div>

            {/* Pro */}
            <div className="plan-card is-pro">
              <span className="ribbon">
                <Crown size={12} strokeWidth={2.6} />
                即将上线
              </span>
              <div className="flex items-center gap-2">
                <span className="text-base font-bold text-slate-900">好基灵 Pro</span>
              </div>
              <div className="mt-3 flex items-end gap-1">
                <span className="plan-price text-4xl">¥19</span>
                <span className="mb-1 text-sm font-medium text-slate-400">/ 月</span>
              </div>
              <p className="mt-2 text-sm text-slate-500">把盯盘和分析，交给更勤快的它。</p>
              <ul className="mt-5 flex flex-col gap-3">
                {PRO_FEATURES.map((f) => (
                  <li key={f} className="plan-feature">
                    <span className="check">
                      <Check size={12} strokeWidth={3} />
                    </span>
                    {f}
                  </li>
                ))}
              </ul>
              <button
                type="button"
                className="btn-accent mt-7 w-full justify-center disabled:cursor-not-allowed disabled:opacity-70"
                disabled
              >
                敬请期待
              </button>
            </div>
          </div>
        </section>

        {/* 为什么放心用 */}
        <section className="section-card reveal reveal-2 mb-16 p-7 sm:p-10">
          <div className="mb-7 text-center">
            <span className="section-eyebrow">隐私优先</span>
            <h2 className="font-display mt-1.5 text-2xl font-extrabold tracking-tight text-slate-900 sm:text-3xl">
              为什么可以放心用
            </h2>
            <p className="mt-3 text-sm text-slate-500">本地优先、隐私边界清晰，做一个让人安心的基金助手。</p>
          </div>
          <div className="grid gap-7 sm:grid-cols-3">
            {TRUST.map(({ icon: Icon, title, desc }) => (
              <div key={title} className="flex flex-col items-center gap-2 text-center">
                <span className="empty-state-icon">
                  <Icon size={22} strokeWidth={2.2} />
                </span>
                <h3 className="text-base font-bold text-slate-900">{title}</h3>
                <p className="text-sm leading-6 text-slate-500">{desc}</p>
              </div>
            ))}
          </div>
        </section>

        {/* 底部 CTA */}
        <section className="reveal reveal-2 relative mb-12 overflow-hidden rounded-[var(--radius-card)] px-6 py-12 text-center text-white shadow-[var(--shadow-lg)]">
          <div
            className="absolute inset-0 -z-10"
            style={{
              background:
                "radial-gradient(600px 240px at 50% -30%, rgba(207,155,62,0.35), transparent 70%), linear-gradient(140deg, var(--brand) 0%, var(--brand-deep) 100%)",
            }}
          />
          <h2 className="font-display text-2xl font-extrabold tracking-tight sm:text-3xl">
            现在就让好基灵帮你看懂基金
          </h2>
          <p className="mx-auto mt-3 max-w-md text-sm leading-6 text-blue-50/85">
            上传一张持仓截图，几分钟内得到属于你的第一份投研日报。
          </p>
          <Link
            href="/register"
            className="mt-7 inline-flex items-center gap-2 rounded-full bg-white px-7 py-3 text-sm font-bold text-[var(--brand-strong)] transition hover:-translate-y-0.5 hover:bg-blue-50"
          >
            免费注册
            <ArrowRight size={18} />
          </Link>
        </section>

        {/* 页脚与风险提示 */}
        <footer className="border-t border-slate-200/70 py-7 pb-24 text-center sm:pb-7">
          <div className="mb-3 flex justify-center">
            <BrandMark size="sm" showEnglish />
          </div>
          <p className="mx-auto max-w-2xl px-4 text-xs leading-5 text-slate-400">
            投资有风险，入市需谨慎。本工具提供的内容仅供参考，不构成任何投资建议。
          </p>
          <p className="mt-2 text-xs text-slate-300">© {new Date().getFullYear()} 好基灵 FundPilot</p>
        </footer>
      </div>

      {/* 移动端固定 CTA */}
      <div className="landing-sticky-cta sm:hidden">
        <Link href="/register" className="btn-primary w-full justify-center">
          免费注册，30 秒上手
          <ArrowRight size={18} />
        </Link>
      </div>
    </main>
  );
}

/** 手机产品预览：仿真「持有」首屏，突出收益大数字与板块标签。 */
function DevicePreview() {
  return (
    <div className="relative">
      {/* 悬浮徽标 */}
      <div className="float-badge reveal reveal-4 left-[-8px] top-10 z-20 hidden sm:flex">
        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-[var(--brand-soft)] text-[var(--brand-strong)]">
          <Activity size={16} strokeWidth={2.4} />
        </span>
        <div className="text-left leading-tight">
          <div className="text-[11px] font-bold text-slate-900">板块实时</div>
          <div className="text-[10px] text-slate-400">半导体 连涨 3 天</div>
        </div>
      </div>
      <div className="float-badge reveal reveal-5 bottom-12 right-[-10px] z-20 hidden sm:flex">
        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-[var(--accent-soft)] text-[var(--accent-strong)]">
          <FileText size={16} strokeWidth={2.4} />
        </span>
        <div className="text-left leading-tight">
          <div className="text-[11px] font-bold text-slate-900">AI 日报已生成</div>
          <div className="text-[10px] text-slate-400">建议：分批止盈</div>
        </div>
      </div>

      <div className="device-shell float-soft">
        <span className="device-notch" />
        <div className="device-screen">
          {/* 顶部 */}
          <div className="mb-3 flex items-center justify-between">
            <BrandMark size="sm" />
            <span className="mini-chip">
              <Bell size={10} strokeWidth={2.6} />
              今日
            </span>
          </div>

          {/* 收益大数字卡 */}
          <div className="mini-card p-3.5">
            <div className="text-[11px] font-medium text-slate-400">今日收益（元）</div>
            <div className="mt-1 flex items-end justify-between">
              <span className="profit-up font-display tnum text-[2rem] font-extrabold leading-none">
                +1,284.50
              </span>
              <span className="profit-up inline-flex items-center gap-0.5 text-sm font-bold">
                <TrendingUp size={14} strokeWidth={2.6} />
                +2.14%
              </span>
            </div>
            <Sparkline />
            <div className="mt-2 flex items-center justify-between text-[11px] text-slate-400">
              <span>持仓市值 ¥ 60,420</span>
              <span>累计 <span className="profit-up font-semibold">+8,930</span></span>
            </div>
          </div>

          {/* 持仓行 */}
          <div className="mt-3 flex flex-col gap-2">
            <HoldingRow name="易方达蓝筹精选" sector="半导体" pct="+3.21%" up />
            <HoldingRow name="华夏中证500ETF" sector="大盘" pct="-0.84%" />
            <HoldingRow name="招商中证白酒" sector="食品饮料" pct="+1.07%" up />
          </div>
        </div>
      </div>
    </div>
  );
}

function Sparkline() {
  return (
    <svg viewBox="0 0 240 56" className="mt-2.5 h-12 w-full" preserveAspectRatio="none" aria-hidden>
      <defs>
        <linearGradient id="spark-fill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--profit-up)" stopOpacity="0.22" />
          <stop offset="100%" stopColor="var(--profit-up)" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path
        d="M0 44 L26 40 L52 42 L78 32 L104 36 L130 24 L156 28 L182 16 L208 20 L240 6 L240 56 L0 56 Z"
        fill="url(#spark-fill)"
      />
      <path
        d="M0 44 L26 40 L52 42 L78 32 L104 36 L130 24 L156 28 L182 16 L208 20 L240 6"
        fill="none"
        stroke="var(--profit-up)"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function HoldingRow({
  name,
  sector,
  pct,
  up = false,
}: {
  name: string;
  sector: string;
  pct: string;
  up?: boolean;
}) {
  return (
    <div className="mini-card flex items-center justify-between px-3 py-2.5">
      <div className="min-w-0">
        <div className="truncate text-[13px] font-semibold text-slate-800">{name}</div>
        <span className="mini-chip mt-1">{sector}</span>
      </div>
      <span className={`${up ? "profit-up" : "profit-down"} font-display tnum text-sm font-bold`}>
        {pct}
      </span>
    </div>
  );
}
