from __future__ import annotations

"""荐基 guard 与日报 guard 共用的文本处理 helper。

抽取自 discovery_guard.py（2026-06-30 P0.5 弱证据降级/结构化字段人话化），2026-07
日报升级时把与「决策证据文本」相关、跟 discovery 无强耦合的部分下沉到这里，供
recommendation_guard.py 复用同一套人话化/归一化逻辑，避免日报和荐基的措辞、字段
命名规则各写一套、后续维护时口径漂移。
"""

import re

_TRACK_LABELS = {
    "momentum": "顺势观察",
    "setup": "蓄势观察",
}

_PATTERN_LABELS = {
    "accumulation": "回调中有资金承接",
    "aligned_up": "上涨有资金配合",
    "distribution": "涨幅较快但资金流出",
    "flow_date_mismatch": "资金日期需核验",
    "flow_turning_positive": "资金开始转正",
    "multi_day_outflow_then_inflow": "资金由流出转回流",
    "price_flow_aligned_up": "上涨有资金配合",
    "weak_outflow": "资金偏弱",
}

# (pattern, replacement) 对，按顺序应用；日报/荐基各自可能有专属字段，
# 调用方可通过 extra_replacements 追加。
_BASE_REGEX_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (
        r"nav_trend\.distance_from_high_percent\s*(?:=|为|约)?\s*([-+]?\d+(?:\.\d+)?)%?",
        "距离近期高点约 {0}%",
    ),
    (
        r"max_drawdown_1y_percent\s*(?:=|为|约)?\s*([-+]?\d+(?:\.\d+)?)%?",
        "近1年最大回撤约 {abs0}%",
    ),
    (
        r"estimated_daily_return_percent\s*(?:=|为|约)?\s*([-+]?\d+(?:\.\d+)?)%?",
        "今日涨跌约 {0}%",
    ),
    (
        r"distance_from_high_percent\s*(?:=|为|约)?\s*([-+]?\d+(?:\.\d+)?)%?",
        "距离近期高点约 {0}%",
    ),
    (
        r"heat_score\s*(?:=|为|约)?\s*([-+]?\d+(?:\.\d+)?)",
        "板块热度分 {0}",
    ),
)

_BASE_TEXT_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("sector_opportunities 得分", "系统方向得分"),
    ("sector_opportunities", "系统筛出的主方向"),
    ("quality_reasons", "加分原因"),
    ("quality_penalties提示", "系统校验提示"),
    ("quality_penalties", "系统校验提示"),
    ("sector_estimate", "板块估算"),
    ("nav_trend", "净值走势"),
    ("return_3m_percent", "近3月收益"),
    ("return_6m_percent", "近6月收益"),
    ("return_1y_percent", "近1年收益"),
)


def humanize_evidence_text(
    text: str,
    *,
    extra_regex_replacements: tuple[tuple[str, str], ...] = (),
    extra_text_replacements: tuple[tuple[str, str], ...] = (),
) -> str:
    """把内部字段名/枚举值替换为中文措辞，避免 LLM/guard 生成的文本泄漏内部字段名。"""
    if not text:
        return text
    result = str(text)
    for pattern, template in (*_BASE_REGEX_REPLACEMENTS, *extra_regex_replacements):
        result = re.sub(
            pattern,
            lambda match, _template=template: _format_number_template(_template, match),
            result,
            flags=re.IGNORECASE,
        )
    result = re.sub(
        r"confidence\s*(?:=|为)?\s*([高中低])",
        lambda match: f"置信度{match.group(1)}",
        result,
        flags=re.IGNORECASE,
    )
    result = re.sub(
        r"track=([a-z_]+)",
        lambda match: track_label(match.group(1)),
        result,
        flags=re.IGNORECASE,
    )
    result = re.sub(
        r"pattern=([a-z_]+)",
        lambda match: pattern_label(match.group(1)),
        result,
        flags=re.IGNORECASE,
    )
    result = re.sub(
        r"fund_quality_score\s*(?:=|为)?\s*([-+]?\d+(?:\.\d+)?)",
        lambda match: f"基金质量分 {fmt_num(match.group(1))}",
        result,
        flags=re.IGNORECASE,
    )
    result = re.sub(
        r"sector_fit_score\s*(?:=|为)?\s*([-+]?\d+(?:\.\d+)?)",
        lambda match: f"板块匹配分 {fmt_num(match.group(1))}",
        result,
        flags=re.IGNORECASE,
    )
    for old, new in (*_BASE_TEXT_REPLACEMENTS, *extra_text_replacements):
        result = re.sub(re.escape(old), new, result, flags=re.IGNORECASE)
    return result


