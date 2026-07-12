"use client";

import { useEffect, useRef, useState } from "react";
import { Search, X } from "lucide-react";
import type { FundSearchItem } from "@/lib/api";
import { searchFunds } from "@/lib/api";
import { useDialogA11y } from "@/lib/useDialogA11y";

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
    <div
      id="fund-code-search-panel"
      role="region"
      aria-label="基金搜索结果"
      className="absolute left-0 right-0 top-full z-30 mt-1 max-h-52 overflow-y-auto rounded-xl border border-slate-200 bg-white shadow-lg"
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation();
          onClose();
        }
      }}
    >
      <div className="flex items-center justify-between border-b border-slate-100 px-3 py-2">
        <span className="text-[11px] font-semibold text-slate-500">东财基金搜索</span>
        <button
          type="button"
          onClick={onClose}
          className="touch-target -mr-2 inline-flex items-center justify-center rounded-full text-slate-500 hover:bg-slate-100 hover:text-slate-600"
          aria-label="关闭基金搜索"
        >
          <X size={14} />
        </button>
      </div>
      <div className="border-b border-slate-100 px-3 py-2">
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          aria-label="搜索基金"
          placeholder="输入基金名称或代码"
          className="min-h-11 w-full rounded-lg border border-slate-200 px-2 py-1.5 text-xs outline-none focus:border-blue-400"
          autoFocus
        />
      </div>
      {loading ? <div className="px-3 py-3 text-xs text-slate-500">搜索中...</div> : null}
      {error ? (
        <div role="alert" className="px-3 py-3 text-xs text-rose-700">
          {error}
        </div>
      ) : null}
      {!loading && !error && items.length === 0 ? (
        <div className="px-3 py-3 text-xs text-slate-500">输入至少 2 个字符</div>
      ) : null}
      {items.map((item) => (
        <button
          key={item.fund_code}
          type="button"
          onClick={() => onSelect(item)}
          aria-label={`选择 ${item.fund_name}（${item.fund_code}）`}
          className="flex min-h-11 w-full flex-col items-start justify-center gap-0.5 border-b border-slate-50 px-3 py-2.5 text-left transition hover:bg-blue-50"
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
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const searchTriggerRef = useRef<HTMLButtonElement>(null);
  const requestClose = () => {
    if (!saving) {
      onClose();
    }
  };
  const dialogRef = useDialogA11y<HTMLDivElement>({
    open,
    onClose: requestClose,
    initialFocusRef: closeButtonRef,
    closeOnEscape: !searchOpen,
  });

  const closeSearch = () => {
    setSearchOpen(false);
    window.requestAnimationFrame(() => searchTriggerRef.current?.focus());
  };

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
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          requestClose();
        }
      }}
      role="presentation"
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="fund-code-edit-title"
        aria-describedby="fund-code-edit-description"
        className="w-full max-w-md rounded-[24px] bg-white p-5 shadow-2xl"
      >
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <h2 id="fund-code-edit-title" className="text-base font-black text-slate-950">
              修正基金代码
            </h2>
            <p id="fund-code-edit-description" className="mt-1 text-xs leading-5 text-slate-500">
              OCR 或名称匹配错误时可手动改码，将从东财档案迁移到正确代码。
            </p>
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            onClick={requestClose}
            disabled={saving}
            className="touch-target inline-flex items-center justify-center rounded-full text-slate-500 hover:bg-slate-100"
            aria-label="关闭"
          >
            <X size={18} />
          </button>
        </div>

        <div className="space-y-3">
          <div className="relative">
            <label htmlFor="fund-code-edit-code" className="mb-1 block text-[11px] font-semibold text-slate-500">
              基金代码
            </label>
            <div className="flex gap-2">
              <input
                id="fund-code-edit-code"
                value={normalizedCode}
                onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
                inputMode="numeric"
                maxLength={6}
                className="min-h-11 w-28 rounded-xl border border-slate-200 px-3 py-2 text-sm font-black tabular-nums outline-none focus:border-blue-400"
              />
              <button
                ref={searchTriggerRef}
                type="button"
                onClick={() => setSearchOpen((current) => !current)}
                aria-expanded={searchOpen}
                aria-controls="fund-code-search-panel"
                className="inline-flex min-h-11 items-center gap-1 rounded-xl border border-slate-200 px-3 py-2 text-xs font-semibold text-slate-600 hover:border-blue-300 hover:text-blue-700"
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
                  closeSearch();
                }}
                onClose={closeSearch}
              />
            ) : null}
          </div>

          <div>
            <label htmlFor="fund-code-edit-name" className="mb-1 block text-[11px] font-semibold text-slate-500">
              基金名称
            </label>
            <input
              id="fund-code-edit-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              className="min-h-11 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm font-semibold text-slate-900 outline-none focus:border-blue-400"
            />
          </div>
        </div>

        {error ? (
          <div className="mt-3 rounded-xl bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-700" role="alert">
            {error}
          </div>
        ) : null}

        <button
          type="button"
          disabled={!canSave}
          onClick={() => void onSave(normalizedCode, name.trim())}
          className="mt-5 min-h-11 w-full rounded-2xl bg-blue-600 py-3 text-sm font-black text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {saving ? "保存中..." : "保存并更新持仓"}
        </button>
      </div>
    </div>
  );
}
