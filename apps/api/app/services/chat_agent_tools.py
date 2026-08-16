"""Read-only / job-trigger tools for follow-up chat. Not used by main report generation.

The model may query holdings, explain an already-guarded decision, look up the
local fund catalogue, read persisted direction-state rows, fetch news, or enqueue
the existing analyze / discovery jobs. It cannot change position percentages,
quality gates, PIT evidence, or holdings truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Literal

from app.services.deepseek_client import FETCH_MARKET_NEWS_TOOL, _execute_fetch_market_news

ChatAgentSurface = Literal["report", "discovery"]

CHAT_AGENT_TOOL_MAX_ROUNDS = 3

GET_HOLDINGS_TOOL = {
    "type": "function",
    "function": {
        "name": "get_holdings",
        "description": (
            "读取用户当前账户汇总持仓（只读快照，不刷新外网）。"
            "用于对照「现在手上有什么」，不是报告生成时冻结的那一份。"
        ),
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}

EXPLAIN_HOLDING_DECISION_TOOL = {
    "type": "function",
    "function": {
        "name": "explain_holding_decision",
        "description": (
            "从【当前这份日报】取出某只持仓的建议动作、仓位比例和守卫/退出依据。"
            "只返回报告里已有的字段，不重算、不改档位。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fund_code": {
                    "type": "string",
                    "description": "6 位基金代码",
                }
            },
            "required": ["fund_code"],
        },
    },
}

EXPLAIN_CANDIDATE_DECISION_TOOL = {
    "type": "function",
    "function": {
        "name": "explain_candidate_decision",
        "description": (
            "从【当前这份荐基报告】取出某只候选的动作、金额上限、质量门和理由链。"
            "只返回报告里已有的字段；候选池外的代码会明确说不在本报告。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fund_code": {
                    "type": "string",
                    "description": "6 位基金代码",
                }
            },
            "required": ["fund_code"],
        },
    },
}

LOOKUP_FUND_TOOL = {
    "type": "function",
    "function": {
        "name": "lookup_fund",
        "description": (
            "在本地全量基金目录里按代码或名称查身份（代码、名称）。"
            "只说明这是哪只基金，不构成推荐，也不能绕过质量门。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "6 位代码或基金名称关键词",
                }
            },
            "required": ["query"],
        },
    },
}

GET_SECTOR_CONTEXT_TOOL = {
    "type": "function",
    "function": {
        "name": "get_sector_context",
        "description": (
            "读取方向状态账本里某板块最近一次【已捕获】记录（趋势分、入场状态、连续天数）。"
            "不联网重算；没有记录时返回 unavailable，不得编造分数。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sector_label": {
                    "type": "string",
                    "description": "板块名称，如 半导体、煤炭、电网设备",
                }
            },
            "required": ["sector_label"],
        },
    },
}

RUN_DAILY_REPORT_TOOL = {
    "type": "function",
    "function": {
        "name": "run_daily_report",
        "description": (
            "触发既有「生成日报」异步任务。仓位百分比、Guard、质量门仍由原流水线决定。"
            "仅在用户明确要求重新生成/再跑一份日报时调用，且必须 confirm=true。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "confirm": {
                    "type": "boolean",
                    "description": "用户已明确要求生成时为 true",
                },
                "allow_stale": {
                    "type": "boolean",
                    "description": "持仓快照过期时是否接受降级分析，默认 false",
                },
            },
            "required": ["confirm"],
        },
    },
}

RUN_DISCOVERY_SCAN_TOOL = {
    "type": "function",
    "function": {
        "name": "run_discovery_scan",
        "description": (
            "触发既有「推荐基金」异步扫描。质量门与金额硬上限仍由原流水线决定。"
            "仅在用户明确要求重新扫描时调用，且必须 confirm=true。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "confirm": {
                    "type": "boolean",
                    "description": "用户已明确要求扫描时为 true",
                },
                "allow_stale": {
                    "type": "boolean",
                    "description": "持仓快照过期时是否接受降级扫描，默认 false",
                },
                "scan_mode": {
                    "type": "string",
                    "enum": ["full_market", "portfolio_gap"],
                    "description": "省略则沿用当前荐基报告或默认市场优选",
                },
                "budget_yuan": {
                    "type": "number",
                    "description": "本次可投入预算；省略则沿用当前报告",
                },
            },
            "required": ["confirm"],
        },
    },
}

TOOL_STATUS_LABELS = {
    "get_holdings": "正在读取当前持仓…",
    "explain_holding_decision": "正在核对日报里的决策依据…",
    "explain_candidate_decision": "正在核对荐基报告里的决策依据…",
    "lookup_fund": "正在查询基金目录…",
    "get_sector_context": "正在读取方向状态账本…",
    "fetch_market_news": "正在拉取市场新闻…",
    "run_daily_report": "正在触发日报生成…",
    "run_discovery_scan": "正在触发荐基扫描…",
}


@dataclass
class ChatAgentContext:
    surface: ChatAgentSurface
    report: dict[str, Any]
    execution_blocked: bool = False
    news_enabled: bool = False
    pending_jobs: list[dict[str, str]] = field(default_factory=list)


def tool_status_label(name: str) -> str:
    return TOOL_STATUS_LABELS.get(name, f"正在调用 {name}…")


def tool_specs_for(
    *,
    surface: ChatAgentSurface,
    news_enabled: bool,
) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = [
        GET_HOLDINGS_TOOL,
        LOOKUP_FUND_TOOL,
        GET_SECTOR_CONTEXT_TOOL,
    ]
    if surface == "report":
        specs.append(EXPLAIN_HOLDING_DECISION_TOOL)
    else:
        specs.append(EXPLAIN_CANDIDATE_DECISION_TOOL)
    if news_enabled:
        specs.append(FETCH_MARKET_NEWS_TOOL)
    specs.extend([RUN_DAILY_REPORT_TOOL, RUN_DISCOVERY_SCAN_TOOL])
    return specs


def execute_chat_tool(tool_call: dict[str, Any], context: ChatAgentContext) -> str:
    name, args = _tool_name_and_args(tool_call)
    if name == "get_holdings":
        return _tool_get_holdings()
    if name == "explain_holding_decision":
        return _tool_explain_holding_decision(args, context.report)
    if name == "explain_candidate_decision":
        return _tool_explain_candidate_decision(args, context.report)
    if name == "lookup_fund":
        return _tool_lookup_fund(args)
    if name == "get_sector_context":
        return _tool_get_sector_context(args)
    if name == "fetch_market_news":
        if not context.news_enabled:
            return _error("新闻工具未启用")
        from app.services.news_service import NewsService

        return _execute_fetch_market_news(tool_call, NewsService(), [])
    if name == "run_daily_report":
        return _tool_run_daily_report(args, context)
    if name == "run_discovery_scan":
        return _tool_run_discovery_scan(args, context)
    return _error(f"unknown tool: {name}")


def _tool_name_and_args(tool_call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    function = tool_call.get("function") or {}
    name = str(function.get("name") or "").strip()
    raw = function.get("arguments") or "{}"
    try:
        args = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        args = {}
    if not isinstance(args, dict):
        args = {}
    return name, args


def _error(message: str, **extra: Any) -> str:
    payload = {"ok": False, "error": message}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


def _ok(payload: dict[str, Any]) -> str:
    body = {"ok": True, **payload}
    return json.dumps(body, ensure_ascii=False)


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "ok", "confirm"}


def _fund_code(value: object) -> str:
    text = str(value or "").strip()
    digits = "".join(character for character in text if character.isdigit())
    return digits if len(digits) == 6 else text


def _clip_list(value: object, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    items = [str(item).strip() for item in value if str(item).strip()]
    return items[:limit]


def _tool_get_holdings() -> str:
    from app.services.portfolio_holdings_service import load_persisted_holdings

    holdings, source, snapshot_date, refreshed_at = load_persisted_holdings(
        fetch_benchmark=False
    )
    items = [
        {
            "fund_code": holding.fund_code,
            "fund_name": holding.fund_name,
            "holding_amount": holding.holding_amount,
            "holding_return_percent": holding.holding_return_percent,
            "sector_name": holding.sector_name,
            "sector_return_percent": holding.sector_return_percent,
            "daily_return_percent": holding.daily_return_percent,
        }
        for holding in holdings
    ]
    return _ok(
        {
            "source": source,
            "snapshot_date": snapshot_date,
            "refreshed_at": refreshed_at.isoformat() if refreshed_at else None,
            "count": len(items),
            "holdings": items,
            "note": "只读当前账本，不是报告冻结持仓。金额为估算展示值，不是可执行委托。",
        }
    )


def _tool_explain_holding_decision(args: dict[str, Any], report: dict[str, Any]) -> str:
    code = _fund_code(args.get("fund_code"))
    if len(code) != 6:
        return _error("fund_code 必须是 6 位数字")
    rec = _first_by_code(report.get("fund_recommendations"), code)
    row = _first_by_code((report.get("analysis_facts") or {}).get("holdings"), code)
    if rec is None and row is None:
        return _error("这份日报没有该基金的建议或持仓行", fund_code=code)
    payload: dict[str, Any] = {
        "fund_code": code,
        "from_report": True,
        "note": "以下字段来自已生成日报，未经本工具重算。",
    }
    if rec is not None:
        payload["recommendation"] = {
            "fund_name": rec.get("fund_name"),
            "action": rec.get("action"),
            "suggested_position_change_percent": rec.get(
                "suggested_position_change_percent"
            ),
            "suggested_position_change_basis": rec.get(
                "suggested_position_change_basis"
            ),
            "estimated_position_change_amount_yuan": rec.get(
                "estimated_position_change_amount_yuan"
            ),
            "amount_note": rec.get("amount_note"),
            "confidence": rec.get("confidence"),
            "decision_path": rec.get("decision_path"),
            "sector_evidence": _clip_list(rec.get("sector_evidence"), 4),
            "fund_evidence": _clip_list(rec.get("fund_evidence"), 4),
            "validation_notes": _clip_list(rec.get("validation_notes"), 4),
            "points": _clip_list(rec.get("points"), 4),
            "risks": _clip_list(rec.get("risks"), 3),
        }
    if row is not None:
        payload["holding_facts"] = _compact_holding_facts(row)
    return _ok(payload)


def _tool_explain_candidate_decision(args: dict[str, Any], report: dict[str, Any]) -> str:
    code = _fund_code(args.get("fund_code"))
    if len(code) != 6:
        return _error("fund_code 必须是 6 位数字")
    rec = _first_by_code(report.get("recommendations"), code)
    pool = _first_by_code(report.get("candidate_pool"), code)
    if rec is None and pool is None:
        return _error("这份荐基报告的候选池/推荐里没有该基金", fund_code=code)
    payload: dict[str, Any] = {
        "fund_code": code,
        "from_report": True,
        "note": "以下字段来自已生成荐基报告。候选池外基金不得说成可买。",
    }
    if rec is not None:
        payload["recommendation"] = {
            "fund_name": rec.get("fund_name"),
            "sector_name": rec.get("sector_name"),
            "action": rec.get("action"),
            "suggested_amount_yuan": rec.get("suggested_amount_yuan"),
            "amount_note": rec.get("amount_note"),
            "confidence": rec.get("confidence"),
            "decision_path": rec.get("decision_path"),
            "waiting_reason_code": rec.get("waiting_reason_code"),
            "sector_evidence": _clip_list(rec.get("sector_evidence"), 4),
            "fund_evidence": _clip_list(rec.get("fund_evidence"), 4),
            "validation_notes": _clip_list(rec.get("validation_notes"), 4),
            "points": _clip_list(rec.get("points"), 4),
            "risks": _clip_list(rec.get("risks"), 3),
        }
    if pool is not None:
        gate = pool.get("quality_gate") if isinstance(pool.get("quality_gate"), dict) else {}
        payload["candidate"] = {
            "fund_name": pool.get("fund_name"),
            "sector_name": pool.get("sector_name") or pool.get("sector_label"),
            "quality_gate_status": gate.get("status"),
            "quality_gate_reasons": _clip_list(gate.get("reasons"), 4),
            "opportunity_score_20_60d": pool.get("opportunity_score_20_60d"),
        }
    return _ok(payload)


def _compact_holding_facts(row: dict[str, Any]) -> dict[str, Any]:
    escalation = row.get("escalation") if isinstance(row.get("escalation"), dict) else {}
    direction_exit = (
        row.get("direction_exit") if isinstance(row.get("direction_exit"), dict) else {}
    )
    evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    composite = evidence.get("composite") if isinstance(evidence.get("composite"), dict) else {}
    return {
        "fund_name": row.get("fund_name"),
        "sector_name": row.get("sector_name"),
        "escalation": {
            "min_bucket": escalation.get("min_bucket"),
            "reasons": _clip_list(escalation.get("reasons"), 4),
        }
        if escalation
        else None,
        "direction_exit": {
            "state": direction_exit.get("state") or direction_exit.get("status"),
            "consecutive_days_below_exit_line": direction_exit.get(
                "consecutive_days_below_exit_line"
            ),
            "add_eligible": direction_exit.get("add_eligible"),
            "thresholds_validated": direction_exit.get("thresholds_validated"),
        }
        if direction_exit
        else None,
        "evidence_level": composite.get("level"),
    }


def _first_by_code(rows: object, code: str) -> dict[str, Any] | None:
    if not isinstance(rows, list):
        return None
    for item in rows:
        if isinstance(item, dict) and str(item.get("fund_code") or "").strip() == code:
            return item
    return None


def _tool_lookup_fund(args: dict[str, Any]) -> str:
    from app.services.fund_code_resolver import (
        lookup_fund_name_by_code,
        search_funds_by_keyword,
    )

    query = str(args.get("query") or "").strip()
    if not query:
        return _error("query 不能为空")
    code = _fund_code(query)
    if len(code) == 6 and code.isdigit():
        name = lookup_fund_name_by_code(code)
        items = [{"fund_code": code, "fund_name": name or "未知"}]
    else:
        found = search_funds_by_keyword(query, limit=5)
        items = [
            {
                "fund_code": str(item.get("fund_code") or ""),
                "fund_name": str(item.get("fund_name") or ""),
            }
            for item in found
            if isinstance(item, dict) and item.get("fund_code")
        ]
    return _ok(
        {
            "query": query,
            "items": items,
            "note": "仅目录身份，不构成推荐，也不能把查询结果说成可买。",
        }
    )


def _tool_get_sector_context(args: dict[str, Any]) -> str:
    from app.database import _connect

    label = str(args.get("sector_label") or "").strip()
    if not label:
        return _error("sector_label 不能为空")
    try:
        with _connect() as connection:
            rows = connection.execute(
                """
                SELECT sector_label, trade_date, entry_state, raw_entry_state,
                       qualifies_for_ready, consecutive_qualifying_days,
                       trend_strength_score, participation_score,
                       position_risk_score, direction_score, source
                FROM sector_direction_states
                WHERE (source IS NULL OR source = 'captured')
                  AND (sector_label = ? OR sector_label LIKE ?)
                ORDER BY
                    CASE WHEN sector_label = ? THEN 0 ELSE 1 END,
                    trade_date DESC
                LIMIT 3
                """,
                (label, f"%{label}%", label),
            ).fetchall()
    except Exception as exc:  # noqa: BLE001 — 账本不可用时明确失败，不编造
        return _error(f"方向状态账本不可用：{exc}")
    if not rows:
        return _ok(
            {
                "status": "unavailable",
                "sector_label": label,
                "items": [],
                "note": "账本没有该方向的已捕获记录，不能据此给出加减仓。",
            }
        )
    items = []
    for row in rows:
        items.append(
            {
                "sector_label": row["sector_label"],
                "trade_date": row["trade_date"],
                "entry_state": row["entry_state"],
                "raw_entry_state": row["raw_entry_state"],
                "qualifies_for_ready": bool(row["qualifies_for_ready"]),
                "consecutive_qualifying_days": row["consecutive_qualifying_days"],
                "trend_strength_score": row["trend_strength_score"],
                "participation_score": row["participation_score"],
                "position_risk_score": row["position_risk_score"],
                "direction_score": row["direction_score"],
                "source": row["source"] or "captured",
            }
        )
    return _ok(
        {
            "status": "ok",
            "sector_label": label,
            "items": items,
            "note": "来自已落库的方向状态，不是本轮实时重算。",
        }
    )


def _tool_run_daily_report(args: dict[str, Any], context: ChatAgentContext) -> str:
    if not _as_bool(args.get("confirm")):
        return _error("需要 confirm=true。仅在用户明确要求重新生成日报时调用。")
    from app.database import get_analysis_role_prompt, get_investor_profile
    from app.models import AnalysisRequest, InvestorProfile
    from app.services.decision_data_evidence import (
        StalePortfolioSnapshotError,
        resolve_portfolio_preflight,
    )
    from app.services.job_limits import JobQueueFull
    from app.services.job_store import create_analysis_job
    from app.services.portfolio_holdings_service import load_persisted_holdings

    allow_stale = _as_bool(args.get("allow_stale"))
    persisted, _source, _date, _refreshed = load_persisted_holdings(fetch_benchmark=False)
    try:
        preflight = resolve_portfolio_preflight(persisted, allow_stale=allow_stale)
    except StalePortfolioSnapshotError as exc:
        return _error(str(exc), reason="stale_portfolio_snapshot")
    if not preflight.holdings:
        return _error("当前没有可用持仓，无法生成日报")
    request = AnalysisRequest(
        holdings=list(preflight.holdings),
        profile=get_investor_profile() or InvestorProfile(),
        analysis_mode="deep",
        system_role_prompt=get_analysis_role_prompt(),
        allow_stale_portfolio_snapshot=allow_stale,
    )
    try:
        job_id = create_analysis_job(request)
    except JobQueueFull:
        return _error("日报队列已满，请稍后重试", reason="job_queue_full")
    context.pending_jobs.append({"job_kind": "analysis", "job_id": job_id})
    return _ok(
        {
            "job_kind": "analysis",
            "job_id": job_id,
            "status": "pending",
            "note": "已排队。仓位百分比与 Guard 仍由原流水线决定，本工具不能改规则。",
        }
    )


def _tool_run_discovery_scan(args: dict[str, Any], context: ChatAgentContext) -> str:
    if not _as_bool(args.get("confirm")):
        return _error("需要 confirm=true。仅在用户明确要求重新扫描荐基时调用。")
    from app.database import get_discovery_role_prompt, get_investor_profile
    from app.models import DiscoveryRequest, InvestorProfile
    from app.services.decision_data_evidence import (
        StalePortfolioSnapshotError,
        resolve_portfolio_preflight,
    )
    from app.services.discovery_job_store import create_discovery_job
    from app.services.job_limits import JobQueueFull
    from app.services.portfolio_holdings_service import load_persisted_holdings

    allow_stale = _as_bool(args.get("allow_stale"))
    persisted, _source, _date, _refreshed = load_persisted_holdings(fetch_benchmark=False)
    try:
        preflight = resolve_portfolio_preflight(persisted, allow_stale=allow_stale)
    except StalePortfolioSnapshotError as exc:
        return _error(str(exc), reason="stale_portfolio_snapshot")

    report = context.report
    facts = report.get("discovery_facts") if isinstance(report.get("discovery_facts"), dict) else {}
    scan_mode = str(args.get("scan_mode") or facts.get("scan_mode") or "full_market")
    if scan_mode not in {"full_market", "portfolio_gap"}:
        scan_mode = "full_market"
    strategy = str(facts.get("discovery_strategy") or "opportunity_first")
    if strategy not in {"opportunity_first", "risk_first"}:
        strategy = "opportunity_first"
    budget = args.get("budget_yuan")
    if budget is None:
        budget = facts.get("available_budget_yuan")
    try:
        budget_yuan = float(budget) if budget is not None else None
    except (TypeError, ValueError):
        budget_yuan = None
    focus = args.get("focus_sectors")
    if not isinstance(focus, list) or not focus:
        focus = report.get("focus_sectors") or []
    focus_sectors = [str(item).strip() for item in focus if str(item).strip()][:3]

    request = DiscoveryRequest(
        profile=get_investor_profile() or InvestorProfile(),
        holdings=list(preflight.holdings),
        analysis_mode="deep",
        focus_sectors=focus_sectors,
        budget_yuan=budget_yuan,
        scan_mode=scan_mode,  # type: ignore[arg-type]
        discovery_strategy=strategy,  # type: ignore[arg-type]
        system_role_prompt=get_discovery_role_prompt(),
        allow_stale_portfolio_snapshot=allow_stale,
    )
    try:
        job_id = create_discovery_job(request)
    except JobQueueFull:
        return _error("荐基队列已满，请稍后重试", reason="job_queue_full")
    context.pending_jobs.append({"job_kind": "discovery", "job_id": job_id})
    return _ok(
        {
            "job_kind": "discovery",
            "job_id": job_id,
            "status": "pending",
            "scan_mode": scan_mode,
            "note": "已排队。质量门与金额硬上限仍由原流水线决定，本工具不能降门槛。",
        }
    )