def _format_number_template(template: str, match: re.Match) -> str:
    raw = match.group(1)
    return template.format(fmt_num(raw), abs0=fmt_abs_num(raw))


def track_label(track: object) -> str:
    normalized = str(track or "").strip().lower()
    return _TRACK_LABELS.get(normalized, str(track or "未知"))


def pattern_label(pattern: object) -> str:
    normalized = str(pattern or "").strip().lower()
    return _PATTERN_LABELS.get(normalized, str(pattern or "未知"))


def normalize_confidence_label(confidence: object) -> str:
    text = str(confidence or "").strip()
    if text in {"高", "中", "低"}:
        return text
    return "中"


def append_unique(existing: list[str], additions: list[str], *, limit: int) -> list[str]:
    result: list[str] = []
    for item in [*existing, *additions]:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def fmt_num(value: object) -> str:
    if value is None:
        return "未知"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{number:.2f}".rstrip("0").rstrip(".")


def fmt_abs_num(value: object) -> str:
    if value is None:
        return "未知"
    try:
        number = abs(float(value))
    except (TypeError, ValueError):
        return str(value)
    return f"{number:.2f}".rstrip("0").rstrip(".")


def as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# --- M2.1/M2.2：双向 guard 升级判定（动作激进度扩展 bucket + resolve_escalation_floor） ---

# 动作激进度 bucket：数值越低越保守。0~3 是 recommendation_guard.py 原有区间
# （减仓评估=0 < 观察=1 < 暂停追涨=2 < 分批加仓=3；"暂停追涨"因隐含"当前有上涨动能，
# 但主动按住不追"的语义，在原有实现里被排在比"观察"更靠"加仓"一侧的位置——这是
# 既有代码的既定语义，本次不改动）。M2.2 向下扩展两档更强的减仓动作，不改变原有
# 4 档的数值，确保 recommendation_guard.py 现有逻辑与测试不受影响。
ACTION_BUCKET_CLEAR_ALL = -2  # 清仓评估
ACTION_BUCKET_DEEP_REDUCE = -1  # 大幅减仓评估
ACTION_BUCKET_REDUCE = 0  # 减仓评估
ACTION_BUCKET_WATCH = 1  # 观察
ACTION_BUCKET_PAUSE = 2  # 暂停追涨
ACTION_BUCKET_ADD = 3  # 分批加仓

_NO_ESCALATION: dict[str, object] = {
    "min_bucket": None,
    "min_action_label": "",
    "reasons": [],
    "suggested_position_change_percent": None,
    "basis": "",
}

# M2.2：动作词表扩展为 7 个（观察/暂停追涨/分批加仓/减仓评估/大幅减仓评估/清仓评估/
# 风控复核），对应上方 6 档 bucket（"风控复核"是 bucket 0 内的措辞变体，不单独占一档，
# 由 recommendation_guard.normalize_action_text 的专属特判处理，不在此表内）。
ACTION_BUCKET_LABELS: dict[int, str] = {
    ACTION_BUCKET_CLEAR_ALL: "清仓评估",
    ACTION_BUCKET_DEEP_REDUCE: "大幅减仓评估",
    ACTION_BUCKET_REDUCE: "减仓评估",
    ACTION_BUCKET_WATCH: "观察",
    ACTION_BUCKET_PAUSE: "暂停追涨",
    ACTION_BUCKET_ADD: "分批加仓",
}


# 用于"双向 guard 升级判定"的保守度排名，与上面 ACTION_BUCKET_* 的原始数值顺序
# 故意不完全一致：ACTION_BUCKET_PAUSE(2) 数值大于 ACTION_BUCKET_WATCH(1)，这是
# recommendation_guard.py 历史上"激进度封顶"（_max_allowed_bucket）逻辑遗留的顺序——
# 那套逻辑只做"是否达到某阈值"的判断（阈值只取 2 或 3），从未真正依赖 watch 和 pause
# 谁的数值更大，改动它们的相对顺序不会影响封顶逻辑。但在双向 guard 的"升级判定"里，
# "暂停追涨"语义上应被视为比"观察"更保守（观察不排除后续加仓的可能，暂停追涨则明确
# 按住不追），若直接复用原始 bucket 数值判断"当前动作是否比升级下限更激进"，会导致
# 观察(1) 无法被判定为比 暂停追涨(2) 更激进（1 > 2 为假），使 M1 场景（LLM 给"观察"、
# 系统本该升级为"暂停追涨"）完全升级不动——这是本次升级要修的核心 bug 的一个真实回归
# 案例，通过引入独立的"升级严重度排名"而非直接复用原始 bucket 数值来解决。
_ESCALATION_SEVERITY_RANK: dict[int, int] = {
    ACTION_BUCKET_CLEAR_ALL: 0,
    ACTION_BUCKET_DEEP_REDUCE: 1,
    ACTION_BUCKET_REDUCE: 2,
    ACTION_BUCKET_PAUSE: 3,
    ACTION_BUCKET_WATCH: 4,
    ACTION_BUCKET_ADD: 5,
}


