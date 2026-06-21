"use client";

import { MessageCircle } from "lucide-react";
import { ReportChatPanel } from "@/components/ReportChatPanel";

type BriefingChatPanelProps = {
  reportId: string;
  reportTitle?: string;
};

export function BriefingChatPanel({ reportId, reportTitle }: BriefingChatPanelProps) {
  return (
    <div className="section-card overflow-hidden">
      <div className="flex items-center gap-2 border-b border-[var(--line)] px-4 py-3.5">
        <MessageCircle size={18} className="text-[var(--brand)]" />
        <h2 className="section-title">AI 追问</h2>
      </div>
      <div className="p-3 sm:p-4">
        <ReportChatPanel reportId={reportId} reportTitle={reportTitle} inline />
      </div>
    </div>
  );
}
