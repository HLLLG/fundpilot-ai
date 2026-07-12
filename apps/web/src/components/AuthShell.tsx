import Link from "next/link";
import { Check, LockKeyhole, ScanLine } from "lucide-react";
import { BrandMark } from "@/components/BrandMark";
import { OCR_PRIVACY_COPY } from "@/lib/ocrPrivacy";

export function AuthShell({
  mode,
  children,
}: {
  mode: "login" | "register";
  children: React.ReactNode;
}) {
  return (
    <main className="auth-shell">
      <section className="auth-atmosphere" aria-label="产品说明">
        <Link href="/" className="auth-brand-link"><BrandMark size="lg" showEnglish /></Link>
        <div className="auth-editorial-copy">
          <p className="research-kicker">DEEP-SEA RESEARCH DESK</p>
          <h2 className="font-display">把持仓放回清晰的判断轨道</h2>
          <p>截图进入、逐项校对、风险判断。每一步都保留数据日期、来源和恢复动作。</p>
          <div className="auth-product-slice" aria-hidden="true">
            <div><span>01</span><strong>截图进入</strong><Check size={15} /></div>
            <div className="active"><span>02</span><strong>逐项校对</strong><ScanLine size={15} /></div>
            <div><span>03</span><strong>形成判断</strong><LockKeyhole size={15} /></div>
          </div>
        </div>
        <p className="auth-privacy-note">{OCR_PRIVACY_COPY.shortLabel} · 服务端持仓按账号隔离</p>
      </section>
      <section className="auth-form-pane">
        <div className="auth-mobile-brand"><Link href="/"><BrandMark size="md" showEnglish /></Link></div>
        <div className={`auth-form-wrap auth-form-${mode}`}>{children}</div>
        <Link href="/" className="auth-back-link">← 返回首页</Link>
      </section>
    </main>
  );
}