def escalation_severity_rank(bucket: int) -> int:
    """把 `ACTION_BUCKET_*` 值映射为"升级判定"专用的保守度排名（数值越小越保守）。

    仅供 `resolve_escalation_floor()` 的调用方在比较"当前动作 bucket"与其返回的
    `min_bucket` 谁更激进时使用；不要用于 `_max_allowed_bucket` 一类的封顶逻辑
    （那类逻辑应继续使用原始 `ACTION_BUCKET_*` 数值，语义不同，见上方注释）。
    """
    return _ESCALATION_SEVERITY_RANK.get(bucket, _ESCALATION_SEVERITY_RANK[ACTION_BUCKET_WATCH])


def classify_action_bucket(action: str) -> int:
    """把任意动作文本分类到 `ACTION_BUCKET_*` 6 档区间（越低越保守）。

    识别顺序很重要：更强烈的减仓词（清仓/大幅减仓）必须先于泛化的"减仓"关键词判断，
    否则"大幅减仓评估"会被"减仓"子串误判为 bucket 0 而非 bucket -1；"清仓评估"本身
    不含"减仓"子串，若不优先判断会落入默认的"观察"分支。

    抽取自 recommendation_guard.py 与 report_judge.py 此前各自维护的两份几乎相同、
    但 token 集合有细微差异的 `_action_bucket` 私有函数（2026-07 M2.2），统一为唯一
    权威判定，避免两处随时间各自漂移。
    """
    text = (action or "").strip()
    if "清仓" in text:
        return ACTION_BUCKET_CLEAR_ALL
    if "大幅减仓" in text:
        return ACTION_BUCKET_DEEP_REDUCE
    if any(token in text for token in ("减仓", "复核", "风控", "降仓")):
        return ACTION_BUCKET_REDUCE
    if any(token in text for token in ("暂停", "勿追涨", "勿追", "观望")):
        return ACTION_BUCKET_PAUSE
    if any(token in text for token in ("加仓", "定投", "分批")):
        return ACTION_BUCKET_ADD
    return ACTION_BUCKET_WATCH


