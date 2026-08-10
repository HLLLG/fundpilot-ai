"""方向退出侧判定（`sector_direction_exit.2026-08.v1`）。

## 为什么需要它

在此之前整套方向成熟度只有**入场**：`sector_entry_maturity` 判断"今天能不能开始买"，
`sector_direction_state` 用滞回避免入场状态抖动。但没有任何机制回答"我已经买了，什么
时候该走"——`invalidation_signals` 那三条（趋势与资金同时跌入低位／主线转退潮／跌破
20 日均线）至今只是一段展示文案，没有代码在逐日跟踪它们。

后果是确定性减仓链路听不见方向信号。`resolve_escalation_floor` 只在「板块机会不可用
且量价背离 confidence=高」时才升级动作档位，因此一个 `opportunity_available=True`、
`confidence=中`、但趋势已经从 69 掉到 41 的方向，产不出任何减仓动作——用户只能靠自己
判断，而这正是浮盈被拿回去的地方。

## 判定口径

**退出要对着「入场时的条件」判，不是对着一根静态线判。** 这是本模块存在的意义：如果
退出只是"trend < 52"，日报自己就能算，不需要和发现基金联动；联动的价值全在那个基线上。

| 触发 | 语义 | 档位 | 阈值来源 |
|---|---|---|---|
| `entry_state == invalid` | 方向作废 | 清仓评估 −100% | 复用既有 ENTRY_INVALID 判定 |
| 趋势跌破退出线，且连续 N 个交易日 | 持续走坏 | 大幅减仓 −50% | 退出线复用；**N 未经回测** |
| 趋势跌破退出线（首日） | 失去入场资格 | 减仓评估 −25%（浮盈时 −1/3） | 退出线复用；浮盈提档沿用既有先例 |
| 趋势仍在线上，但相对入场分回落 ≥X | 方向转弱 | 暂停追涨（禁止加仓，不要求卖出） | **X 未经回测**，仅相对模式可用 |
| 其余 | 方向有效 | 不升级，允许走既有加仓阶梯 | 入场线复用 |

**刻意不新造退出线。** `EXIT_TREND_THRESHOLD` 是既有常量（入场线 60 − 8），原本只用于
ready→forming 的滞回；这里让它同时承担持仓退出，全代码库因此只有一条退出线，不会漂移。
浮盈提档到 −1/3 也不是新规则，沿用 `resolve_escalation_floor` 里既有的同名先例。

`PERSISTENT_BREAKDOWN_DAYS` 与 `RELATIVE_TREND_DECAY_POINTS` 是本模块唯二的新设参数，
**都没有回测支撑**（`thresholds_validated=False` 会如实写进返回值）。天数计数器刻意只用来
做**升级**、不当准入门槛：抗抖动已经由滞回带（60 进 / 52 出）承担，不需要再叠一层。

## 双模式与降级

线上真实账户里绝大多数持仓来自截图导入，**没有**对应的发现基金买入事件。若退出强依赖
入场契约，这套规则对这些持仓完全不起作用。因此：

* 有契约 → `basis="relative_to_entry"`，能说出"买入时 69 分，现在 41 分"；
* 无契约 → `basis="absolute"`，只说"趋势 41 已跌破退出线 52"。

两种都产出档位，只是解释力不同。趋势分本身取不到时 → `basis="unavailable"`：**不要求
卖出（缺数据不构成卖出理由），但也不授权加仓**。这个不对称是刻意的。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Mapping, Sequence

from app.services.decision_guard_shared import (
    ACTION_BUCKET_CLEAR_ALL,
    ACTION_BUCKET_DEEP_REDUCE,
    ACTION_BUCKET_LABELS,
    ACTION_BUCKET_PAUSE,
    ACTION_BUCKET_REDUCE,
)
from app.services.sector_opportunity_scoring import ENTRY_INVALID, ENTRY_READY_TO_START

logger = logging.getLogger(__name__)

DIRECTION_EXIT_POLICY_VERSION = "sector_direction_exit.2026-08.v1"

EXIT_STATE_HOLD = "hold"
EXIT_STATE_PAUSE_ADD = "pause_add"
EXIT_STATE_REDUCE = "reduce"
EXIT_STATE_DEEP_REDUCE = "deep_reduce"
EXIT_STATE_EXIT = "exit"
EXIT_STATE_UNAVAILABLE = "unavailable"

#: 趋势连续跌破退出线多少个交易日算「持续走坏」，据此把减仓升级为大幅减仓。
#: **新设参数，未经回测。** 只用于升级，不作为准入门槛。
PERSISTENT_BREAKDOWN_DAYS = 3
#: 趋势仍在退出线上方、但相对入场时回落多少分算「方向转弱」，据此禁止继续加仓。
#: **新设参数，未经回测。** 仅在拿到入场契约（相对模式）时生效。
RELATIVE_TREND_DECAY_POINTS = 12.0

_NO_EXIT: dict[str, Any] = {
    "policy_version": DIRECTION_EXIT_POLICY_VERSION,
    "exit_state": EXIT_STATE_HOLD,
    "min_bucket": None,
    "min_action_label": "",
    "suggested_position_change_percent": None,
    "allows_add": True,
    "basis": "absolute",
    "consecutive_days_below_exit_line": 0,
    "reasons": [],
    "triggers": [],
    "entry_reference": None,
    "thresholds_validated": False,
}


def assess_direction_exit(
    *,
    sector_label: str,
    entry_state: str | None,
    trend_strength: float | None,
    exit_trend_threshold: float,
    trend_history: Sequence[tuple[str, float]] = (),
    entry_contract: Mapping[str, Any] | None = None,
    has_unrealized_gain: bool = False,
    persistent_breakdown_days: int = PERSISTENT_BREAKDOWN_DAYS,
    relative_decay_points: float = RELATIVE_TREND_DECAY_POINTS,
) -> dict[str, Any]:
    """纯函数：给一个已持有方向算退出档位。无 IO，阈值可覆盖。

    ``trend_history`` 是**不含今日**的历史趋势分，按交易日**从近到远**排列
    ``[(trade_date, trend), ...]``；缺失时只能看到今日，连续天数最多算到 1。
    """
    label = str(sector_label or "").strip()
    result: dict[str, Any] = {
        **_NO_EXIT,
        "sector_label": label,
        "exit_trend_threshold": exit_trend_threshold,
        "persistent_breakdown_days": persistent_breakdown_days,
    }

    entry_reference = _normalize_entry_contract(entry_contract, sector_label=label)
    result["entry_reference"] = entry_reference
    relative = entry_reference is not None and entry_reference.get("entry_trend") is not None

    trend = _num(trend_strength)
    if trend is None:
        # 缺数据不构成卖出理由，但也不授权加仓。
        result.update(
            exit_state=EXIT_STATE_UNAVAILABLE,
            basis="unavailable",
            allows_add=False,
            reasons=["方向趋势分不可得，无法确认入场理由是否仍然成立"],
            triggers=["补齐20日价格结构与横截面分位后重新判定"],
        )
        return result

    result["basis"] = "relative_to_entry" if relative else "absolute"
    result["trend_strength"] = trend

    below = trend < exit_trend_threshold
    consecutive = _consecutive_days_below(
        trend,
        trend_history,
        threshold=exit_trend_threshold,
    )
    result["consecutive_days_below_exit_line"] = consecutive
    persistent_days = max(2, int(persistent_breakdown_days))
    persistent = consecutive >= persistent_days

    # 一、方向作废：复用既有 invalid 判定（双弱 / 退潮 / 结构破坏），不重算条件。
    #
    # 刻意**不**直接给清仓：既有 `resolve_escalation_floor` 把清仓留给"多重强信号极端
    # 共振"这一档，单一条件就顶到清仓会破坏那套标定。而且方向失效是**板块**信号，基金
    # 本身不等于它的板块，仅凭板块信号砍掉全部仓位是越权。因此作废先给大幅减仓，只有
    # 同时确认"已连续跌破退出线"才升到清仓评估。
    if str(entry_state or "") == ENTRY_INVALID:
        base_reason = _with_entry_context(
            f"方向「{label}」已判定为不具备参与条件"
            "（趋势与资金参与度同时处于横截面低位，或主线退潮、价格结构破坏）",
            entry_reference,
            trend,
        )
        if persistent:
            return _finalize(
                result,
                exit_state=EXIT_STATE_EXIT,
                min_bucket=ACTION_BUCKET_CLEAR_ALL,
                percent=-100.0,
                reasons=[
                    base_reason,
                    f"且趋势强度已连续 {consecutive} 个交易日低于退出线 "
                    f"{exit_trend_threshold:g}，方向失效已被时间确认",
                ],
                triggers=["趋势与资金参与度同时回到横截面中位以上，方向才重新具备参与资格"],
            )
        return _finalize(
            result,
            exit_state=EXIT_STATE_DEEP_REDUCE,
            min_bucket=ACTION_BUCKET_DEEP_REDUCE,
            percent=-50.0,
            reasons=[base_reason],
            triggers=[
                "趋势与资金参与度同时回到横截面中位以上，方向才重新具备参与资格",
                f"若连续 {persistent_days} 个交易日仍在退出线下则升级为清仓评估",
            ],
        )

    # 二、趋势跌破退出线：首日减仓，持续走坏升级为大幅减仓。
    if below:
        if persistent:
            return _finalize(
                result,
                exit_state=EXIT_STATE_DEEP_REDUCE,
                min_bucket=ACTION_BUCKET_DEEP_REDUCE,
                percent=-50.0,
                reasons=[
                    _with_entry_context(
                        f"方向「{label}」趋势强度 {trend:.1f} 已连续 {consecutive} 个交易日"
                        f"低于退出线 {exit_trend_threshold:g}，属持续走坏而非单日插针",
                        entry_reference,
                        trend,
                    )
                ],
                triggers=[
                    f"趋势强度回到退出线 {exit_trend_threshold:g} 以上并保持",
                    "否则按持续走坏继续降档",
                ],
            )
        percent = -(100.0 / 3.0) if has_unrealized_gain else -25.0
        reasons = [
            _with_entry_context(
                f"方向「{label}」趋势强度 {trend:.1f} 已跌破退出线 {exit_trend_threshold:g}，"
                "不再满足当初把它选进来的条件",
                entry_reference,
                trend,
            )
        ]
        if has_unrealized_gain:
            reasons.append("当前持仓浮盈，落袋压力更小，建议提高减仓比例")
        return _finalize(
            result,
            exit_state=EXIT_STATE_REDUCE,
            min_bucket=ACTION_BUCKET_REDUCE,
            percent=percent,
            reasons=reasons,
            triggers=[
                f"趋势强度回到退出线 {exit_trend_threshold:g} 以上",
                f"若连续 {persistent_days} 个交易日仍在线下则升级为大幅减仓",
            ],
        )

    # 三、趋势仍在线上但相对入场明显回落：只禁止加仓，不要求卖出。
    if relative:
        entry_trend = float(entry_reference["entry_trend"])
        decay = entry_trend - trend
        result["trend_decay_from_entry"] = round(decay, 2)
        if decay >= relative_decay_points:
            return _finalize(
                result,
                exit_state=EXIT_STATE_PAUSE_ADD,
                min_bucket=ACTION_BUCKET_PAUSE,
                percent=None,
                reasons=[
                    f"方向「{label}」趋势强度已从买入时的 {entry_trend:.1f} 回落到 {trend:.1f}"
                    f"（-{decay:.1f} 分），方向在转弱但尚未跌破退出线 {exit_trend_threshold:g}，"
                    "本轮不再加仓"
                ],
                triggers=[
                    "趋势强度重新回到买入时水平附近才恢复加仓资格",
                    f"跌破退出线 {exit_trend_threshold:g} 则转为减仓",
                ],
            )

    # 四、趋势仍在线上。加仓额外要求方向**当前仍通过入场线**——原来的加仓阶梯只看
    # `research_score`（"今天这个方向多强"），不问"我买它的理由还在不在"，于是一个趋势
    # 正在从 43 掉到 38 的方向照样能拿到加仓比例。这里把它按住：不要求卖出，但不给加。
    if str(entry_state or "") == ENTRY_READY_TO_START:
        result["allows_add"] = True
        return result

    result.update(
        exit_state=EXIT_STATE_PAUSE_ADD,
        min_bucket=ACTION_BUCKET_PAUSE,
        min_action_label=ACTION_BUCKET_LABELS[ACTION_BUCKET_PAUSE],
        allows_add=False,
        reasons=[
            f"方向「{label}」趋势强度 {trend:.1f} 仍在退出线 {exit_trend_threshold:g} 上方，"
            "但当前未通过入场线，维持持有、本轮不加仓"
        ],
        triggers=[
            "方向重新通过入场线（趋势、资金参与度、价格结构同时达标）才恢复加仓资格",
            f"跌破退出线 {exit_trend_threshold:g} 则转为减仓",
        ],
    )
    return result


def _finalize(
    result: dict[str, Any],
    *,
    exit_state: str,
    min_bucket: int,
    percent: float | None,
    reasons: list[str],
    triggers: list[str],
) -> dict[str, Any]:
    result.update(
        exit_state=exit_state,
        min_bucket=min_bucket,
        min_action_label=ACTION_BUCKET_LABELS[min_bucket],
        suggested_position_change_percent=percent,
        # 任何一档退出信号都同时取消加仓资格：不能一边说方向在走坏、一边允许加。
        allows_add=False,
        reasons=reasons,
        triggers=triggers,
    )
    return result


def _with_entry_context(
    text: str,
    entry_reference: Mapping[str, Any] | None,
    trend: float,
) -> str:
    """有入场契约时把"当初为什么买"写进理由，让减仓建议可追溯到那笔决策。"""
    if not entry_reference:
        return text
    entry_trend = _num(entry_reference.get("entry_trend"))
    entry_date = str(entry_reference.get("entry_date") or "").strip()
    if entry_trend is None or not entry_date:
        return text
    return (
        f"{text}；{entry_date} 买入时该方向趋势强度为 {entry_trend:.1f}"
        f"（现 {trend:.1f}），入场理由已不成立"
    )


def _consecutive_days_below(
    today_trend: float,
    trend_history: Sequence[tuple[str, float]],
    *,
    threshold: float,
) -> int:
    """含今日在内、连续低于退出线的交易日数。

    ``trend_history`` 必须按交易日从近到远排列。历史里出现空缺（系统停机、板块当天没进
    扫描）时不假装那天也达标：遇到第一个不在线下的记录即停止，取不到记录也停止。
    """
    if today_trend >= threshold:
        return 0
    count = 1
    for _date, value in trend_history:
        score = _num(value)
        if score is None or score >= threshold:
            break
        count += 1
    return count


def _normalize_entry_contract(
    entry_contract: Mapping[str, Any] | None,
    *,
    sector_label: str,
) -> dict[str, Any] | None:
    if not isinstance(entry_contract, Mapping):
        return None
    contract_label = str(entry_contract.get("sector_label") or "").strip()
    # 方向换过了（当初买的是另一个板块）就不能拿旧基线判现在这个方向。
    if contract_label and sector_label and contract_label != sector_label:
        return None
    entry_trend = _num(entry_contract.get("entry_trend"))
    normalized = {
        "sector_label": contract_label or sector_label,
        "entry_date": str(entry_contract.get("entry_date") or "").strip() or None,
        "entry_state": str(entry_contract.get("entry_state") or "").strip() or None,
        "entry_trend": entry_trend,
        "entry_participation": _num(entry_contract.get("entry_participation")),
        "entry_position_risk": _num(entry_contract.get("entry_position_risk")),
        "entry_tranche_scale": _num(entry_contract.get("entry_tranche_scale")),
        "thesis_event_id": str(entry_contract.get("thesis_event_id") or "").strip() or None,
    }
    return normalized


# --------------------------------------------------------------------------
# 读取侧：趋势历史与入场契约（都复用既有存储，不新增表）
# --------------------------------------------------------------------------


def load_direction_trend_history(
    sector_labels: Sequence[str],
    *,
    before_trade_date: str | None,
    lookback_days: int = 12,
) -> dict[str, list[tuple[str, float]]]:
    """从 `sector_direction_states` 读各方向**今日之前**的趋势分，从近到远。

    该表由发现基金链路按交易日写入（单写者），这里只读。读不到就返回空 dict，让退出
    判定退化为"只看今天"，不阻塞日报。
    """
    labels = [str(label).strip() for label in sector_labels if str(label or "").strip()]
    if not labels or not str(before_trade_date or "").strip():
        return {}
    unique = list(dict.fromkeys(labels))
    placeholders = ",".join("?" for _ in unique)
    rows_by_label: dict[str, list[tuple[str, float]]] = {label: [] for label in unique}
    try:
        from app.database import _connect

        with _connect() as connection:
            rows = connection.execute(
                "SELECT trade_date, sector_label, trend_strength_score "
                "FROM sector_direction_states "
                f"WHERE sector_label IN ({placeholders}) AND trade_date < ? "
                "ORDER BY trade_date DESC",
                (*unique, str(before_trade_date)),
            ).fetchall()
    except Exception:  # noqa: BLE001 — 历史读不到只是少一层升级判断，不能拖垮日报
        logger.warning("读取方向趋势历史失败，退出判定退化为只看今日", exc_info=True)
        return {}

    for row in rows:
        label = str(row["sector_label"])
        bucket = rows_by_label.get(label)
        if bucket is None or len(bucket) >= lookback_days:
            continue
        score = _num(row["trend_strength_score"])
        if score is None:
            continue
        bucket.append((str(row["trade_date"]), score))
    return {label: values for label, values in rows_by_label.items() if values}


def load_direction_entry_contracts(
    fund_codes: Sequence[str],
    *,
    limit: int = 500,
) -> dict[str, dict[str, Any]]:
    """从发现基金的买入决策事件里取每只基金最近一次的入场契约。

    `decision_events` 已经是不可变证据，且 discovery 的 buy 事件 payload 里带着
    `recommendation.sector_name` / `entry_tranche_scale` 以及冻结快照里那一行完整的 v3
    分数——入场理由本来就在库里，这里只是第一次把它读回来。事件按 `decision_at DESC`
    返回，因此每只基金第一次命中即最近一次，即使被 limit 截断也仍然正确。
    """
    codes = {str(code).strip() for code in fund_codes if str(code or "").strip()}
    codes.discard("000000")
    if not codes:
        return {}
    try:
        from app.request_context import get_request_user_id
        from app.services.decision_repository import list_decision_events

        events = list_decision_events(
            user_id=get_request_user_id(),
            source_type="discovery",
            limit=limit,
        )
    except Exception:  # noqa: BLE001 — 没有契约只是退化成绝对模式
        logger.warning("读取发现基金入场契约失败，退出判定退化为绝对模式", exc_info=True)
        return {}

    contracts: dict[str, dict[str, Any]] = {}
    for event in events:
        code = str((event or {}).get("fund_code") or "").strip()
        if code not in codes or code in contracts:
            continue
        if str(event.get("action_category") or "") != "buy":
            continue
        contract = _entry_contract_from_event(event)
        if contract is not None:
            contracts[code] = contract
    return contracts


def _entry_contract_from_event(event: Mapping[str, Any]) -> dict[str, Any] | None:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return None
    recommendation = payload.get("recommendation")
    recommendation = recommendation if isinstance(recommendation, Mapping) else {}
    sector_label = str(recommendation.get("sector_name") or "").strip()
    if not sector_label:
        return None

    scores = _entry_scores_from_snapshot(payload, sector_label)
    if scores is None:
        scores = _entry_scores_from_evidence_text(recommendation)

    return {
        "sector_label": sector_label,
        "entry_date": str(event.get("decision_date") or "").strip() or None,
        "entry_state": str(recommendation.get("entry_path") or "").strip() or None,
        "entry_trend": (scores or {}).get("trend"),
        "entry_participation": (scores or {}).get("participation"),
        "entry_position_risk": (scores or {}).get("position"),
        "entry_tranche_scale": _num(recommendation.get("entry_tranche_scale")),
        "thesis_event_id": str(event.get("event_id") or "").strip() or None,
    }


def _entry_scores_from_snapshot(
    payload: Mapping[str, Any],
    sector_label: str,
) -> dict[str, float | None] | None:
    """首选：从冻结快照里那一行完整的 v3 分数取值（结构化，最可靠）。"""
    replay = payload.get("replay_bundle")
    facts = (replay or {}).get("facts_snapshot") if isinstance(replay, Mapping) else None
    rows = (facts or {}).get("sector_opportunities") if isinstance(facts, Mapping) else None
    if not isinstance(rows, Sequence):
        return None
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("sector_label") or "").strip() != sector_label:
            continue
        trend = _num(row.get("trend_strength_score"))
        if trend is None:
            continue
        return {
            "trend": trend,
            "participation": _num(row.get("participation_score")),
            "position": _num(row.get("position_risk_score")),
        }
    return None


_EVIDENCE_SCORE_RE = re.compile(
    r"趋势强度\s*([0-9]+(?:\.[0-9]+)?)"
    r"[^0-9]{0,12}资金参与度\s*([0-9]+(?:\.[0-9]+)?)"
    r"[^0-9]{0,12}价格位置\s*([0-9]+(?:\.[0-9]+)?)"
)


def _entry_scores_from_evidence_text(
    recommendation: Mapping[str, Any],
) -> dict[str, float | None] | None:
    """兜底：老事件没有冻结快照时，从 sector_evidence 文案里回读三个分数。

    文案格式由 `discovery` 侧生成（"…entry_state=ready_to_start，趋势强度69.09，
    资金参与度47.70，价格位置92.67"）。这是最后手段——解析不出来就退回绝对模式，
    不猜。
    """
    evidence = recommendation.get("sector_evidence")
    if not isinstance(evidence, Sequence):
        return None
    for line in evidence:
        match = _EVIDENCE_SCORE_RE.search(str(line or ""))
        if match is None:
            continue
        return {
            "trend": float(match.group(1)),
            "participation": float(match.group(2)),
            "position": float(match.group(3)),
        }
    return None


def _num(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None  # 排除 NaN


__all__ = [
    "DIRECTION_EXIT_POLICY_VERSION",
    "EXIT_STATE_DEEP_REDUCE",
    "EXIT_STATE_EXIT",
    "EXIT_STATE_HOLD",
    "EXIT_STATE_PAUSE_ADD",
    "EXIT_STATE_REDUCE",
    "EXIT_STATE_UNAVAILABLE",
    "PERSISTENT_BREAKDOWN_DAYS",
    "RELATIVE_TREND_DECAY_POINTS",
    "assess_direction_exit",
    "load_direction_entry_contracts",
    "load_direction_trend_history",
]
