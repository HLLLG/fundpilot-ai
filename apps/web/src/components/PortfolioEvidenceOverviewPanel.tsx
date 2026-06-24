"use client";

import { useEffect, useState } from "react";
import type { PortfolioEvidenceOverview } from "@/lib/api";
import { fetchPortfolioEvidenceOverview } from "@/lib/api";
import { StatusPill } from "@/components/StatusPill";
import { confidenceTone } from "@/components/SectorSignalBacktestPanel";

const LEVEL_ORDER = ["高", "中", "低", "不足"] as const;

const TONE_CLASS: Record<string, string> = {
  green: "good",
  amber: "warn",
  red: "danger",
  blue: "neutral",
};

function LevelBar({ level, percent }: { level: string; percent: number }) {
  const tone = confidenceTone(level);
  const width = Math.max(2, Math.min(100, percent));
  return (
    <div className={`factor-bar factor-tone-${TONE_CLASS[tone] ?? "neutral"}`}>
      <div className="factor-bar-head">
        <span className="factor-bar-label">综合置信 {level}</span>
        <span className="factor-bar-value">{percent.toFixed(0)}%</span>
      </div>
      <div className="factor-bar-track">
        <div className="factor-bar-fill" style={{ width: `${width}%` }} />
      </div>
    </div>
  );
}

export function PortfolioEvidenceOverviewPanel({ enabled }: { enabled: boolean }) {
  const [data, setData] = useState<PortfolioEvidenceOverview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled || data || loading) {
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchPortfolioEvidenceOverview()
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "加载证据总览失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [enabled, data, loading]);

  if (!enabled) {
    return null;
  }
  if (loading) {
    return <div className="factor-note">正在聚合持仓量化证据…</div>;
  }
  if (error) {
    return <div className="factor-note">证据总览加载失败：{error}</div>;
  }
  if (!data || !data.available || !data.overview.available) {
    return <div className="factor-note">暂无足够量化证据可聚合（需因子/信号/风险至少一路覆盖）。</div>;
  }

  const ov = data.overview;
  const weights = ov.weight_by_level ?? {};

  return (
    <div className="factor-panel-body">
      <div className="factor-fund-summary" style={{ marginBottom: 12 }}>
        <span style={{ fontSize: 22, fontWeight: 900 }}>
          {(ov.backed_weight_percent ?? 0).toFixed(0)}%
        </span>{" "}
        市值有中/高量化背书 · {ov.summary}
      </div>
      <div className="factor-bars">
        {LEVEL_ORDER.filter((lv) => (weights[lv] ?? 0) > 0).map((lv) => (
          <LevelBar key={lv} level={lv} percent={weights[lv] ?? 0} />
        ))}
      </div>
      <div className="factor-cards" style={{ marginTop: 12 }}>
        {data.holdings.map((h) => (
          <div key={h.fund_code} className="factor-fund-card" title={h.evidence.summary}>
            <div className="factor-fund-head">
              <StatusPill tone={confidenceTone(h.evidence.composite.level)}>
                {h.evidence.composite.level}
              </StatusPill>
              <div className="factor-fund-meta">
                <div className="factor-fund-name">{h.fund_name || h.fund_code}</div>
                <div className="factor-fund-score">{h.evidence.summary}</div>
              </div>
            </div>
          </div>
        ))}
      </div>
      <div className="factor-foot">
        证据覆盖 {ov.covered_holdings}/{ov.total_holdings} 只 · 三路量化置信（因子IC/板块信号/风险样本）市值加权
      </div>
    </div>
  );
}