def resolve_escalation_floor(
    *,
    sector_opportunity: dict | None,
    evidence: dict | None,
    market_breadth: dict | None,
    over_concentration: bool,
    has_unrealized_gain: bool,
    decision_style: str,
) -> dict[str, object]:
    """双向 guard 的"升级下限"判定（M2.1）。

    设计文档：docs/superpowers/specs/2026-07-02-ai-decision-sharpening-design.md 第 M2.1 节。

    返回 `{min_bucket, reasons, suggested_position_change_percent, basis}`：

    - `min_bucket` 与上方 `ACTION_BUCKET_*` 同一数值体系（越低越保守）。调用方在
      LLM 给出的动作 bucket **高于** `min_bucket` 时，应强制下调到 `min_bucket`
      对应的动作。由于该数值体系里"低"即"更保守"，这个"下调"在证据触发升级时
      实际表现为"把原本过于宽松的动作往更保守的方向强制拉回"——这正是本次升级要
      修的"guard 只防乐观、不防迟钝"的单向缺陷（能把 bucket 拉低但不能把 bucket
      拉高的旧机制，现在双向都能拉，只是通过同一个"封顶"操作实现）。
    - `min_bucket=None` 表示本次未触发任何升级，调用方保持现有单向 guard 逻辑不变。
    - `suggested_position_change_percent` 为负数（建议减仓比例，相对当前持仓金额）；
      触发但设计未给出具体比例的档位（仅第一档，暂停追涨）返回 `None`。
    - 具体数值区间（20~30% / 30~40% / 40~60%）是设计稿给出的**初始建议值，未做
      历史回测校准**（M6 待办）；当前实现取各区间中值，校准后再替换为真实数值。

    **两处信号可用性限制（诚实记录，非掩盖，均只影响触发门槛最高的第 5 档）：**

    1. "量价背离显著"用 `sector_opportunity.confidence == "高"` 判定——该值仅在
       M1.4 修复后、distribution/accumulation 规则 `significant=True` 且
       `edge_percent>=10` 时才会出现。调用方传入的 `sector_opportunity` 是
       `describe_sector_opportunity()` 的输出，不携带原始回测桶，因此本函数无法
       区分 edge=10 与 edge=25 的强度差异；设计稿第 5 档要求的"edge≥15"在当前
       签名下退化为与第 1~4 档相同的"confidence==高"判定，不做额外区分。
    2. 第 5 档"已破位"未作为独立信号传入（函数签名未给出该字段的数据来源），
       用 `len(sector_opportunity.penalties) >= 2`（同时命中多条警示，如"资金
       背离或持续流出"叠加"单日涨幅过热"）作为"多重信号共振"的可行近似。

    以上两点不影响第 1~4 档（均用签名里明确给出的字段判定），第 5 档本身也是
    "仅在极端情形下触发"的兜底档，误差容忍度更高；待 M1.3 的量价背离回测在生产
    环境验证出真实历史窗口后，可考虑扩展签名直接传入原始 edge_percent 做更精确判定。

    `decision_style` 只影响第 4/5 档的门槛松紧（tactical/aggressive 更容易触发），
    不是触发的必要条件——conservative 风格下证据极强时同样可以触发第 4/5 档。
    """
    if not sector_opportunity or sector_opportunity.get("opportunity_available") is not False:
        return dict(_NO_ESCALATION)

    has_strong_divergence = str(sector_opportunity.get("confidence") or "") == "高"
    if not has_strong_divergence:
        return dict(_NO_ESCALATION)

    lenient = decision_style in {"tactical", "aggressive"}

    reasons: list[str] = ["量价背离信号显著，且当前持仓板块方向不构成机会"]
    min_bucket: int = ACTION_BUCKET_PAUSE
    percent: float | None = None

    composite = (evidence or {}).get("composite") or {}
    evidence_level = str(composite.get("level") or "")
    weak_fund_evidence = evidence_level in {"低", "不足"}

    if weak_fund_evidence:
        min_bucket = ACTION_BUCKET_REDUCE
        percent = -25.0
        reasons.append("该基金自身量化证据（因子/风险/信号综合置信）不足以支撑继续观望")

        if has_unrealized_gain:
            percent = -35.0
            reasons.append("当前持仓浮盈，落袋压力更小，建议提高减仓比例")

        breadth = market_breadth or {}
        sentiment_ice = str(breadth.get("sentiment_level") or "") == "冰点"
        sentiment_dropping = (breadth.get("sentiment_level_change") or 0) <= -2
        breadth_extreme = sentiment_ice and sentiment_dropping
        row4_triggered = (
            (breadth_extreme or over_concentration)
            if lenient
            else (breadth_extreme and over_concentration)
        )
        if row4_triggered:
            min_bucket = ACTION_BUCKET_DEEP_REDUCE
            percent = -50.0
            reasons.append("大盘情绪骤冷叠加持仓集中度超限，风险共振加剧")

            # 注意：第5档门槛（>=2 条 penalties）不随 decision_style 松紧——第4档已经
            # 通过"or/and"切换让 lenient 风格更容易触发，若第5档也同步降到与"触发
            # 第4档"完全相同的门槛（即恒真，因为 M1.4 disqualified 场景天然自带至少
            # 1 条 penalty），会让第5档在 lenient 风格下形同虚设、与第4档无法区分。
            # 因此第5档统一保留"至少2条 penalty 同时命中"的更高门槛，作为比第4档
            # 更严格的独立信号，而不是第4档的简单加强版。
            penalty_count = len(sector_opportunity.get("penalties") or [])
            if penalty_count >= 2:
                min_bucket = ACTION_BUCKET_CLEAR_ALL
                percent = -100.0
                reasons.append("多重强信号极端共振，风险已超出常规减仓可控范围")

    return {
        "min_bucket": min_bucket,
        "min_action_label": ACTION_BUCKET_LABELS[min_bucket],
        "reasons": reasons,
        "suggested_position_change_percent": percent,
        "basis": "；".join(reasons),
    }


# --- M4：荐基双向 guard 升级判定（与日报 resolve_escalation_floor 思路一致，语义不同） ---

