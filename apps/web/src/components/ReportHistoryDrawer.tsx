"use client";

import { FlaskConical } from "lucide-react";
import { useState } from "react";

import type { Report } from "@/lib/api";
import { HistoryDrawerShell } from "@/components/HistoryDrawerShell";
import { HistoryRail } from "@/components/HistoryRail";
import { InlineNotice } from "@/components/InlineNotice";
import { SectorSignalBacktestPanel } from "@/components/SectorSignalBacktestPanel";

type ReportHistoryDrawerProps = {
  open: boolean;
  reports: Report[];
  activeReportId?: string | null;
  loading?: boolean;
  error?: string | null;
  onClose: () => void;
  onRefresh: () => void | Promise<unknown>;
  onSelect: (report: Report) => void;
  onDeleted: (reportId: string) => void;
};

function HistoryResearchDisclosure() {
  const [open, setOpen] = useState(false);

  return (
    <details
      className="history-research-disclosure"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary className="min-h-11">
        <FlaskConical size={16} />
        研究分析与板块回测
      </summary>
      {open ? (
        <div className="pt-3">
          <SectorSignalBacktestPanel title="板块信号历史回测（全部 canonical）" />
        </div>
      ) : null}
    </details>
  );
}

export function ReportHistoryDrawer({
  open,
  reports,
  activeReportId,
  loading = false,
  error,
  onClose,
  onRefresh,
  onSelect,
  onDeleted,
}: ReportHistoryDrawerProps) {
  return (
    <HistoryDrawerShell
      open={open}
      onClose={onClose}
      title="全部历史日报"
      description="按日期连续回看；选择后仍在当前日报阅读区展示。"
      labelledById="report-history-drawer-title"
    >
      {loading && reports.length === 0 ? (
        <div className="history-loading-state" role="status">正在加载历史日报…</div>
      ) : null}
      {error ? (
        <InlineNotice tone="error" message={error} className="mb-3" />
      ) : null}
      <HistoryRail
        reports={reports}
        activeReportId={activeReportId}
        onRefresh={() => void onRefresh()}
        onSelect={(report) => {
          onSelect(report);
          onClose();
        }}
        onDeleted={onDeleted}
      />
      <HistoryResearchDisclosure />
    </HistoryDrawerShell>
  );
}
