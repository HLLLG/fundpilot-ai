"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  Check,
  ChevronRight,
  FileCheck2,
  Fingerprint,
  ScanLine,
  ShieldCheck,
} from "lucide-react";
import { BrandMark } from "@/components/BrandMark";
import { BRAND, SITE_REGISTRATION } from "@/lib/brand";
import { OCR_PRIVACY_COPY } from "@/lib/ocrPrivacy";

const STEPS = [
  { step: "01", title: "截图进入", desc: "支付宝 / 养基宝持仓截图" },
  { step: "02", title: "逐项校对", desc: "代码、金额与收益由你确认" },
  { step: "03", title: "形成判断", desc: "先看风险和行动，再展开证据" },
];

const CAPABILITIES = [
  ["持仓恢复", "截图识别后才进入校对，不把 OCR 猜测直接写入账户。"],
  ["组合观察", "把持仓、板块与日期放进同一条决策轨道。"],
  ["日报判断", "结论先行，风险、行动与专业证据渐进展开。"],
];

const USE_CASES = [
  ["上班族快速整理", "减少逐只查代码和手动抄录。"],
  ["新手先看行动", "先回答今天是否需要处理。"],
  ["进阶用户查证据", "继续追溯数据日期、风险与依据。"],
];

export function LandingPage() {
  const primaryCtaRef = useRef<HTMLAnchorElement>(null);
  const [showStickyCta, setShowStickyCta] = useState(false);

  useEffect(() => {
    if (!("IntersectionObserver" in window) || !primaryCtaRef.current) return;
    const observer = new IntersectionObserver(
      ([entry]) => setShowStickyCta(!entry.isIntersecting),
      { threshold: 0.1 },
    );
    observer.observe(primaryCtaRef.current);
    return () => observer.disconnect();
  }, []);

  return (
    <main className="landing-hero-bg min-h-screen overflow-x-clip">
      <header className="landing-masthead">
        <div className="mx-auto flex h-full max-w-[1240px] items-center justify-between px-4 sm:px-6">
          <BrandMark size="md" showEnglish />
          <nav aria-label="账号入口">
            <Link href="/login" prefetch={false} className="btn-ghost px-3">登录</Link>
          </nav>
        </div>
      </header>

      <div className="mx-auto w-full max-w-[1240px] px-4 sm:px-6">
        <section
          aria-labelledby="landing-title"
          className="landing-editorial-hero"
          data-testid="landing-hero"
        >
          <div className="landing-hero-copy">
            <p className="research-kicker">{BRAND.englishName} · PERSONAL RESEARCH DESK</p>
            <h1 id="landing-title" className="font-display landing-title">
              截个图，<span>就懂你的基金</span>
            </h1>
            <p className="landing-deck">
              从一张持仓截图开始，把零散数据整理成可校对的持仓、可追溯的风险，
              以及今天真正需要处理的下一步。
            </p>
            <Link
              ref={primaryCtaRef}
              href="/register"
              prefetch={false}
              className="btn-primary mt-7 min-h-11 w-full justify-center sm:w-auto"
              data-testid="landing-primary-cta"
            >
              免费开始使用 <ArrowRight size={17} />
            </Link>
            <div className="landing-compact-trust" aria-label="使用说明">
              {["无需绑卡", OCR_PRIVACY_COPY.shortLabel, "账号数据隔离"].map((item) => (
                <span key={item}><Check size={13} />{item}</span>
              ))}
            </div>
          </div>

          <ResearchDeskPreview />

          <div className="landing-proof-rail" data-testid="landing-proof-strip">
            {["写入前校对", "日期可追溯", "结论先行"].map((item, index) => (
              <div key={item}>
                <span>0{index + 1}</span>
                <strong>{item}</strong>
                <small>{index === 0 ? "识别结果由你确认" : index === 1 ? "区分实时与估算" : "证据按需展开"}</small>
              </div>
            ))}
          </div>
        </section>

        <section
          aria-labelledby="landing-steps-title"
          className="editorial-section"
          data-layout="editorial"
          data-testid="landing-steps"
        >
          <EditorialHeading eyebrow="决策轨道" title="每一步都可确认，不把识别结果直接当答案" id="landing-steps-title" />
          <ol className="decision-track">
            {STEPS.map((item) => (
              <li key={item.step}>
                <span className="decision-track-index">{item.step}</span>
                <div><h3>{item.title}</h3><p>{item.desc}</p></div>
              </li>
            ))}
          </ol>
        </section>

        <section className="editorial-section" data-layout="editorial" data-testid="landing-features">
          <EditorialHeading eyebrow="研究能力" title="不是功能堆叠，而是一条完整判断路径" id="landing-features-title" />
          <div className="editorial-ledger">
            {CAPABILITIES.map(([title, desc], index) => (
              <article key={title}>
                <span>0{index + 1}</span>
                <h3>{title}</h3>
                <p>{desc}</p>
                <ChevronRight size={17} aria-hidden="true" />
              </article>
            ))}
          </div>
        </section>

        <section className="editorial-section">
          <EditorialHeading eyebrow="典型使用方式" title="不同经验，同一套清晰秩序" />
          <div className="use-case-lines">
            {USE_CASES.map(([title, desc], index) => (
              <article key={title}><span>0{index + 1}</span><h3>{title}</h3><p>{desc}</p></article>
            ))}
          </div>
        </section>

        <section
          aria-labelledby="landing-trust-title"
          className="editorial-section trust-editorial"
          data-layout="editorial"
          data-testid="landing-trust"
        >
          <EditorialHeading eyebrow="隐私与边界" title="放心使用，先把边界说清楚" id="landing-trust-title" />
          <div className="trust-manifesto">
            <TrustLine icon={ScanLine} title={OCR_PRIVACY_COPY.trustTitle} desc={OCR_PRIVACY_COPY.trustDescription} />
            <TrustLine icon={Fingerprint} title="服务端持仓按账号隔离" desc="保存到服务端的持仓按登录账号隔离，不与其他用户混用。" />
            <TrustLine icon={ShieldCheck} title="分析边界明确" desc="内容仅供研究参考，不代替你的判断，也不会替你执行交易。" />
          </div>
        </section>

        <section className="membership-editorial">
          <div>
            <p className="research-kicker">ACCESS</p>
            <h2 className="font-display">先免费使用核心研究流程</h2>
            <p>基础能力当前可免费使用。会员能力会在正式可用时单独说明，不用模糊承诺制造稀缺。</p>
          </div>
          <Link href="/register" prefetch={false} className="btn-secondary min-h-11">创建研究台 <ArrowRight size={16} /></Link>
        </section>

        <footer className="landing-footer">
          <BrandMark size="sm" showEnglish />
          <p>
            投资有风险，入市需谨慎。本工具内容仅供参考，不构成投资建议。
            <span className="landing-registration" aria-label="网站备案信息">
              <span>{SITE_REGISTRATION.registeredSiteName}</span>
              <a
                href={SITE_REGISTRATION.icpQueryUrl}
                target="_blank"
                rel="noopener noreferrer"
              >
                {SITE_REGISTRATION.icpRecordNumber}
              </a>
            </span>
          </p>
          <small>© {new Date().getFullYear()} {BRAND.name} {BRAND.englishName}</small>
        </footer>
      </div>

      {showStickyCta ? (
        <div className="landing-sticky-cta sm:hidden" data-testid="landing-sticky-cta">
          <Link href="/register" prefetch={false} className="btn-primary min-h-11 w-full">免费开始使用 <ArrowRight size={17} /></Link>
        </div>
      ) : null}
    </main>
  );
}

