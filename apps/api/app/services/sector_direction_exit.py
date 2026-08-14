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

## 连续天数只数「有证据」的日子

趋势分单看数值分不出「真实低分」与「无证据占位」：证据不足时 v3 把趋势分兜底成
`35 + 5日涨跌×1.5` 并 clamp 到 **≤45**，而退出线是 52 —— 每个占位值都长得像已跌破。
落库里实测到过这种行（08-07 的国防军工/电网设备恰为 45.0、黄金恰为 35.0，而同批真实
实算值是 36.15 / 48.08 / 90.52）。因此 `load_direction_trend_history` 用
`trend_evidence_coverage`（打分行的 `component_coverage.trend`，兜底分支里与占位值同时
置 0）过滤，并且遇到无证据的日子**停止回溯**而不是跳过——跳过会把空缺日两侧接成连续
序列，等于用没有证据的日子把 −25% 推到 −50%。

## 双模式与降级

线上真实账户里绝大多数持仓来自截图导入，**没有**对应的发现基金买入事件。若退出强依赖
入场契约，这套规则对这些持仓完全不起作用。因此：

* 有契约 → `basis="relative_to_entry"`，能说出"买入时 69 分，现在 41 分"；
* 无契约 → `basis="absolute"`，只说"趋势 41 已跌破退出线 52"。

