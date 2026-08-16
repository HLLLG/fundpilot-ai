import { API_BASE, apiFetch } from "@/lib/api/core";


export type FactorIcStatus = {
  available: boolean;
  snapshot_id?: string | null;
  schema_version?: number;
  upgrade_required?: boolean;
  expected_universe_size?: number;
  run_date?: string;
  generated_at?: string;
  published_at?: string | null;
  age_days?: number;
  stale?: boolean;
  stale_after_days: number;
  source: "database" | "local_file" | "unavailable";
  target_universe_size?: number | null;
  universe_size?: number | null;
  universe_mode?: string | null;
  rebalance_count?: number | null;
  factor_periods?: Record<string, number | null>;
  cohort_mode?: "current_survivors" | "point_in_time" | string | null;
  point_in_time?: {
    snapshot_id?: string | null;
    snapshot_date?: string | null;
    effective_anchor_count?: number;
    anchor_coverage_rate?: number;
    cohort_nav_coverage_rate?: number;
    publishable?: boolean;
    point_in_time_scope?: "membership_only" | "nav_observation_pit" | string;
    nav_revision_pit?: boolean;
    nav_publication_lag_trading_days?: Record<string, number>;
    execution_entry_offset_trading_days?: number;
    mature_anchor_count_by_horizon?: Record<string, number>;
  } | null;
  pit_upgrade?: {
    state?: "collecting" | "unavailable" | "active" | string;
    snapshot_count?: number;
    effective_anchor_count?: number;
    anchor_coverage_rate?: number;
    cohort_nav_coverage_rate?: number;
    reason?: string;
  } | null;
  pit_coverage?: Record<string, unknown> | null;
  source_commit?: string | null;
};


export type EvidenceMaturityAlert = {
  code: string;
  severity: "critical" | "warning" | "info" | string;
  title: string;
  message: string;
  action: string;
};


export type EvidenceMaturityMilestone = {
  code: string;
  label: string;
  observed: number | null;
  required: number | null;
  unit: string;
  progress_percent: number | null;
  theoretical_minimum_trading_days?: number;
  theoretical_minimum_months?: number;
};


/**
 * 阻塞类型：`blocked_on_time` 会随采集自愈；`blocked_on_data_source` 等待无用，
 * 必须补数据源或改口径；`blocked_unclassified` 表示原因码尚未归类，不得当作会自愈。
 */
export type EvidenceMaturityBlockerKind =
  | "not_blocked"
  | "blocked_on_time"
  | "blocked_on_data_source"
  /** 消费方仍在读上游已移除的输入：等和买数据都无效，只能改代码。 */
  | "blocked_on_removed_input"
  /** 不是缺口：候选没过门禁属于系统正常工作，无需行动。 */
  | "excluded_by_design"
  | "blocked_unclassified";

export type EvidenceMaturityBlockerDetail = {
  blocker: EvidenceMaturityBlockerKind | string;
  blocker_label: string;
  /** null 表示无法归因，不是"不会自愈"。 */
  self_healing: boolean | null;
  reason_counts?: Record<string, number>;
};

export type EvidenceMaturityBlocker = EvidenceMaturityBlockerDetail & {
  code: string;
  label: string;
  detail?: string;
};


export type EvidenceMaturityStatus = {
  schema_version: "evidence_maturity.v1" | string;
  generated_at: string;
  overall_status: "healthy" | "collecting" | "attention" | "degraded" | string;
  mode: string;
  automatic_promotion_allowed: false;
  worker: {
    status: string;
    healthy: boolean;
    reason: string;
    heartbeat_at?: string | null;
    heartbeat_age_seconds?: number | null;
    started_at?: string | null;
    jobs: Array<{ name: string; persistent: boolean; alive: boolean }>;
  };
  universe: {
    status: string;
    snapshot_count: number;
    first_snapshot_date?: string | null;
    latest_snapshot_date?: string | null;
    latest_snapshot_age_days?: number | null;
    latest_sampled_fund_count?: number | null;
    latest_fund_type_count?: number | null;
    effective_anchor_count?: number | null;
    minimum_effective_anchor_count: number;
    anchor_progress_percent?: number | null;
    publishable: boolean;
  };
  factor_ic: {
    status: string;
    available: boolean;
    stale: boolean;
    confidence_eligible: boolean;
    run_date?: string | null;
    age_days?: number | null;
    schema_version?: number | null;
    source?: string | null;
    universe_size?: number | null;
    cohort_mode?: string | null;
    point_in_time_scope?: string | null;
    nav_revision_pit: boolean;
    mature_period_count_20d?: number | null;
    mature_period_count_60d?: number | null;
    economic_minimum_period_count: number;
    economic_progress_percent_20d?: number | null;
    economic_progress_percent_60d?: number | null;
    confidence_block_reasons: string[];
  };
  nav_observation: {
    status: "not_started" | "collecting" | "unavailable" | string;
    observation_count?: number | null;
    fund_count?: number | null;
    capture_run_count?: number | null;
    revision_count?: number | null;
    first_observed_at?: string | null;
    latest_observed_at?: string | null;
    latest_capture_age_days?: number | null;
    latest_nav_date?: string | null;
    latest_capture_fund_count?: number | null;
    availability_basis?: string | null;
    revision_policy?: string | null;
    minimum_feature_history_points?: number | null;
    full_model_ready: boolean;
    automatic_promotion_allowed: false;
  };
  decision_score_shadow: {
    status: string;
    mode?: string | null;
    model_version?: string | null;
    report_count?: number | null;
    artifact_count?: number | null;
    total_artifact_count?: number | null;
    legacy_artifact_count?: number | null;
    valid_artifact_count?: number | null;
    shadow_evaluable_report_count?: number | null;
    top_k_changed_report_count?: number | null;
    candidate_count?: number | null;
    scored_count?: number | null;
    scored_coverage_percent?: number | null;
    missing_component_counts?: Record<string, number> | null;
    /** 硬门先于所有维度生效：它拦住时组件缺口不是真正原因。 */
    hard_gate_blocked_count?: number | null;
    hard_gate_blocked_percent?: number | null;
    hard_gate_blocker?: EvidenceMaturityBlockerDetail | null;
    blocker?: string | null;
    blocker_label?: string | null;
    self_healing?: boolean | null;
    component_blockers?: Record<string, EvidenceMaturityBlockerDetail> | null;
    automatic_promotion_allowed: false;
  };
  milestones: EvidenceMaturityMilestone[];
  /** 当前所有非空缺口，以及等待到底有没有用。 */
  blockers?: EvidenceMaturityBlocker[];
  alerts: EvidenceMaturityAlert[];
  notices: string[];
};


export async function fetchFactorIcStatus(): Promise<FactorIcStatus> {
  const response = await apiFetch(`${API_BASE}/api/diagnostics/factor-ic-status`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}


export async function fetchEvidenceMaturityStatus(): Promise<EvidenceMaturityStatus> {
  const response = await apiFetch(`${API_BASE}/api/diagnostics/evidence-maturity`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}