function ResearchDeskPreview() {
  return (
    <figure className="research-desk-preview">
      <figcaption>界面示意 · 非实时数据</figcaption>
      <div className="desk-window">
        <div className="desk-window-head"><BrandMark size="sm" /><span>今日研究摘要</span><small>数据日期 · 待确认</small></div>
        <div className="desk-status">
          <p>组合状态</p><strong>先校对，再判断</strong>
          <span>截图识别结果尚未写入</span>
        </div>
        <div className="desk-track" aria-hidden="true">
          <span className="done"><ScanLine size={15} />截图</span>
          <i />
          <span className="active"><FileCheck2 size={15} />校对</span>
          <i />
          <span>判断</span>
        </div>
        <div className="desk-list">
          <div><span>待确认持仓</span><strong>代码 / 金额 / 收益</strong><small>可逐项修改</small></div>
          <div><span>风险提示</span><strong>数据日期与估算口径</strong><small>证据可展开</small></div>
        </div>
      </div>
    </figure>
  );
}

function EditorialHeading({ eyebrow, title, id }: { eyebrow: string; title: string; id?: string }) {
  return <div className="editorial-heading"><p>{eyebrow}</p><h2 id={id} className="font-display">{title}</h2></div>;
}

function TrustLine({ icon: Icon, title, desc }: { icon: typeof ScanLine; title: string; desc: string }) {
  return <article><Icon size={20} /><div><h3>{title}</h3><p>{desc}</p></div></article>;
}