_NO_DISCOVERY_ESCALATION: dict[str, object] = {
    "action": None,
    "amount_multiplier": None,
    "reasons": [],
    "basis": "",
}

_DISCOVERY_WEAK_QUALITY_THRESHOLD = 55.0
_DISCOVERY_STRONG_QUALITY_THRESHOLD = 75.0
_DISCOVERY_AMOUNT_BOOST_MULTIPLIER = 1.2


def resolve_discovery_escalation(
    *,
    sector_opportunity: dict | None,
    pool_item: dict | None,
) -> dict[str, object]:
    """荐基双向 guard 升级判定（M4）。

    设计文档：docs/superpowers/specs/2026-07-02-ai-decision-sharpening-design.md 第 M4 节。

    与日报 `resolve_escalation_floor` 思路一致（用 `sector_opportunity.confidence=="高"`
    作为"量价背离证据是否足够强"的判定入口），但**荐基语义不同**——荐基推荐的是"要不要
    买入一只新基金"，不涉及"清仓已持仓"，因此这里不产出 `min_bucket`，而是：

    - `action="exclude"`：证据强烈指向该候选**不该出现在候选池里**，调用方应把它从
      最终推荐列表中剔除（而不是像日报那样"降级为更保守的动作文字"）。
    - `action="boost"`：证据强烈支持该候选，允许给比常规预算上限更高的建议买入金额
      （`amount_multiplier` 相对调用方原有金额的乘数；调用方仍应在此基础上应用
      预算/集中度上限，boost 不能突破硬约束，只是把"软上限"抬高）。
    - `action=None`：未触发，调用方沿用既有逻辑（弱证据仅降级动作文案为"建议关注"，
      不剔除；证据正常时金额仅受预算/集中度上限约束，不做提升）。

    两个方向都要求 `sector_opportunity.confidence == "高"`——按 M1.4 的实现，这只有在
    量价背离历史回测证据极强时才会出现，代表"系统对这个方向的量化判断本身很有把握"
    （无论方向是好是坏），与 `_weak_evidence_reasons` 里 `confidence in {"低","不足"}`
    （"不确定、看不清"）是两种完全不同、互不冲突的信号：

    - 负向（`opportunity_available=False`）：仅当该基金自身 `fund_quality_score` 也
      偏弱（<55 或缺失）时才剔除——要求板块和基金两个维度同时印证，避免误伤"板块暂时
      承压但基金本身质地扎实"的候选（只有板块弱、基金本身强时，仍保留在池内、只是
      不作为首选，由既有的 `_should_downgrade_weak_evidence` 弱证据降级处理）。
    - 正向（`opportunity_available=True`）：仅当该基金自身 `fund_quality_score` 也
      足够高（>=75）时才提额，同样要求两个维度共振。

    金额提升比例（+20%）与质量分阈值（55/75，复用 discovery_guard.py 既有的 55 分
    "质量分偏低"门槛，75 分是新引入的"质量分偏高"对称门槛）均为初始建议值，**未做
    历史回测校准**（M6 待办，与 `resolve_escalation_floor` 的仓位比例同样性质）。
    """
    if not sector_opportunity or str(sector_opportunity.get("confidence") or "") != "高":
        return dict(_NO_DISCOVERY_ESCALATION)

    opportunity_available = sector_opportunity.get("opportunity_available")
    quality = as_float((pool_item or {}).get("fund_quality_score"))

    if opportunity_available is False:
        if quality is None or quality < _DISCOVERY_WEAK_QUALITY_THRESHOLD:
            reasons = ["量价背离信号显著，板块方向不构成机会", "该基金质量分同样偏低，两项证据共振"]
            return {
                "action": "exclude",
                "amount_multiplier": None,
                "reasons": reasons,
                "basis": "；".join(reasons),
            }
        return dict(_NO_DISCOVERY_ESCALATION)

    if opportunity_available is True:
        if quality is not None and quality >= _DISCOVERY_STRONG_QUALITY_THRESHOLD:
            reasons = ["量价背离回测显著支持该方向", "该基金质量分较高，两项证据共振"]
            return {
                "action": "boost",
                "amount_multiplier": _DISCOVERY_AMOUNT_BOOST_MULTIPLIER,
                "reasons": reasons,
                "basis": "；".join(reasons),
            }
        return dict(_NO_DISCOVERY_ESCALATION)

    return dict(_NO_DISCOVERY_ESCALATION)
