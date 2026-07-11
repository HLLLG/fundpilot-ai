import { translateEvidenceText } from "@/lib/decisionText";

type DecisionEvidenceGridProps = {
  sectorEvidence?: string[];
  fundEvidence?: string[];
  validationNotes?: string[];
  className?: string;
};

/**
 * Shared "决策路径证据链" grid: 板块依据 / 基金依据 / 校验备注。
 * Used by both 荐基 (DiscoveryReportPanel) and 日报 (ReportPanel) so the two
 * decision-explainability surfaces stay visually and semantically consistent.
 */
export function DecisionEvidenceGrid({
  sectorEvidence,
  fundEvidence,
  validationNotes,
  className,
}: DecisionEvidenceGridProps) {
  const groups = [
    ["板块依据", sectorEvidence],
    ["基金依据", fundEvidence],
    ["校验备注", validationNotes],
  ] as const;
  if (!groups.some(([, items]) => items?.length)) {
    return null;
  }
  return (
    <div className={`grid gap-2 md:grid-cols-3 ${className ?? ""}`}>
      {groups.map(([title, items]) =>
        items?.length ? (
          <div
            key={title}
            className={
              title === "校验备注"
                ? "min-w-0 rounded-xl border border-amber-100 bg-amber-50/70 px-3 py-2.5"
                : "min-w-0 rounded-xl border border-slate-100 bg-slate-50/80 px-3 py-2.5"
            }
          >
            <div className={title === "校验备注" ? "text-xs font-black text-amber-900" : "text-xs font-black text-slate-800"}>
              {title}
            </div>
            <ul className={title === "校验备注" ? "mt-1.5 space-y-1 text-xs leading-5 text-amber-900" : "mt-1.5 space-y-1 text-xs leading-5 text-slate-600"}>
              {items.slice(0, 3).map((item) => (
                <li className="break-words [overflow-wrap:anywhere]" key={item}>· {translateEvidenceText(item)}</li>
              ))}
            </ul>
          </div>
        ) : null,
      )}
    </div>
  );
}
