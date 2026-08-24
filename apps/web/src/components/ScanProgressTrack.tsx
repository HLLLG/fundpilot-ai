"use client";

import { Check, X } from "lucide-react";
import type { DiscoveryScanProgress, DiscoveryScanTrack } from "@/lib/discoveryScanProgress";

type ScanProgressTrackProps = {
  progress: DiscoveryScanProgress;
  track: DiscoveryScanTrack;
  eyebrow: string;
  ariaName: string;
  testId: string;
  stepTestIdPrefix: string;
  faultHint: string;
};

function padCount(value: number): string {
  return String(value).padStart(2, "0");
}

export function ScanProgressTrack({
  progress,
  track,
  eyebrow,
  ariaName,
  testId,
  stepTestIdPrefix,
  faultHint,
}: ScanProgressTrackProps) {
  const failed = progress.status === "failed";

  return (
    <div
      className="discovery-scan-chart"
      data-testid={testId}
      data-status={progress.status}
      role="status"
      aria-live="polite"
      aria-label={`${ariaName}，第 ${track.reachedCount} 步，共 ${track.total} 步。${track.headline}`}
    >
      <div className="discovery-scan-chart-head">
        <div className="min-w-0">
          <p className="discovery-scan-chart-eyebrow">{eyebrow}</p>
          <p className="discovery-scan-chart-kicker">{track.headline}</p>
        </div>
        <p className="discovery-scan-chart-count" aria-hidden="true">
          <strong>{padCount(track.reachedCount)}</strong>
          <span>/ {padCount(track.total)}</span>
        </p>
      </div>

      <div className="discovery-scan-chart-track">
        <span className="discovery-scan-chart-rail" aria-hidden="true">
          <span
            className="discovery-scan-chart-fill"
            style={{ width: `${track.fillPercent}%` }}
          />
        </span>
        <ol className="discovery-scan-chart-steps">
          {track.nodes.map((node) => (
            <li
              key={node.id}
              className="discovery-scan-chart-step"
              data-state={node.state}
              data-testid={`${stepTestIdPrefix}-${node.id}`}
              aria-current={node.state === "current" ? "step" : undefined}
            >
              <span className="discovery-scan-chart-node" aria-hidden="true">
                {node.state === "done" ? <Check size={11} strokeWidth={3} /> : null}
                {node.state === "failed" ? <X size={11} strokeWidth={3} /> : null}
              </span>
              <span className="discovery-scan-chart-label">{node.label}</span>
              <span className="discovery-scan-chart-hint">{node.hint}</span>
            </li>
          ))}
        </ol>
      </div>

      {failed ? <p className="discovery-scan-chart-fault">{faultHint}</p> : null}
    </div>
  );
}
