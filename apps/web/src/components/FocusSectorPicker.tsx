"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { ChevronDown, Loader2, Search, X } from "lucide-react";
import type { DiscoverySectorHeat } from "@/lib/api";

const MAX_FOCUS = 3;

type FocusSectorPickerProps = {
  selected: string[];
  onChange: (sectors: string[]) => void;
  allLabels: string[];
  heatRows?: DiscoverySectorHeat[];
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
};

function heatMapFromRows(rows: DiscoverySectorHeat[]): Map<string, DiscoverySectorHeat> {
  const map = new Map<string, DiscoverySectorHeat>();
  for (const row of rows) {
    if (row.sector_label) {
      map.set(row.sector_label, row);
    }
  }
  return map;
}

export function FocusSectorPicker({
  selected,
  onChange,
  allLabels,
  heatRows = [],
  loading = false,
  error = null,
  onRetry,
}: FocusSectorPickerProps) {
  const listId = useId();
  const containerRef = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);

  const heatByLabel = useMemo(() => heatMapFromRows(heatRows), [heatRows]);

  const optionLabels = useMemo(() => {
    const seen = new Set<string>();
    const merged: string[] = [];
    for (const label of [...selected, ...allLabels]) {
      const trimmed = label.trim();
      if (!trimmed || seen.has(trimmed)) {
        continue;
      }
      seen.add(trimmed);
      merged.push(trimmed);
    }
    return merged.sort((a, b) => a.localeCompare(b, "zh-CN"));
  }, [allLabels, selected]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const pool = optionLabels.filter((label) => !selected.includes(label));
    if (!q) {
      return pool;
    }
    return pool.filter((label) => label.toLowerCase().includes(q));
  }, [optionLabels, query, selected]);

  useEffect(() => {
    const onPointerDown = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, []);

  const addLabel = (label: string) => {
    if (selected.includes(label) || selected.length >= MAX_FOCUS) {
      return;
    }
    onChange([...selected, label]);
    setQuery("");
    setOpen(false);
  };

  const removeLabel = (label: string) => {
    onChange(selected.filter((item) => item !== label));
  };

  const formatHeat = (label: string) => {
    const heat = heatByLabel.get(label);
    const change = heat?.change_1d_percent;
    if (change == null) {
      return null;
    }
    return `${change >= 0 ? "+" : ""}${change.toFixed(2)}%`;
  };

  if (loading && allLabels.length === 0) {
    return (
      <div className="flex items-center gap-2 text-xs text-slate-500">
        <Loader2 size={14} className="animate-spin" />
        加载板块列表…
      </div>
    );
  }

  if (error && allLabels.length === 0) {
    return (
      <div className="rounded-xl border border-red-100 bg-red-50 px-3 py-2 text-xs text-red-700">
        <p>{error}</p>
        {onRetry ? (
          <button
            type="button"
            onClick={onRetry}
            className="mt-2 inline-flex min-h-11 items-center rounded-lg px-2 font-semibold text-red-800 underline"
          >
            重试
          </button>
        ) : null}
      </div>
    );
  }

  return (
    <div ref={containerRef} className="space-y-2">
      {selected.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {selected.map((label) => (
            <button
              key={label}
              type="button"
              aria-label={`取消关注 ${label}`}
              onClick={() => removeLabel(label)}
              className="inline-flex min-h-11 items-center gap-1 rounded-full border border-[var(--brand)] bg-[var(--brand-soft)] px-3 text-xs font-medium text-[var(--brand-strong)] hover:bg-blue-100"
            >
              {label}
              <X className="h-3 w-3" aria-hidden="true" />
            </button>
          ))}
        </div>
      ) : (
        <p className="text-[11px] text-slate-500">未选择时将按板块热度自动扫描</p>
      )}

      <div className="relative">
        <div className="flex min-h-11 items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3">
          <Search className="h-4 w-4 shrink-0 text-slate-500" />
          <input
            type="text"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && filtered[0]) {
                event.preventDefault();
                addLabel(filtered[0]);
              }
              if (event.key === "Escape") {
                setOpen(false);
              }
            }}
            placeholder={
              selected.length >= MAX_FOCUS
                ? "已达 3 个上限，请先取消"
                : `搜索或浏览全部 ${optionLabels.length} 个板块…`
            }
            disabled={selected.length >= MAX_FOCUS}
            className="min-h-11 min-w-0 flex-1 bg-transparent text-sm text-slate-800 outline-none placeholder:text-slate-500 disabled:cursor-not-allowed"
            role="combobox"
            aria-label="搜索关注方向"
            aria-expanded={open && selected.length < MAX_FOCUS}
            aria-autocomplete="list"
            aria-controls={listId}
            autoComplete="off"
          />
          <ChevronDown className={`h-4 w-4 text-slate-500 transition ${open ? "rotate-180" : ""}`} />
        </div>

        {open && selected.length < MAX_FOCUS ? (
          <ul
            id={listId}
            role="listbox"
            className="absolute z-20 mt-1 max-h-64 w-full overflow-y-auto rounded-xl border border-slate-200 bg-white py-1 shadow-lg"
          >
            {filtered.length === 0 ? (
              <li className="px-3 py-2 text-xs text-slate-500">无匹配板块</li>
            ) : (
              filtered.map((label) => {
                const heatText = formatHeat(label);
                return (
                  <li key={label}>
                    <button
                      type="button"
                      role="option"
                      aria-selected={false}
                      className="flex min-h-11 w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm text-slate-700 hover:bg-slate-50"
                      onClick={() => addLabel(label)}
                    >
                      <span>{label}</span>
                      {heatText ? (
                        <span className="shrink-0 text-xs tabular-nums text-slate-500">{heatText}</span>
                      ) : null}
                    </button>
                  </li>
                );
              })
            )}
          </ul>
        ) : null}
      </div>

      <p className="text-[11px] leading-5 text-slate-500">
        共 {optionLabels.length} 个主题板块可选；也可在市场 → 主题板块点击「加入关注方向」
      </p>
    </div>
  );
}
