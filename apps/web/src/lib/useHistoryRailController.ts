"use client";

import { useEffect, useMemo, useState } from "react";

export type HistoryRailItem = {
  id: string;
  title: string;
  created_at: string;
};

export type HistoryDeleteIntent<T extends HistoryRailItem> =
  | { kind: "single"; reports: [T] }
  | { kind: "batch"; reports: T[] };

export type HistoryDeleteFeedback = {
  message: string;
  tone: "error" | "warning";
};

type UseHistoryRailControllerOptions<T extends HistoryRailItem> = {
  reports: T[];
  activeReportId?: string | null;
  initialLimit: number;
  getSearchText: (report: T) => string;
  deleteItem: (reportId: string) => Promise<void>;
  onRefresh: () => void | Promise<unknown>;
  onDeleted?: (reportId: string) => void;
};

export function useHistoryRailController<T extends HistoryRailItem>({
  reports,
  activeReportId,
  initialLimit,
  getSearchText,
  deleteItem,
  onRefresh,
  onDeleted,
}: UseHistoryRailControllerOptions<T>) {
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [batchMode, setBatchMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [batchDeleting, setBatchDeleting] = useState(false);
  const [deleteIntent, setDeleteIntent] = useState<HistoryDeleteIntent<T> | null>(null);
  const [deleteFeedback, setDeleteFeedback] = useState<HistoryDeleteFeedback | null>(null);
  const [visibleCount, setVisibleCount] = useState(initialLimit);
  const [query, setQuery] = useState("");

  const selectedCount = selectedIds.size;
  const allSelected = reports.length > 0 && selectedCount === reports.length;
  const filteredReports = useMemo(() => {
    const keyword = query.trim().toLocaleLowerCase("zh-CN");
    if (!keyword) return reports;
    return reports.filter((report) =>
      getSearchText(report).toLocaleLowerCase("zh-CN").includes(keyword),
    );
  }, [getSearchText, query, reports]);
  const visibleReports = useMemo(() => {
    if (batchMode) return filteredReports;
    const visible = filteredReports.slice(0, visibleCount);
    const active = filteredReports.find((report) => report.id === activeReportId);
    if (active && !visible.some((report) => report.id === active.id)) visible.push(active);
    return visible;
  }, [activeReportId, batchMode, filteredReports, visibleCount]);
  const hasMore = !batchMode && visibleCount < filteredReports.length;

  useEffect(() => {
    setVisibleCount(initialLimit);
  }, [initialLimit, query, reports.length]);

  useEffect(() => {
    const currentIds = new Set(reports.map((report) => report.id));
    setSelectedIds((previous) => {
      if (previous.size === 0 || [...previous].every((reportId) => currentIds.has(reportId))) {
        return previous;
      }
      return new Set([...previous].filter((reportId) => currentIds.has(reportId)));
    });
  }, [reports]);

  const enterBatchMode = () => setBatchMode(true);

  const exitBatchMode = () => {
    setBatchMode(false);
    setSelectedIds(new Set());
  };

  const toggleSelected = (reportId: string) => {
    setSelectedIds((previous) => {
      const next = new Set(previous);
      if (next.has(reportId)) {
        next.delete(reportId);
      } else {
        next.add(reportId);
      }
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (allSelected) {
      setSelectedIds(new Set());
      return;
    }
    setSelectedIds(new Set(reports.map((report) => report.id)));
  };

  const requestSingleDelete = (report: T) => {
    setDeleteIntent({ kind: "single", reports: [report] });
  };

  const requestBatchDelete = () => {
    if (selectedCount === 0) {
      return;
    }
    const selectedReports = reports.filter((report) => selectedIds.has(report.id));
    if (selectedReports.length === 0) {
      return;
    }
    setDeleteIntent({ kind: "batch", reports: selectedReports });
  };

  const closeDeleteDialog = () => setDeleteIntent(null);

  const confirmDelete = async () => {
    const intent = deleteIntent;
    if (!intent) {
      return;
    }
    setDeleteFeedback(null);
    setDeleteIntent(null);

    if (intent.kind === "single") {
      const report = intent.reports[0];
      setDeletingId(report.id);
      try {
        await deleteItem(report.id);
        onDeleted?.(report.id);
        await onRefresh();
      } catch {
        setDeleteFeedback({ message: "删除失败，请稍后重试。", tone: "error" });
      } finally {
        setDeletingId(null);
      }
      return;
    }

    setBatchDeleting(true);
    try {
      const selectedIdList = intent.reports.map((report) => report.id);
      const results = await Promise.allSettled(selectedIdList.map((reportId) => deleteItem(reportId)));
      const failed = results.filter((result) => result.status === "rejected").length;
      const succeededIds = selectedIdList.filter(
        (_, index) => results[index].status === "fulfilled",
      );
      for (const reportId of succeededIds) {
        onDeleted?.(reportId);
      }
      await onRefresh();
      exitBatchMode();
      if (failed > 0) {
        setDeleteFeedback({
          message: `${failed} 份删除失败，其余已删除。可重新选择失败项后重试。`,
          tone: "warning",
        });
      }
    } catch {
      setDeleteFeedback({ message: "批量删除失败，请稍后重试。", tone: "error" });
    } finally {
      setBatchDeleting(false);
    }
  };

  const showMore = () => {
    setVisibleCount((count) => Math.min(count + initialLimit, filteredReports.length));
  };

  return {
    allSelected,
    batchDeleting,
    batchMode,
    closeDeleteDialog,
    confirmDelete,
    deleteFeedback,
    deleteIntent,
    deletingId,
    enterBatchMode,
    exitBatchMode,
    filteredReports,
    hasMore,
    query,
    requestBatchDelete,
    requestSingleDelete,
    selectedCount,
    selectedIds,
    setQuery,
    showMore,
    toggleSelectAll,
    toggleSelected,
    visibleCount,
    visibleReports,
  };
}
