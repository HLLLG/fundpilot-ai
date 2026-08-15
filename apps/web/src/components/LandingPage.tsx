import Link from "next/link";
import { ArrowRight, FileCheck2, ScanLine } from "lucide-react";
import { BrandMark } from "@/components/BrandMark";
import { BRAND, SITE_REGISTRATION } from "@/lib/brand";

export function LandingPage() {
  return (
    <div className="landing-hero-bg min-h-screen overflow-x-clip">
      <header className="landing-masthead">
        <div className="mx-auto flex h-full max-w-[1240px] items-center justify-between px-4 sm:px-6">
          <BrandMark size="md" showEnglish />
          <nav aria-label="账号入口" className="flex items-center gap-1">
            <Link href="/login" prefetch={false} className="btn-ghost px-3">登录</Link>
            <Link href="/register" prefetch={false} className="btn-ghost px-3">注册</Link>
          </nav>
        </div>
      </header>

      <div className="mx-auto w-full max-w-[1240px] px-4 sm:px-6">
        <main>
          <section
            aria-labelledby="landing-title"
            className="landing-editorial-hero"
            data-testid="landing-hero"
          >
            <div className="landing-hero-copy">
              <p className="research-kicker">{BRAND.englishName}</p>
              <h1 id="landing-title" className="font-display landing-title">
                截个图，<span>就懂你的基金</span>
              </h1>
              <div className="mt-8 flex w-full flex-col gap-3 sm:w-auto sm:flex-row">
                <Link
                  href="/register"
                  prefetch={false}
                  className="btn-primary min-h-11 w-full justify-center sm:w-auto"
                  data-testid="landing-primary-cta"
                >
                  注册 <ArrowRight size={17} aria-hidden="true" />
                </Link>
                <Link
                  href="/login"
                  prefetch={false}
                  className="btn-secondary min-h-11 w-full justify-center sm:w-auto"
                >
                  登录
                </Link>
              </div>
            </div>

            <ResearchDeskPreview />
          </section>
        </main>

        <footer className="landing-footer">
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
        </footer>
      </div>
    </div>
  );
}

function ResearchDeskPreview() {
  return (
    <figure className="research-desk-preview">
      <div className="desk-window">
        <div className="desk-window-head">
          <BrandMark size="sm" />
          <span>今日研究摘要</span>
        </div>
        <div className="desk-status">
          <p>组合状态</p>
          <strong>先校对，再判断</strong>
        </div>
        <div className="desk-track" aria-hidden="true">
          <span className="done"><ScanLine size={15} />截图</span>
          <i />
          <span className="active"><FileCheck2 size={15} />校对</span>
          <i />
          <span>判断</span>
        </div>
      </div>
    </figure>
  );
}
