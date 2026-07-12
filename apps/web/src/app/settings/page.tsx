"use client";

import Link from "next/link";
import { ArrowLeft, Database, Fingerprint, Mail, ShieldCheck, UserRound } from "lucide-react";
import { BrandMark } from "@/components/BrandMark";
import { useAuth } from "@/components/AuthProvider";

export default function SettingsPage() {
  const { user } = useAuth();

  return (
    <main className="settings-shell">
      <header className="settings-masthead">
        <div className="mx-auto flex h-full max-w-[1120px] items-center justify-between px-4 sm:px-6">
          <BrandMark size="sm" showEnglish />
          <Link href="/" className="btn-ghost min-h-11 px-3"><ArrowLeft size={16} />返回研究台</Link>
        </div>
      </header>

      <div className="mx-auto grid max-w-[1120px] gap-8 px-4 py-8 sm:px-6 lg:grid-cols-[240px_minmax(0,1fr)] lg:py-12">
        <aside className="settings-index">
          <p className="research-kicker">SETTINGS</p>
          <h1 className="font-display">账号与数据</h1>
          <p>查看身份、隐私边界与当前数据状态。账号信息暂为只读。</p>
          <nav aria-label="设置分组">
            <a href="#account">账号身份</a>
            <a href="#privacy">隐私与数据</a>
            <a href="#danger">危险操作</a>
          </nav>
        </aside>

        <div className="settings-content">
          <section id="account" className="settings-section" aria-labelledby="account-title">
            <div className="settings-section-head">
              <div><p>01 / IDENTITY</p><h2 id="account-title">账号身份</h2></div>
              <span className="settings-status">已同步 · 只读</span>
            </div>
            <div className="settings-data-list">
              <SettingsRow icon={UserRound} label="用户名" value={user?.username || "未命名用户"} />
              <SettingsRow icon={Mail} label="登录邮箱" value={user?.userAccount || "未登录"} breakAll />
            </div>
          </section>

          <section id="privacy" className="settings-section" aria-labelledby="privacy-title">
            <div className="settings-section-head"><div><p>02 / DATA</p><h2 id="privacy-title">隐私与数据</h2></div></div>
            <div className="settings-note-grid">
              <article><Fingerprint size={20} /><h3>账号隔离</h3><p>持仓与报告按登录账号隔离，不与其他用户混用。</p></article>
              <article><Database size={20} /><h3>数据口径</h3><p>页面持续标注数据日期、实时或估算状态，缺失信息不会伪造补齐。</p></article>
              <article><ShieldCheck size={20} /><h3>分析边界</h3><p>系统不代替用户交易，也不改变既有风控阈值。</p></article>
            </div>
          </section>

          <section id="danger" className="settings-section settings-danger" aria-labelledby="danger-title">
            <div className="settings-section-head"><div><p>03 / CAREFUL</p><h2 id="danger-title">危险操作</h2></div></div>
            <div className="settings-danger-row">
              <div><h3>清除账户数据</h3><p>当前版本暂未开放自助清除入口，避免误操作。需要处理时请联系服务维护者。</p></div>
              <button type="button" disabled className="btn-secondary min-h-11">暂不可用</button>
            </div>
          </section>
        </div>
      </div>
    </main>
  );
}

function SettingsRow({ icon: Icon, label, value, breakAll = false }: { icon: typeof UserRound; label: string; value: string; breakAll?: boolean }) {
  return <div><Icon size={18} /><span>{label}</span><strong className={breakAll ? "break-all" : "break-words"}>{value}</strong></div>;
}