两种都产出档位，只是解释力不同。趋势分本身取不到时 → `basis="unavailable"`：**不要求
卖出（缺数据不构成卖出理由），但也不授权加仓**。这个不对称是刻意的。
"""

from __future__ import annotations

from datetime import date, timedelta
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
#: 实际买入日晚于推荐日多少个自然日以内，仍认为"这笔买入执行的就是那次推荐"、推荐日的
#: 方向分可以直接当入场基线。超过它就要把基线**重定**到实际买入日（见
#: `reconcile_entry_contract_with_holding`）。这是数据代表性判断参数（不进任何收益规则、
#: 不决定档位），5 天覆盖「推荐当周内买入」：申购确认本身要 T+1~T+2，再留用户两三天犹豫。
ENTRY_REBASE_TOLERANCE_DAYS = 5
#: 基线重定时，允许从实际买入日往前最多找多少个自然日内的有证据分数。账本缺买入日当天
#: 的行很常见（周末买入、捕获断更一天），但太早的分数同样不能代表买入时的状态。
_REBASE_LOOKBACK_DAYS = 5

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
    #: 拿到了买入记录、但因为板块分类漂移而无法用作相对基线时的披露文案。
    "entry_reference_note": None,
    #: 买入时承诺的失效条件 × 今天的判定，逐条对照。
    "invalidation_status": [],
    #: 其中"当初承诺过、今天确实触发"的 code；非空即可直接引用买入承诺作为减仓理由。
    "breached_entry_promises": [],
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
    invalidation_checks: object = None,
    persistent_breakdown_days: int = PERSISTENT_BREAKDOWN_DAYS,
    relative_decay_points: float = RELATIVE_TREND_DECAY_POINTS,
) -> dict[str, Any]:
    """纯函数：给一个已持有方向算退出档位。无 IO，阈值可覆盖。

    ``trend_history`` 是**不含今日**的历史趋势分，按交易日**从近到远**排列
    ``[(trade_date, trend), ...]``；缺失时只能看到今日，连续天数最多算到 1。

    ``invalidation_checks`` 是当前方向行的 `invalidation_checks`（见
    `sector_opportunity_scoring._invalidation_checks_v3`）。它与入场契约里冻结的
    ``promised_invalidation`` 对照后写入 ``invalidation_status``：这是"买入卡片上写的失效
    条件"第一次真的被逐日核对，而不是停在展示文案。
    """
    label = str(sector_label or "").strip()
    result: dict[str, Any] = {
        **_NO_EXIT,
        "sector_label": label,
        "exit_trend_threshold": exit_trend_threshold,
        "persistent_breakdown_days": persistent_breakdown_days,
    }

    entry_reference, entry_reference_note = _resolve_entry_reference(
        entry_contract,
        sector_label=label,
    )
    result["entry_reference"] = entry_reference
    # 拿到了买入记录但用不上时必须说清原因（板块分类漂移），否则用户以为系统压根没这笔账。
    result["entry_reference_note"] = entry_reference_note
    relative = entry_reference is not None and entry_reference.get("entry_trend") is not None

    # 逐日核对买入时承诺的失效条件。分类漂移时 `entry_reference` 为 None，但承诺仍然属于
    # 那笔买入，照样要核对——所以这里从原始契约取，不依赖 `entry_reference`。
    invalidation_status, breached = _resolve_invalidation_status(
        promised=(entry_contract or {}).get("promised_invalidation")
        if isinstance(entry_contract, Mapping)
        else None,
        today_checks=invalidation_checks,
    )
    result["invalidation_status"] = invalidation_status
    result["breached_entry_promises"] = [row["code"] for row in breached]
    breach_reason = (
        "买入时写明的失效条件已触发："
        + "；".join(row["label"] or row["code"] for row in breached[:2])
        if breached
        else None
    )

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
                    *([breach_reason] if breach_reason else []),
                ],
                triggers=["趋势与资金参与度同时回到横截面中位以上，方向才重新具备参与资格"],
            )
        return _finalize(
            result,
            exit_state=EXIT_STATE_DEEP_REDUCE,
            min_bucket=ACTION_BUCKET_DEEP_REDUCE,
            percent=-50.0,
            reasons=[base_reason, *([breach_reason] if breach_reason else [])],
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
                    ),
                    *([breach_reason] if breach_reason else []),
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
        if breach_reason:
            reasons.append(breach_reason)
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

    # 三、买入时承诺的失效条件已触发，但趋势还没跌破退出线。
    #
    # 上面两档覆盖的是"趋势跌破退出线"和"方向作废"，而承诺里还有主线退潮、早期试仓的信号分
    # 跌破试仓线这类条件——它们可以在趋势分仍然体面时先触发。既然买入卡片上写着"出现这些
    # 情况就该退"，最低限度是**不再加仓**：这是用户当初同意的条件，不是新造的规则。
    #
    # 刻意只到 pause_add，不直接减仓：这些 code 复用的阈值原本是**入场**门槛，用作退出触发
    # 没有回测支撑（`thresholds_validated=False` 已如实披露），把它顶到减仓等于用未标定的
    # 阈值处置真实仓位。
    if breach_reason:
        return _finalize(
            result,
            exit_state=EXIT_STATE_PAUSE_ADD,
            min_bucket=ACTION_BUCKET_PAUSE,
            percent=None,
            reasons=[
                _with_entry_context(
                    f"{breach_reason}；方向「{label}」趋势强度 {trend:.1f} 尚在退出线 "
                    f"{exit_trend_threshold:g} 上方，因此维持持有、本轮不再加仓",
                    entry_reference,
                    trend,
                )
            ],
            triggers=[
                "已触发的失效条件重新解除后才恢复加仓资格",
                f"若趋势强度继续跌破退出线 {exit_trend_threshold:g} 则转为减仓",
            ],
        )

    # 四、趋势仍在线上但相对入场明显回落：只禁止加仓，不要求卖出。
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

    # 五、趋势仍在线上。加仓额外要求方向**当前仍通过入场线**——原来的加仓阶梯只看
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


def _resolve_entry_reference(
    entry_contract: Mapping[str, Any] | None,
    *,
    sector_label: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """返回 `(可用于相对比较的入场基线, 不可用时的披露文案)`。

    板块分类会随时间变化：015788 在 2026-08-06 被买入时荐基记录的方向是「信创」，现在这只
    基金归到「数字经济」。拿信创的入场趋势分去比数字经济的当前趋势分是两个成分篮子的分数
    对比，会给出一个**错误的**基线——比没有基线更危险，所以这里仍然拒绝相对模式。

    但此前是**静默**拒绝：`entry_reference` 直接为 null，用户只看到"趋势 73.3 仍在退出线
    上方"，完全不知道系统其实握着那笔买入记录、只是因为分类漂移而没用上。现在把原因作为
    文案返回，由调用方披露。
    """
    if not isinstance(entry_contract, Mapping):
        return None, None
    # 契约在核对阶段（`reconcile_entry_contract_with_holding`）被判定不能代表这笔持仓时，
    # 与分类漂移同一处理：拒绝相对模式，但把原因披露出来而不是静默为 null。
    disqualified = str(entry_contract.get("disqualified_reason") or "").strip()
    if disqualified:
        return None, disqualified
    contract_label = str(entry_contract.get("sector_label") or "").strip()
    if contract_label and sector_label and contract_label != sector_label:
        entry_date = str(entry_contract.get("entry_date") or "").strip()
        when = f"{entry_date} " if entry_date else ""
        return None, (
            f"{when}买入时记录的方向是「{contract_label}」，该基金现已归入「{sector_label}」；"
            "两者成分不同，不能用当初的方向分做相对比较，本次按绝对退出线判定"
        )
    return _normalize_entry_contract(entry_contract, sector_label=sector_label), None


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
    # 基线被重定到实际买入日时保留原推荐日，让文案能说清"基线取自买入日，推荐发生在 X"。
    rebased_from = str(entry_contract.get("entry_rebased_from") or "").strip()
    if rebased_from:
        normalized["entry_rebased_from"] = rebased_from
    return normalized


def _resolve_invalidation_status(
    *,
    promised: object,
    today_checks: object,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """把"买入时承诺的失效条件"与"今天的判定"对起来。

    返回 `(status, breached)`：`status` 是逐条对照（含未承诺但今天已触发的条件，标
    `promised=False`），`breached` 只含**当初承诺过、且今天确实触发**的那些——那是可以直接
    引用买入承诺的减仓理由。

    `triggered=None` 表示今天缺数据无法判定，既不算触发也不算解除。
    """
    promised_labels: dict[str, str] = {}
    if isinstance(promised, Sequence) and not isinstance(promised, (str, bytes)):
        for item in promised:
            if not isinstance(item, Mapping):
                continue
            code = str(item.get("code") or "").strip()
            if code:
                promised_labels[code] = str(item.get("label") or "").strip()

    status: list[dict[str, Any]] = []
    breached: list[dict[str, Any]] = []
    seen: set[str] = set()
    if isinstance(today_checks, Sequence) and not isinstance(today_checks, (str, bytes)):
        for check in today_checks:
            if not isinstance(check, Mapping):
                continue
            code = str(check.get("code") or "").strip()
            if not code or code in seen:
                continue
            seen.add(code)
            triggered = check.get("triggered")
            triggered = None if triggered is None else bool(triggered)
            row = {
                "code": code,
                "label": promised_labels.get(code) or str(check.get("label") or "").strip(),
                "promised": code in promised_labels,
                "triggered": triggered,
                "detail": str(check.get("detail") or "").strip() or None,
            }
            status.append(row)
            if row["promised"] and triggered:
                breached.append(row)

    # 承诺过、但今天这一行压根没有对应判定（策略变了 / 档位不同）：如实标为无法判定。
    for code, label in promised_labels.items():
        if code in seen:
            continue
        status.append(
            {
                "code": code,
                "label": label,
                "promised": True,
                "triggered": None,
                "detail": "当前方向档位不再产出该条判定，无法逐日核对",
            }
        )
    return status, breached


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

    **只取有趋势证据的那些日子。** 证据不足时 v3 会把趋势分兜底成 `35 + 5日涨跌×1.5`
    并 clamp 到 ≤45，而退出线是 52 —— 每一个占位值都长得像「已跌破退出线」。
    `trend_evidence_coverage`（即打分行的 `component_coverage.trend`）为 0 或 NULL 就是
    这种"当天没有趋势证据"的行，必须当成历史空缺跳过，否则连续跌破天数会被没有证据的
    日子灌水，把 −25% 一路推到 −50%。存量行该列为 NULL，因此按无证据处理。
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
                "SELECT trade_date, sector_label, trend_strength_score, "
                "trend_evidence_coverage "
                "FROM sector_direction_states "
                f"WHERE sector_label IN ({placeholders}) AND trade_date < ? "
                "ORDER BY trade_date DESC",
                (*unique, str(before_trade_date)),
            ).fetchall()
    except Exception:  # noqa: BLE001 — 历史读不到只是少一层升级判断，不能拖垮日报
        logger.warning("读取方向趋势历史失败，退出判定退化为只看今日", exc_info=True)
        return {}

    # 遇到"这天没有可用趋势证据"就**停止**该方向的回溯，而不是跳过它。跳过会把空缺日
    # 两侧的日子接成连续序列（例：08-08 在线下、08-07 无证据、08-06 在线下，跳过后读成
    # 连续 3 天），正是 `_consecutive_days_below` 的契约明确要避免的。行按交易日全局倒序
    # 返回、多方向交错，因此用一个 per-label 的停止集合，不能直接 break。
    stopped: set[str] = set()
    for row in rows:
        label = str(row["sector_label"])
        bucket = rows_by_label.get(label)
        if bucket is None or label in stopped or len(bucket) >= lookback_days:
            continue
        score = _num(row["trend_strength_score"])
        coverage = _num(_row_value(row, "trend_evidence_coverage"))
        if score is None or coverage is None or coverage <= 0:
            stopped.add(label)
            continue
        bucket.append((str(row["trade_date"]), score))
    return {label: values for label, values in rows_by_label.items() if values}


def _row_value(row: Any, key: str) -> Any:
    """按列名取值，列不存在时返回 None（迁移尚未跑到的库不能因此抛错）。"""
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return None


def load_direction_ledger_health(
    as_of_trade_date: str | None,
) -> dict[str, Any]:
    """方向状态账本的捕获健康度：`{last_captured_trade_date, expected_trade_date, stale, note}`。

    退出侧的「连续跌破天数」完全依赖每交易日 19:10 的定时捕获往 `sector_direction_states`
    写行；捕获一旦断更，天数会停在 1、−50% 那一档实际不可达——而这**不会**产生任何报错，
    只是该升的档位安静地不升。健康度必须随退出判定一起披露，否则"连续天数是下界"只是一句
    没人能核实的免责声明。

    判定基准是**上一交易日**而不是当天：日报白天生成时，当天的捕获（19:10）还没跑，账本
    最新覆盖到上一交易日就是健康的。只统计 `source='captured'`（含存量 NULL，语义与
    `load_previous_direction_states` 一致）：回填行是补数手段，不代表捕获链路活着。

    stale 只用于披露，不修正任何动作——断更让连续天数**偏小**，方向只会"该升未升"，
    不存在误升级，因此不需要拦截，只需要让用户知道天数不可信。
    """
    result: dict[str, Any] = {
        "last_captured_trade_date": None,
        "expected_trade_date": None,
        "stale": True,
        "note": None,
    }
    try:
        from app.database import _connect
        from app.services.trading_session import get_previous_trade_date

        expected = (
            get_previous_trade_date(str(as_of_trade_date).strip())
            if str(as_of_trade_date or "").strip()
            else None
        )
        result["expected_trade_date"] = expected
        with _connect() as connection:
            row = connection.execute(
                "SELECT MAX(trade_date) AS last_captured FROM sector_direction_states "
                "WHERE (source IS NULL OR source = 'captured')",
            ).fetchone()
        last_captured = (
            str(_row_value(row, "last_captured") or "").strip() or None
            if row is not None
            else None
        )
        result["last_captured_trade_date"] = last_captured
        if last_captured is None:
            result["note"] = (
                "方向状态账本尚无捕获记录，连续跌破天数只能从今日起算（下界）"
            )
            return result
        # 交易日字符串同为 ISO 日期，字典序即时间序。expected 不可得时保守判 stale。
        stale = expected is None or last_captured < expected
        result["stale"] = stale
        if stale:
            result["note"] = (
                f"方向状态账本最后捕获日为 {last_captured}"
                f"（应覆盖到 {expected or '未知'}），连续跌破天数为下界、可能低估"
            )
        return result
    except Exception:  # noqa: BLE001 — 健康度是披露项，查不到不能拖垮日报
        logger.warning("读取方向状态账本健康度失败", exc_info=True)
        result["note"] = "方向状态账本健康度不可得，连续跌破天数按下界解读"
        return result


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


def reconcile_entry_contract_with_holding(
    contract: Mapping[str, Any],
    *,
    first_purchase_date: str | None,
    first_seen_date: str | None,
    rebase_score_loader: Any = None,
    tolerance_days: int = ENTRY_REBASE_TOLERANCE_DAYS,
) -> dict[str, Any]:
    """把入场契约与这笔持仓的真实时间线核对，返回（可能被修正的）契约。

    契约来自 discovery 的 buy 决策事件——它在**报告生成时**就冻结了，代表的是"那天系统
    推荐买入时方向长什么样"，不是"用户实际买入时方向长什么样"。两者错位时相对退出基线
    （"买入时 69 分、现在 41 分"）从第一天就是错的，比没有基线更危险。三种情形：

    1. **推荐之前就已持有**（购入日/首见日早于推荐日）：这笔推荐根本不是这笔持仓的入场，
       契约打上 `disqualified_reason`，退化为绝对模式并披露。首见日是"实际买入日的上界"，
       它早于推荐日即可确定持有在前——反过来（首见晚于推荐）不能确定什么，导入截图有延迟。
    2. **实际买入日晚于推荐日超过容差**：推荐日的分数不再代表买入决策。尝试从方向状态
       账本取买入日当天或此前 `_REBASE_LOOKBACK_DAYS` 个自然日内的有证据趋势分作新基线
       （`entry_rebased_from` 保留原推荐日；参与度/价格位置清空，账本里没有对应快照就不
       编造）；取不到 → `disqualified_reason`，绝对模式并披露。
    3. **容差以内**：这笔买入执行的就是那次推荐，契约原样生效。

    只在两个日期都可解析时核对；解析不了就原样返回——"不知道"不等于"错位"，与
    `_unrealized_loss_add_percent` 对 `None` 的处理同一纪律。`rebase_score_loader`
    由调用方注入（签名 `(sector_label, on_or_before, not_before) -> (trade_date, score) | None`），
    本函数自身无 IO、可直接单测。
    """
    result = dict(contract)
    entry_date = _parse_iso_date(result.get("entry_date"))
    if entry_date is None:
        return result

    purchase = _parse_iso_date(first_purchase_date)
    first_seen = _parse_iso_date(first_seen_date)

    held_before = None
    if purchase is not None and purchase < entry_date:
        held_before = (str(first_purchase_date), "购入日")
    elif first_seen is not None and first_seen < entry_date:
        held_before = (str(first_seen_date), "持仓首次出现日")
    if held_before is not None:
        when, kind = held_before
        result["disqualified_reason"] = (
            f"该持仓的{kind} {when} 早于 {result.get('entry_date')} 的买入推荐，"
            "这笔推荐不是该持仓的入场决策，不能用它的方向分做相对比较，本次按绝对退出线判定"
        )
        return result

    if purchase is None:
        return result
    gap_days = (purchase - entry_date).days
    if gap_days <= max(0, int(tolerance_days)):
        return result

    original_entry_date = str(result.get("entry_date") or "")
    rebased = None
    if callable(rebase_score_loader):
        not_before = (purchase - timedelta(days=_REBASE_LOOKBACK_DAYS)).isoformat()
        try:
            rebased = rebase_score_loader(
                str(result.get("sector_label") or ""),
                purchase.isoformat(),
                not_before,
            )
        except Exception:  # noqa: BLE001 — 基线重定是增强项，查询失败按取不到处理
            logger.warning("入场基线重定查询失败", exc_info=True)
            rebased = None
    if rebased is not None:
        rebased_date, rebased_score = rebased
        result.update(
            entry_date=str(rebased_date),
            entry_trend=float(rebased_score),
            # 账本里只有趋势分数轴可靠可回读（参见回填契约：participation 是中性填充）。
            # 买入日的参与度/价格位置没有可信快照，清空而不是留着推荐日的旧值冒充。
            entry_participation=None,
            entry_position_risk=None,
            entry_rebased_from=original_entry_date,
        )
        return result

    result["disqualified_reason"] = (
        f"实际购入日 {first_purchase_date} 晚于 {original_entry_date} 的买入推荐超过 "
        f"{int(tolerance_days)} 天，且方向账本没有购入日附近的有证据分数，"
        "无法建立可信的入场基线，本次按绝对退出线判定"
    )
    return result


def load_trend_score_on_or_before(
    sector_label: str,
    on_or_before: str,
    not_before: str,
) -> tuple[str, float] | None:
    """从方向状态账本取 `[not_before, on_or_before]` 内最近一天的有证据趋势分。

    供入场基线重定使用。`captured` 与 `backfilled` 都收——与退出侧趋势历史同一取数
    契约（回填行的趋势轴是纯函数重算的真实值，只有滞回三列不可信）。占位值行
    （`trend_evidence_coverage` 为 0/NULL）照旧排除。
    """
    label = str(sector_label or "").strip()
    if not label or not str(on_or_before or "").strip() or not str(not_before or "").strip():
        return None
    try:
        from app.database import _connect

        with _connect() as connection:
            row = connection.execute(
                "SELECT trade_date, trend_strength_score FROM sector_direction_states "
                "WHERE sector_label = ? AND trade_date <= ? AND trade_date >= ? "
                "AND trend_evidence_coverage > 0 "
                "AND trend_strength_score IS NOT NULL "
                "ORDER BY trade_date DESC LIMIT 1",
                (label, str(on_or_before), str(not_before)),
            ).fetchone()
    except Exception:  # noqa: BLE001 — 查不到只是基线重定失败，不拖垮日报
        logger.warning("读取买入日附近的方向分失败", exc_info=True)
        return None
    if row is None:
        return None
    score = _num(row["trend_strength_score"])
    if score is None:
        return None
    return str(row["trade_date"]), score


def _parse_iso_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


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
        # 买入当时**承诺**的失效条件（结构化 code）。它是"当初说好什么情况下这笔就错了"的
        # 唯一凭证：策略参数以后会变，但已经发生的那笔买入必须按当时的承诺复核。
        "promised_invalidation": _promised_invalidation_from_snapshot(
            payload,
            sector_label,
        ),
    }


def _promised_invalidation_from_snapshot(
    payload: Mapping[str, Any],
    sector_label: str,
) -> list[dict[str, Any]]:
    """从冻结快照里取出买入时那一行的 `invalidation_checks`（只留 code 与文案）。"""
    replay = payload.get("replay_bundle")
    facts = (replay or {}).get("facts_snapshot") if isinstance(replay, Mapping) else None
    rows = (facts or {}).get("sector_opportunities") if isinstance(facts, Mapping) else None
    if not isinstance(rows, Sequence):
        return []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("sector_label") or "").strip() != sector_label:
            continue
        checks = row.get("invalidation_checks")
        if not isinstance(checks, Sequence):
            return []
        promised: list[dict[str, Any]] = []
        for check in checks:
            if not isinstance(check, Mapping):
                continue
            code = str(check.get("code") or "").strip()
            if not code:
                continue
            promised.append({"code": code, "label": str(check.get("label") or "").strip()})
        return promised
    return []


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
    "ENTRY_REBASE_TOLERANCE_DAYS",
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
    "load_direction_ledger_health",
    "load_direction_trend_history",
    "load_trend_score_on_or_before",
    "reconcile_entry_contract_with_holding",
]
