"use client";

import { useEffect, useState } from "react";
import { Search, X } from "lucide-react";
import type { FundSearchItem } from "@/lib/api";
import { searchFunds } from "@/lib/api";

type FundCodeSearchPanelProps = {
  initialQuery: string;
  onSelect: (item: FundSearchItem) => void;
  onClose: () => void;
};

function FundCodeSearchPanel({ initialQuery, onSelect, onClose }: FundCodeSearchPanelProps) {
  const [query, setQuery] = useState(initialQuery);
  const [items, setItems] = useState<FundSearchItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      if (query.trim().length < 2) {
        setItems([]);
        return;
      }
      setLoading(true);
      setError(null);
      try {
        const results = await searchFunds(query.trim());
        if (!cancelled) {
          setItems(results);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "搜索失败");
          setItems([]);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };
    const timer = window.setTimeout(() => void run(), 280);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [query]);

  return (
    <div className="absolute left-0 right-0 top-full z-30 mt-1 max-h-52 overflow-y-auto rounded-xl border border-slate-200 bg-white shadow-lg">
      <div className="flex items-center justify-between border-b border-slate-100 px-3 py-2">
        <span className="text-[11px] font-semibold text-slate-500">东财基金搜索</span>
        <button type="button" onClick={onClose} className="text-slate-400 hover:text-slate-600" aria-label="关闭">
          <X size={14} />
        </button>
      </div>
      <div className="border-b border-slate-100 px-3 py-2">
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="输入基金名称或代码"
          className="w-full rounded-lg border border-slate-200 px-2 py-1.5 text-xs outline-none focus:border-blue-400"
          autoFocus
        />
      </div>
      {loading ? <div className="px-3 py-3 text-xs text-slate-400">搜索中...</div> : null}
      {error ? <div className="px-3 py-3 text-xs text-rose-600">{error}</div> : null}
      {!loading && !error && items.length === 0 ? (
        <div className="px-3 py-3 text-xs text-slate-400">输入至少 2 个字符</div>
      ) : null}
      {items.map((item) => (
        <button
          key={item.fund_code}
          type="button"
          onClick={() => onSelect(item)}
          className="flex w-full flex-col items-start gap-0.5 border-b border-slate-50 px-3 py-2.5 text-left transition hover:bg-blue-50"
        >
          <span className="text-xs font-bold tabular-nums text-blue-700">{item.fund_code}</span>
          <span className="text-xs text-slate-700">{item.fund_name}</span>
        </button>
      ))}
    </div>
  );
}

export function isProvisionalFundCode(fundCode: string | null | undefined) {
  return Boolean(fundCode && fundCode.length === 6 && fundCode.startsWith("9") && fundCode !== "000000");
}

type FundCodeEditModalProps = {
  open: boolean;
  fundCode: string;
  fundName: string;
  saving?: boolean;
  error?: string | null;
  onClose: () => void;
  onSave: (nextCode: string, nextName: string) => void | Promise<void>;
};

export function FundCodeEditModal({
  open,
  fundCode,
  fundName,
  saving = false,
  error = null,
  onClose,
  onSave,
}: FundCodeEditModalProps) {
  const [code, setCode] = useState(fundCode);
  const [name, setName] = useState(fundName);
  const [searchOpen, setSearchOpen] = useState(false);

  useEffect(() => {
    if (open) {
      setCode(fundCode);
      setName(fundName);
      setSearchOpen(false);
    }
  }, [open, fundCode, fundName]);

  if (!open) {
    return null;
  }

  const normalizedCode = code.replace(/\D/g, "").slice(0, 6);
  const canSave = normalizedCode.length === 6 && name.trim().length > 0 && !saving;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-end justify-center bg-slate-950/50 p-4 sm:items-center"
      onClick={onClose}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="fund-code-edit-title"
        className="w-full max-w-md rounded-[24px] bg-white p-5 shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <h3 id="fund-code-edit-title" className="text-base font-black text-slate-950">
              修正基金代码
            </h3>
            <p className="mt-1 text-xs leading-5 text-slate-500">
              OCR 或名称匹配错误时可手动改码，将从东财档案迁移到正确代码。
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className="inline-flex h-8 w-8 items-center justify-center rounded-full text-slate-400 hover:bg-slate-100"
            aria-label="关闭"
          >
            <X size={18} />
          </button>
        </div>

        <div className="space-y-3">
          <div className="relative">
            <label className="mb-1 block text-[11px] font-semibold text-slate-400">基金代码</label>
            <div className="flex gap-2">
              <input
                value={normalizedCode}
                onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
                inputMode="numeric"
                maxLength={6}
                className="w-28 rounded-xl border border-slate-200 px-3 py-2 text-sm font-black tabular-nums outline-none focus:border-blue-400"
              />
              <button
                type="button"
                onClick={() => setSearchOpen((current) => !current)}
                className="inline-flex items-center gap-1 rounded-xl border border-slate-200 px-3 py-2 text-xs font-semibold text-slate-600 hover:border-blue-300 hover:text-blue-700"
              >
                <Search size={14} />
                搜索选码
              </button>
            </div>
            {searchOpen ? (
              <FundCodeSearchPanel
                initialQuery={name}
                onSelect={(item) => {
                  setCode(item.fund_code);
                  setName(item.fund_name);
                  setSearchOpen(false);
                }}
                onClose={() => setSearchOpen(false)}
              />
            ) : null}
          </div>

          <div>
            <label className="mb-1 block text-[11px] font-semibold text-slate-400">基金名称</label>
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm font-semibold text-slate-900 outline-none focus:border-blue-400"
            />
          </div>
        </div>

        {error ? (
          <div className="mt-3 rounded-xl bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-700">{error}</div>
        ) : null}

        <button
          type="button"
          disabled={!canSave}
          onClick={() => void onSave(normalizedCode, name.trim())}
          className="mt-5 w-full rounded-2xl bg-blue-600 py-3 text-sm font-black text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {saving ? "保存中..." : "保存并更新持仓"}
        </button>
      </div>
    </div>
  );
}
