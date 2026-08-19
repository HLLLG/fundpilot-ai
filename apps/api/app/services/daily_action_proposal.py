from __future__ import annotations

"""日报的确定性动作提议：把 LLM 从「决策来源」降为「解释层」。

## 为什么需要它

荐基（discovery）的分工是"确定性系统做决定、LLM 做解释"：
`discovery_facts.build_recommendation_candidate_scope()` 先联立方向动作边界、基金质量、
载体质量与板块身份产出一份白名单，逐只标 `actionable / conditional_wait / watch_only`；
prompt 明确要求 `suggested_amount_yuan` 输出 null，金额由 `discovery_allocator` 统一算。
模型对"买不买、买多少"没有话语权。

日报此前是反过来的：`analyze_pipeline.run_analysis()` 全文没有任何决策计算，`action`
完全由 LLM 从 `allowed_actions` 里挑，然后 `recommendation_guard` 一路往下压。整条链路
里**没有任何函数回答"这只持仓今天应该是什么动作"**，只有一串"不许比这个更激进"的天花板。

后果是单向的：系统只会让结论更保守，永远不会比模型更果断。模型含糊，报告就含糊——
一个 `entry_state=ready_to_start`、资金参与度与价格位置都过线、量化证据为「高」、没有任何
风险触发的持仓，只要模型写了"观察"，系统就照样输出"观察"，而它自己的规则本来是支持加仓的。

## 这个模块做什么

`propose_daily_action()` 用**已经算好的门禁结果**给出一个动作提议。
`recommendation_guard._promote_to_proposed_add()` 在整条 clamp 链跑完之后消费它，只做一件
事：把"系统规则支持加仓、但结论仍停在被动动作（观察/暂停追涨）"的情况抬到「分批加仓」。

**为什么不是直接拿提议当动作链的输入**（第一版这样写，是错的）：既有的每一道 clamp 与它
对应的解释文案都建立在"模型提了什么、被什么规则改掉了"之上。把提议塞进链首会同时砸掉两
件事——`>= ACTION_BUCKET_ADD` 的分支不再触发，用户看不到"为什么不能加仓"；更糟的是模型
提出的风险动作（如「减仓评估」）会被提议的中性基线**放松**成「观察」。系统可以比模型更
果断地买，但绝不能比模型或规则更轻率地放松风险结论。

关键纪律：

* **提议只在正向证据齐备时才开加仓**。九个条件全部为真才提议 `分批加仓`，任何一条不满足
  就退回观察/风控复核。这里不放宽任何门禁——提升发生在 clamp 链之后、动作词表与交易门禁
  之前，因此提升出来的加仓仍要过剩下那几道门。
* **要求方向成熟度层真实存在**。`_entry_state_add_block_reason()` 的既有取舍是"快照缺席
  不拦"（缺的是子层，旧机会分仍能回答方向问题）。但"不拦"不等于"可以据此主动提议买入"：
  提议侧要求 `entry_state` 必须在场且已就绪，比 guard 侧更严。
* **风险方向复用 `resolve_escalation_floor`**，不新写一套。降档判定已经在那里，提议只是
  把它的结论翻译成动作。
* **不碰金额**。比例仍由 `_resolve_deterministic_position_change` 算，本模块只出动作。
"""

from dataclasses import dataclass, field
from typing import Any

from app.services.decision_guard_shared import (
    ACTION_BUCKET_ADD,
    ACTION_BUCKET_LABELS,
    ACTION_BUCKET_REDUCE,
    ACTION_BUCKET_WATCH,
    classify_action_bucket,
)

DAILY_ACTION_PROPOSAL_SCHEMA_VERSION = "daily_action_proposal.v1"

_WATCH = ACTION_BUCKET_LABELS[ACTION_BUCKET_WATCH]
_RISK_REVIEW = "风控复核"


