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
import { BRAND } from "@/lib/brand";
import { OCR_PRIVACY_COPY } from "@/lib/ocrPrivacy";

const FEATURES = [
  {
    icon: ScanLine,
    title: "拍图识别持仓",
    desc: "上传支付宝 / 养基宝持仓截图，自动识别基金代码、份额与收益，写入前可逐项校对。",
  },
  {
    icon: Activity,
    title: "实时追踪板块冷暖",
    desc: "自动关联基金所属板块，在同一视图查看盘中涨跌与 5 日走势，并明确数据日期。",
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
    title: OCR_PRIVACY_COPY.trustTitle,
    desc: OCR_PRIVACY_COPY.trustDescription,
  },
  {
    icon: ShieldCheck,
    title: "服务端持仓按账号隔离",
    desc: "保存到服务端的持仓按登录账号隔离，不与其他用户混用。",
  },
  {
    icon: Sparkles,
    title: "决策边界清晰",
    desc: "分析仅供参考，不代替你的判断，也不会替你执行交易。",
  },
];

const HERO_PROOFS = [
  { title: "写入前校对", desc: "识别结果由你确认" },
  { title: "日期可追溯", desc: "区分实时与估算" },
  { title: "结论先行", desc: "证据按需展开" },
];

const STEPS = [
  { step: "01", title: "上传截图", desc: "支持支付宝 / 养基宝持仓截图" },
  { step: "02", title: "确认结果", desc: "核对代码、份额与收益后再写入" },
  { step: "03", title: "阅读简报", desc: "先看结论，再按需展开原因与证据" },
];

