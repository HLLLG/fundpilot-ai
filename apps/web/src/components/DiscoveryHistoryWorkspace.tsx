"use client";

import type { FundDiscoveryReport } from "@/lib/api";
import { DiscoveryHistoryRail } from "@/components/DiscoveryHistoryRail";
import { HistoryDrawerShell } from "@/components/HistoryDrawerShell";

type DiscoveryHistoryWorkspaceProps = {
  reports: FundDiscoveryReport[];
  activeReportId?: string | null;
  open: boolean;
  onOpen: () => void;
  onClose: () => void;
  onRefresh: () => void | Promise<unknown>;
  onSelect: (report: FundDiscoveryReport, source: "rail" | "drawer") => void;
  onDeleted?: (reportId: string) => void;
};

export function DiscoveryHistoryWorkspace({
  reports,
  activeReportId,
  open,
  onOpen,
  onClose,
  onRefresh,
  onSelect,
  onDeleted,
}: DiscoveryHistoryWorkspaceProps) {
  return (
    <>
      <div className="discovery-history-desktop" data-testid="discovery-history-desktop">
        <DiscoveryHistoryRail
          reports={reports}
          activeReportId={activeReportId}
          onRefresh={() => void onRefresh()}
          onSelect={(report) => onSelect(report, "rail")}
          onDeleted={onDeleted}
          onOpenAll={onOpen}
        />
      </div>

      <HistoryDrawerShell
        open={open}
        onClose={onClose}
        title="历史推荐"
        description="选择后会回到当前发现页，不会清空尚未提交的扫描条件。"
        labelledById="discovery-history-drawer-title"
      >
        <DiscoveryHistoryRail
          reports={reports}
          activeReportId={activeReportId}
          onRefresh={() => void onRefresh()}
          onSelect={(report) => {
            onSelect(report, "drawer");
            onClose();
          }}
          onDeleted={onDeleted}
          variant="drawer"
          initialLimit={20}
        />
      </HistoryDrawerShell>
    </>
  );
}