@dataclass(frozen=True)
class DailyActionProposal:
    """一条持仓的确定性动作提议。

    `action` 是提议本身；`reason_codes` 让"为什么不是加仓"可核验（前端与复盘都需要区分
    "方向没到"和"数据没到"）；`supports_add` 单独留出来是因为它是唯一一条会让结论变得
    **更积极**的路径，需要能被独立断言。
    """

    action: str
    reason_codes: tuple[str, ...] = ()
    supports_add: bool = False
    blocked_add_reasons: tuple[str, ...] = field(default=())

    @property
    def bucket(self) -> int:
        return classify_action_bucket(self.action)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": DAILY_ACTION_PROPOSAL_SCHEMA_VERSION,
            "action": self.action,
            "reason_codes": list(self.reason_codes),
            "supports_add": self.supports_add,
            "blocked_add_reasons": list(self.blocked_add_reasons),
        }


def _risk_baseline(risk_level: str, suggested_action: str) -> str:
    if risk_level == "high" or suggested_action == "risk_review":
        return _RISK_REVIEW
    return _WATCH


def propose_daily_action(
    *,
    risk_level: str,
    risk_suggested_action: str,
    escalation_min_bucket: int | None,
    max_allowed_bucket: int,
    entry_state: str | None,
    entry_state_block_reason: str | None,
    sector_absence_reason: str | None,
    opportunity_available: bool | None,
    weak_evidence_reasons: tuple[str, ...] | list[str],
    reversal_blocked: bool,
    execution_blocked: bool,
    additional_add_blocks: tuple[str, ...] | list[str] = (),
) -> DailyActionProposal:
    """按已算好的门禁结果给出动作提议。

    参数刻意全部是**标量/布尔**：本模块不重算任何门禁，也不 import
    `recommendation_guard`。门禁只有一处实现（guard 里），提议只消费它的结论——任务 1
    的教训就是同一个判断在两处各写一份必然漂移。
    """
    # 数据时点不过关时不做任何方向判断：这与 guard 的 `execution_blocked` 分支同义，
    # 提议在这里先行退让，避免下游看到一个"系统提议加仓、但被数据门禁拦掉"的矛盾记录。
    if execution_blocked:
        return DailyActionProposal(
            action=_risk_baseline(risk_level, risk_suggested_action),
            reason_codes=("decision_evidence_not_ready",),
        )

    # 风险升级方向直接复用 `resolve_escalation_floor` 的结论，不新写判定。
    if escalation_min_bucket is not None and escalation_min_bucket <= ACTION_BUCKET_REDUCE:
        return DailyActionProposal(
            action=ACTION_BUCKET_LABELS[escalation_min_bucket],
            reason_codes=("risk_escalation",),
        )

    blocked: list[str] = []
    if sector_absence_reason:
        blocked.append("sector_direction_evidence_absent")
    if not entry_state:
        # 提议侧比 guard 严：guard 对"成熟度子层缺席"不拦（旧机会分仍在），但缺席时
        # 没有任何东西能证明"现在可以开始买"，因此不足以主动提议加仓。
        blocked.append("entry_state_unavailable")
    elif entry_state_block_reason:
        blocked.append("entry_state_not_ready")
    if opportunity_available is False:
        blocked.append("opportunity_unavailable")
    if weak_evidence_reasons:
        blocked.append("weak_evidence")
    if escalation_min_bucket is not None and escalation_min_bucket < ACTION_BUCKET_ADD:
        blocked.append("risk_escalation_floor")
    if max_allowed_bucket < ACTION_BUCKET_ADD:
        blocked.append("risk_ceiling")
    if reversal_blocked:
        blocked.append("reversal_or_pullback")
    for extra in additional_add_blocks or ():
        code = str(extra).strip()
        if code:
            blocked.append(code)

    if blocked:
        return DailyActionProposal(
            action=_risk_baseline(risk_level, risk_suggested_action),
            reason_codes=("add_not_supported",),
            blocked_add_reasons=tuple(dict.fromkeys(blocked)),
        )

    return DailyActionProposal(
        action=ACTION_BUCKET_LABELS[ACTION_BUCKET_ADD],
        reason_codes=("direction_and_fund_evidence_support_add",),
        supports_add=True,
    )


__all__ = [
    "DAILY_ACTION_PROPOSAL_SCHEMA_VERSION",
    "DailyActionProposal",
    "propose_daily_action",
]
