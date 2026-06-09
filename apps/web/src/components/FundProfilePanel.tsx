"use client";

import { useState } from "react";
import { BookMarked, FileImage, RefreshCw, Upload } from "lucide-react";
import type { FundProfile } from "@/lib/api";
import { FundProfileCard } from "@/components/FundProfileCard";
import { FundProfileDetailModal } from "@/components/FundProfileDetailModal";

type FundProfilePanelProps = {
  profiles: FundProfile[];
  isBusy: boolean;
  onFileSelect: (file: File) => void;
  onRefresh: () => void;
  onImport?: (file: File) => void;
};

export function FundProfilePanel({
  profiles,
  isBusy,
  onFileSelect,
  onRefresh,
  onImport,
}: FundProfilePanelProps) {
  const [selectedProfile, setSelectedProfile] = useState<FundProfile | null>(null);

  return (
    <section className="glass-panel min-w-0 rounded-[28px] p-6">
      <div className="mb-5 flex items-start justify-between gap-4">
        <div>
          <div className="mb-3 inline-flex h-11 w-11 items-center justify-center rounded-2xl bg-indigo-600 text-white">
            <BookMarked size={22} />
          </div>
          <h2 className="text-xl font-black text-slate-950">基金档案库</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            上传养基宝/支付宝单基金「详情」截图，用于覆盖份额、成本与关联板块。日常持有金额与收益由「今日」账户汇总自动更新。
          </p>
        </div>
        <button
          type="button"
          onClick={onRefresh}
          className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-white text-slate-500 shadow-sm transition hover:text-blue-600"
          aria-label="刷新基金档案"
        >
          <RefreshCw size={17} />
        </button>
      </div>

      <label
        className={`group flex min-h-28 flex-col items-center justify-center rounded-[24px] border border-dashed border-indigo-300 bg-white/80 px-5 py-6 text-center transition hover:border-indigo-500 hover:bg-indigo-50/70 ${isBusy ? "pointer-events-none opacity-60" : ""}`}
      >
        <FileImage className="mb-2 text-indigo-600" size={30} />
        <span className="text-sm font-black text-slate-950">
          {isBusy ? "正在识别截图..." : "上传单基金详情截图"}
        </span>
        <span className="mt-1 text-xs text-slate-500">
          识别基金代码、持有金额、份额、成本、关联板块（首次约 10–30 秒，之后通常数秒）
        </span>
        <input
          type="file"
          accept="image/*"
          className="sr-only"
          disabled={isBusy}
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) {
              onFileSelect(file);
            }
            event.currentTarget.value = "";
          }}
        />
      </label>

      {onImport ? (
        <label className="mt-4 inline-flex w-full cursor-pointer items-center justify-center gap-2 rounded-full border border-slate-200 bg-white px-4 py-2.5 text-sm font-bold text-slate-600 transition hover:border-indigo-300 hover:text-indigo-700">
          <Upload size={16} />
          从 JSON 恢复档案（换机备用）
          <input
            type="file"
            accept="application/json,.json"
            className="sr-only"
            onChange={(event) => {
              const selected = event.target.files?.[0];
              if (selected) {
                onImport(selected);
              }
              event.currentTarget.value = "";
            }}
          />
        </label>
      ) : null}

      <div className="mt-5 space-y-4">
        {profiles.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-slate-200 bg-white px-4 py-5 text-center text-sm text-slate-500">
            暂无档案。请上传单基金详情截图建档。
          </div>
        ) : (
          <div className="space-y-3">
            {profiles.map((profile) => (
              <FundProfileCard
                key={profile.fund_code}
                profile={profile}
                onOpenDetail={() => setSelectedProfile(profile)}
              />
            ))}
          </div>
        )}
      </div>

      {selectedProfile ? (
        <FundProfileDetailModal
          profile={selectedProfile}
          onClose={() => setSelectedProfile(null)}
        />
      ) : null}
    </section>
  );
}
