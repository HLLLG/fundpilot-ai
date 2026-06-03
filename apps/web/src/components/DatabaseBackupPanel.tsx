"use client";

import { useRef, useState } from "react";
import { Database, Download, Upload } from "lucide-react";
import { exportDatabase, importDatabase } from "@/lib/api";

type DatabaseBackupPanelProps = {
  onImported?: () => void;
};

export function DatabaseBackupPanel({ onImported }: DatabaseBackupPanelProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const handleExport = async () => {
    setBusy(true);
    setMessage(null);
    try {
      await exportDatabase();
      setMessage("数据库已下载（fundpilot-app.db）。");
    } catch (err: unknown) {
      setMessage(err instanceof Error ? err.message : "导出失败");
    } finally {
      setBusy(false);
    }
  };

  const handleImport = async (file: File) => {
    setBusy(true);
    setMessage(null);
    try {
      const result = await importDatabase(file);
      setMessage(
        result.backup_path
          ? `导入成功。原库已备份至 ${result.backup_path}`
          : "导入成功。",
      );
      onImported?.();
    } catch (err: unknown) {
      setMessage(err instanceof Error ? err.message : "导入失败");
    } finally {
      setBusy(false);
      if (inputRef.current) {
        inputRef.current.value = "";
      }
    }
  };

  return (
    <div className="rounded-[24px] border border-slate-200 bg-white p-5">
      <div className="mb-3 flex items-center gap-2 text-sm font-black text-slate-950">
        <Database size={18} className="text-slate-600" />
        本地数据库备份
      </div>
      <p className="mb-4 text-xs leading-5 text-slate-600">
        导出完整 SQLite（含历史日报、档案、对话）。导入会自动备份当前库并替换，适合换机恢复。
      </p>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => void handleExport()}
          className="inline-flex items-center gap-2 rounded-xl bg-slate-900 px-4 py-2 text-xs font-bold text-white hover:bg-slate-800 disabled:opacity-60"
        >
          <Download size={14} />
          导出数据库
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => inputRef.current?.click()}
          className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2 text-xs font-bold text-slate-700 hover:bg-slate-50 disabled:opacity-60"
        >
          <Upload size={14} />
          导入数据库
        </button>
        <input
          ref={inputRef}
          type="file"
          accept=".db,application/octet-stream"
          className="hidden"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void handleImport(file);
          }}
        />
      </div>
      {message ? <p className="mt-3 text-xs font-semibold text-slate-600">{message}</p> : null}
    </div>
  );
}