const USE_CASES = [
  { title: "上班族快速整理", desc: "用一张持仓截图完成录入，减少逐只查代码和手动抄金额。" },
  { title: "新手先看行动", desc: "先读今天是否需要处理，再按需展开原因、风险和专业证据。" },
  { title: "波段用户看联动", desc: "把持仓与板块涨跌放在同一视图，明确数据日期和估算口径。" },
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
      <div className="mx-auto flex w-full max-w-6xl flex-col px-4 py-4 sm:px-5 sm:py-5">
        {/* 顶部品牌头 */}
        <header className="reveal reveal-1 flex items-center justify-between gap-3">
          <BrandMark size="md" showEnglish />
          <nav aria-label="账号入口">
            <Link href="/login" prefetch={false} className="btn-ghost min-h-11 px-4">
              登录
            </Link>
          </nav>
        </header>

        {/* 主视觉：左文案 + 右产品预览 */}
        <section
          aria-labelledby="landing-title"
          className="grid items-center gap-8 pb-12 pt-10 sm:gap-10 sm:pb-16 sm:pt-20 lg:grid-cols-[1.05fr_0.95fr] lg:gap-6"
          data-testid="landing-hero"
        >
          <div className="reveal reveal-2 flex flex-col items-start text-left">
            <span className="eyebrow mb-5">
              <span className="eyebrow-dot">
                <Sparkles size={11} strokeWidth={2.6} />
              </span>
              AI 基金研究台
            </span>
            <h1
              id="landing-title"
              className="font-display max-w-xl text-[2.35rem] font-extrabold leading-[1.08] tracking-tight text-slate-950 sm:text-6xl"
            >
              截个图，
              <br className="hidden sm:block" />
              <span className="landing-gradient-text">就懂你的基金</span>
            </h1>
            <p className="mt-5 max-w-lg text-base leading-7 text-slate-600 sm:mt-6 sm:text-lg">
              不用记代码、不用盯盘到眼花。上传一张持仓截图，{BRAND.name}
              帮你理清持仓、追踪板块，每天推送一份
              <span className="font-semibold text-slate-700">听得懂的 AI 简报</span>。
            </p>
            <div className="mt-8 w-full sm:mt-9 sm:w-auto">
              <Link
                href="/register"
                prefetch={false}
                className="btn-primary min-h-11 w-full justify-center px-6 sm:w-auto"
                data-testid="landing-primary-cta"
              >
                免费开始使用
                <ArrowRight size={18} />
              </Link>
            </div>

            {/* 信任条 + 可验证的产品边界 */}
            <div
              aria-label="使用说明"
              className="trust-strip mt-5 w-full !justify-start gap-x-4 gap-y-2 sm:mt-6"
            >
              {["无需绑卡", OCR_PRIVACY_COPY.shortLabel, "服务端持仓隔离"].map((label) => (
                <span key={label} className="inline-flex items-center gap-1.5">
                  <Check aria-hidden="true" size={13} strokeWidth={2.8} />
                  {label}
                </span>
              ))}
            </div>
            <div
              className="mt-7 grid w-full max-w-xl divide-y divide-slate-200/80 border-y border-slate-200/80 sm:grid-cols-3 sm:divide-x sm:divide-y-0"
              data-testid="landing-proof-strip"
            >
              {HERO_PROOFS.map((proof, index) => (
                <div
                  key={proof.title}
                  className={`py-3.5 sm:px-4 sm:py-4 ${index === 0 ? "sm:pl-0" : ""}`}
                >
                  <span className="block text-sm font-bold text-slate-900">{proof.title}</span>
                  <span className="mt-1 block text-xs leading-5 text-slate-500">{proof.desc}</span>
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
        <section
          aria-labelledby="landing-steps-title"
          className="reveal reveal-2 pb-12 sm:pb-16"
          data-layout="editorial"
          data-testid="landing-steps"
        >
          <div className="mb-5 grid gap-2 sm:grid-cols-[0.7fr_1.3fr] sm:items-end">
            <span className="section-eyebrow">从截图到判断</span>
            <h2
              id="landing-steps-title"
              className="font-display text-xl font-extrabold tracking-tight text-slate-900 sm:text-2xl"
            >
              每一步都可确认，不把识别结果直接当答案
            </h2>
          </div>
          <ol className="grid border-y border-slate-200/80 sm:grid-cols-3">
            {STEPS.map(({ step, title, desc }) => (
              <li
                key={step}
                className="flex flex-col gap-2 border-t border-slate-200/80 py-5 first:border-t-0 sm:border-l sm:border-t-0 sm:px-6 sm:first:border-l-0 sm:first:pl-0"
              >
                <span className="font-display text-xs font-extrabold tracking-widest text-[var(--accent-strong)]">
                  {step}
                </span>
                <h3 className="text-base font-bold text-slate-900">{title}</h3>
                <p className="text-sm leading-6 text-slate-600">{desc}</p>
              </li>
            ))}
          </ol>
        </section>

        {/* 三个核心能力 */}
        <section
          aria-labelledby="landing-features-title"
          className="reveal reveal-2 pb-12 sm:pb-16"
          data-layout="editorial"
          data-testid="landing-features"
        >
          <div className="mb-6 grid gap-2 sm:grid-cols-[0.7fr_1.3fr] sm:items-end">
            <span className="section-eyebrow">核心能力</span>
            <h2
              id="landing-features-title"
              className="font-display text-2xl font-extrabold tracking-tight text-slate-900 sm:text-3xl"
            >
              三步，把一团乱的持仓理明白
            </h2>
          </div>
          <div className="grid border-y border-slate-200/80 sm:grid-cols-3">
            {FEATURES.map(({ icon: Icon, title, desc }, i) => (
              <article
                key={title}
                className="flex flex-col gap-3 border-t border-slate-200/80 py-6 first:border-t-0 sm:border-l sm:border-t-0 sm:px-6 sm:first:border-l-0 sm:first:pl-0"
              >
                <div className="flex items-center justify-between">
                  <span className="flex h-10 w-10 items-center justify-center rounded-full bg-[var(--brand-soft)] text-[var(--brand-strong)]">
                    <Icon aria-hidden="true" size={19} strokeWidth={2.2} />
                  </span>
                  <span className="feature-index">0{i + 1}</span>
                </div>
                <h3 className="text-lg font-bold text-slate-900">{title}</h3>
                <p className="text-sm leading-6 text-slate-600">{desc}</p>
              </article>
            ))}
          </div>
        </section>

        {/* 典型使用方式 */}
        <section className="reveal reveal-2 pb-12 sm:pb-16">
          <div className="mb-6 grid gap-2 sm:grid-cols-[0.7fr_1.3fr] sm:items-end">
            <span className="section-eyebrow">典型使用方式</span>
            <h2 className="font-display text-2xl font-extrabold tracking-tight text-slate-900 sm:text-3xl">
              不同阶段，都能快速找到下一步
            </h2>
          </div>
          <div className="grid border-y border-slate-200/80 sm:grid-cols-3">
            {USE_CASES.map(({ title, desc }, index) => (
              <article
                key={title}
                className="border-t border-slate-200/80 py-5 text-left first:border-t-0 sm:border-l sm:border-t-0 sm:px-6 sm:first:border-l-0 sm:first:pl-0"
              >
                <span className="font-display text-xs font-extrabold tracking-[0.16em] text-[var(--accent-strong)]">
                  0{index + 1}
                </span>
                <h3 className="mt-2 text-base font-bold text-slate-900">{title}</h3>
                <p className="mt-2 text-sm leading-6 text-slate-600">{desc}</p>
              </article>
            ))}
          </div>
        </section>

        {/* 会员 / Pro 价值展示 */}
        <section className="reveal reveal-2 pb-12 sm:pb-16">
          <div className="mb-8 text-center">
            <span className="section-eyebrow">会员方案</span>
            <h2 className="font-display mt-1.5 text-2xl font-extrabold tracking-tight text-slate-900 sm:text-3xl">
              先免费用顺手，再决定要不要升级
            </h2>
            <p className="mx-auto mt-3 max-w-xl text-sm text-slate-500">
              基础能力当前可免费使用。需要更勤快的盯盘、更深的分析时，Pro 帮你多想一步。
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
                <span className="mb-1 text-sm font-medium text-slate-500">/ 当前</span>
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
              <Link href="/register" prefetch={false} className="btn-secondary mt-7 min-h-11 w-full justify-center">
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
                <span className="text-base font-bold text-slate-900">{BRAND.name} Pro</span>
              </div>
              <div className="mt-3 flex items-end gap-1">
                <span className="plan-price text-4xl">¥19</span>
                <span className="mb-1 text-sm font-medium text-slate-500">/ 月</span>
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
                className="btn-accent mt-7 min-h-11 w-full justify-center disabled:cursor-not-allowed disabled:opacity-70"
                disabled
              >
                敬请期待
              </button>
            </div>
          </div>
        </section>

        {/* 为什么放心用 */}
        <section
          aria-labelledby="landing-trust-title"
          className="reveal reveal-2 mb-12 border-y border-slate-200/80 py-8 sm:mb-16 sm:py-10"
          data-layout="editorial"
          data-testid="landing-trust"
        >
          <div className="grid gap-7 lg:grid-cols-[0.72fr_2fr] lg:gap-10">
            <div>
              <span className="section-eyebrow">隐私与边界</span>
              <h2
                id="landing-trust-title"
                className="font-display mt-1.5 text-2xl font-extrabold tracking-tight text-slate-900 sm:text-3xl"
              >
                放心使用，先把边界说清楚
              </h2>
              <p className="mt-3 max-w-md text-sm leading-6 text-slate-600">
                识别方式、数据隔离和分析责任都明确披露，不用模糊承诺换取信任。
              </p>
            </div>
            <div className="grid divide-y divide-slate-200/80 sm:grid-cols-3 sm:divide-x sm:divide-y-0">
              {TRUST.map(({ icon: Icon, title, desc }, index) => (
                <article
                  key={title}
                  className={`flex flex-col items-start gap-2 py-5 sm:px-5 sm:py-1 ${
                    index === 0 ? "sm:pl-0" : ""
                  }`}
                >
                  <span className="flex h-10 w-10 items-center justify-center rounded-full bg-[var(--brand-soft)] text-[var(--brand-strong)]">
                    <Icon aria-hidden="true" size={19} strokeWidth={2.2} />
                  </span>
                  <h3 className="text-base font-bold text-slate-900">{title}</h3>
                  <p className="text-sm leading-6 text-slate-600">{desc}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        {/* 底部 CTA */}
        <section className="reveal reveal-2 relative mb-10 overflow-hidden rounded-[var(--radius-card)] px-5 py-10 text-center text-white shadow-[var(--shadow-lg)] sm:mb-12 sm:px-6 sm:py-12">
          <div
            className="absolute inset-0 -z-10"
            style={{
              background:
                "radial-gradient(600px 240px at 50% -30%, rgba(207,155,62,0.35), transparent 70%), linear-gradient(140deg, var(--brand) 0%, var(--brand-deep) 100%)",
            }}
          />
          <h2 className="font-display text-2xl font-extrabold tracking-tight sm:text-3xl">
            现在就让{BRAND.name}帮你看懂基金
          </h2>
          <p className="mx-auto mt-3 max-w-md text-sm leading-6 text-blue-50/85">
            上传一张持仓截图，几分钟内得到属于你的第一份投研日报。
          </p>
          <Link
            href="/register"
            prefetch={false}
            className="mt-7 inline-flex min-h-11 items-center gap-2 rounded-full bg-white px-7 py-3 text-sm font-bold text-[var(--brand-strong)] transition-colors hover:bg-blue-50"
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
          <p className="mx-auto max-w-2xl px-2 text-xs leading-5 text-slate-600 sm:px-4">
            投资有风险，入市需谨慎。本工具提供的内容仅供参考，不构成任何投资建议。
          </p>
          <p className="mt-2 text-xs text-slate-500">
            © {new Date().getFullYear()} {BRAND.name} {BRAND.englishName}
          </p>
        </footer>
      </div>

      {/* 移动端固定 CTA */}
      <div className="landing-sticky-cta sm:hidden">
        <Link href="/register" prefetch={false} className="btn-primary min-h-11 w-full justify-center">
          免费开始使用
          <ArrowRight size={18} />
        </Link>
      </div>
    </main>
  );
}

/** 手机产品预览：仿真「持有」首屏，突出收益大数字与板块标签。 */
function DevicePreview() {
  return (
    <figure className="relative">
      <figcaption className="mb-3 text-center text-[11px] font-bold tracking-[0.08em] text-slate-500">
        界面示意 · 非实时数据
      </figcaption>
      <div className="relative" aria-hidden="true">
      {/* 悬浮徽标 */}
      <div className="float-badge reveal reveal-4 left-[-8px] top-10 z-20 hidden sm:flex">
        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-[var(--brand-soft)] text-[var(--brand-strong)]">
          <Activity size={16} strokeWidth={2.4} />
        </span>
        <div className="text-left leading-tight">
          <div className="text-[11px] font-bold text-slate-900">板块实时</div>
          <div className="text-[10px] text-slate-500">半导体 +2.5% · 5日 +8.1%</div>
        </div>
      </div>
      <div className="float-badge reveal reveal-5 bottom-12 right-[-10px] z-20 hidden sm:flex">
        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-[var(--accent-soft)] text-[var(--accent-strong)]">
          <FileText size={16} strokeWidth={2.4} />
        </span>
        <div className="text-left leading-tight">
          <div className="text-[11px] font-bold text-slate-900">AI 日报已生成</div>
          <div className="text-[10px] text-slate-500">建议：分批止盈</div>
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
            <div className="text-[11px] font-medium text-slate-500">今日收益（元）</div>
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
            <div className="mt-2 flex items-center justify-between text-[11px] text-slate-500">
              <span>持仓市值 ¥ 60,420</span>
              <span>累计 <span className="profit-up font-semibold">+8,930</span></span>
            </div>
          </div>

          {/* 持仓行 */}
          <div className="mt-3 flex flex-col gap-2">
            <HoldingRow name="示例基金 A" sector="半导体" pct="+3.21%" up />
            <HoldingRow name="示例基金 B" sector="大盘" pct="-0.84%" />
            <HoldingRow name="示例基金 C" sector="食品饮料" pct="+1.07%" up />
          </div>
        </div>
      </div>
      </div>
    </figure>
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
