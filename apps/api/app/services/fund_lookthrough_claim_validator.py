"""Conservatively validate generated prose against fund-lookthrough facts.

The validator is deliberately a pure, post-generation utility.  It scans only a
small allow-list of narrative fields, never changes structured actions or
amounts, and never copies raw claims or holdings into its audit trail.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence
import unicodedata


CLAIM_AUDIT_SCHEMA_VERSION = "fund_lookthrough_claim_audit.v1"

_RECOMMENDATION_LIST_FIELDS = (
    "points",
    "sector_evidence",
    "fund_evidence",
    "risks",
    "news_bullish",
    "news_bearish",
    "validation_notes",
)
_RECOMMENDATION_SCALAR_FIELDS = (
    "amount_note",
    "decision_path",
    "suggested_position_change_basis",
)
_TOP_LEVEL_NARRATIVE_FIELDS = ("title", "summary", "market_view")

_SCOPE_NOTICE = (
    "持仓证据来自定期报告披露，仅代表报告截止日的披露范围，"
    "不是当前、实时或完整持仓。"
)
_NO_COMMON_NOTICE = "披露范围内未发现共同证券，完整组合重合未知。"
_CROSS_VINTAGE_NOTICE = (
    "报告期不一致，仅作跨期披露相似度，不是当前重合下界。"
)
_IDENTITY_INSUFFICIENT_NOTICE = "证券身份披露不足，完整组合重合未知。"
_UNVERIFIED_NUMERIC_NOTICE = (
    "该候选缺少可核验的同报告期持仓重合下限，相关重合数字已省略。"
)
_MISSING_FACTS_NOTICE = "缺少可核验的基金持仓穿透事实，相关叙述已省略。"
_POSITIVE_RATIONALE_NOTICE = (
    "低或未观察到的披露重合不能证明完整组合更分散，也不能作为买入理由。"
)
_SAFE_STANDARD_NOTICES = {
    _SCOPE_NOTICE,
    _NO_COMMON_NOTICE,
    _CROSS_VINTAGE_NOTICE,
    _IDENTITY_INSUFFICIENT_NOTICE,
    _UNVERIFIED_NUMERIC_NOTICE,
    _MISSING_FACTS_NOTICE,
    _POSITIVE_RATIONALE_NOTICE,
}

_SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？!?\n])|(?<=[.])(?=\s)")
_FUND_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_CONTROL_CHAR_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\u2066-\u2069\ufeff]")
_NUMBER_TOKEN = r"[-+]?\d+(?:\.\d+)?"
_EN_OVERLAP_TERM = (
    r"(?:(?:portfolio|security|stock|holdings?)[\s_-]*)?"
    r"(?:overlap|intersection)"
)

_LOOKTHROUGH_SIGNAL_RE = re.compile(
    r"(?:"
    r"持仓穿透|穿透持仓|重合|重叠|交集|共同持仓|共同证券|完全分散|完全互补|"
    r"前十(?:大)?持仓|十大持仓|top[\s_-]*(?:10|ten)[\s_-]*holdings|"
    r"look[\s_-]*through|\boverlap\b|"
    r"\b(?:portfolio|security|stock|holdings?)[\s_-]*intersection\b|"
    r"(?:fully|perfectly|completely)[\s_-]*diversified|"
    r"common[\s_-]*(?:holdings?|securities)|disclosed[\s_-]*holdings?"
    r")",
    re.IGNORECASE,
)

_CURRENT_OR_COMPLETE_HOLDINGS_RE = re.compile(
    r"(?:"
    r"(?:当前|实时|即时|现时|最新)(?:的)?(?:完整|全部|全量)?持仓|"
    r"(?:完整|全部|全量)(?:的)?持仓|"
    r"(?:完整|全部|全量)(?:的)?(?:组合|全组合)(?:持仓|敞口|重合)?|"
    r"精确(?:的)?(?:全组合|完整组合|组合)(?:持仓|敞口|重合)?|"
    r"(?:current|real[\s_-]*time|realtime|live|up[\s_-]*to[\s_-]*date)"
    r"[\s_-]*(?:complete[\s_-]*|full[\s_-]*)?holdings?|"
    r"(?:complete|full|entire)[\s_-]*(?:current[\s_-]*)?holdings?|"
    r"(?:complete|full|entire)[\s_-]*portfolio(?:[\s_-]*(?:holdings?|overlap))?|"
    r"exact[\s_-]*(?:full|entire|complete)?[\s_-]*portfolio"
    r")",
    re.IGNORECASE,
)
_DISCLOSURE_CONTEXT_RE = re.compile(
    r"(?:季报|年报|定期报告|前十(?:大)?持仓|十大持仓|披露|报告截止日|"
    r"quarterly|annual[\s_-]*report|filing|top[\s_-]*(?:10|ten)|disclos|look[\s_-]*through)",
    re.IGNORECASE,
)
_NEGATED_SCOPE_RE = re.compile(
    r"(?:不是|并非|不代表|不能视为|无法代表|未知|"
    r"not|does[\s_-]*not|do[\s_-]*not|cannot|can['’]?t|unknown)",
    re.IGNORECASE,
)

_ZERO_OR_ABSOLUTE_DIVERSIFICATION_RE = re.compile(
    r"(?:"
    r"(?:重合|重叠|交集)(?:率|比例|度)?(?:为|是|=|：|:)?\s*0(?:\.0+)?\s*%|"
    r"0(?:\.0+)?\s*%\s*(?:的)?(?:组合|证券|股票|持仓)?(?:重合|重叠|交集)|"
    rf"0(?:\.0+)?\s*%\s*{_EN_OVERLAP_TERM}|"
    r"零(?:重合|重叠|交集)|完全(?:不|无)(?:重合|重叠|交集)|毫无(?:重合|重叠|交集)|"
    r"(?:没有任何|未发现)共同(?:持仓|证券)|"
    r"完全分散|完全互补|"
    rf"(?:zero|no|empty)[\s_-]*{_EN_OVERLAP_TERM}|completely[\s_-]*disjoint|"
    r"(?:fully|perfectly|completely)[\s_-]*diversified|"
    r"no[\s_-]*common[\s_-]*(?:holdings?|securities)|no[\s_-]*shared[\s_-]*holdings?"
    r")",
    re.IGNORECASE,
)
_OVERLAP_CONTEXT_RE = re.compile(
    r"(?:重合|重叠|交集|共同(?:证券|持仓)|overlap|"
    r"(?:portfolio|security|stock|holdings?)[\s_-]*intersection|"
    r"common[\s_-]*(?:holdings?|securities))",
    re.IGNORECASE,
)
_LOW_OVERLAP_RE = re.compile(
    r"(?:低(?:重合|重叠|交集)|(?:重合|重叠|交集)(?:率|比例|度)?(?:较低|很低|偏低)|"
    r"少(?:重合|重叠|交集)|无(?:重合|重叠|交集)|不(?:重合|重叠|交集)|"
    rf"(?:low|minimal|little|no)[\s_-]*{_EN_OVERLAP_TERM})",
    re.IGNORECASE,
)
_POSITIVE_ALLOCATION_RE = re.compile(
    r"(?:买入|申购|加仓|值得配置|优先配置|建议配置|推荐配置|更值得|"
    r"\bbuy\b|\bpurchase\b|\ballocate\b|\brecommend(?:ed|ation)?\b|"
    r"add[\s_-]*(?:to[\s_-]*)?(?:the[\s_-]*)?position)",
    re.IGNORECASE,
)
_DIVERSIFICATION_BENEFIT_RE = re.compile(
    r"(?:更分散|分散化|分散风险|改善分散|降低集中度|稀释风险|组合互补|"
    r"more[\s_-]*diversified|diversification|diversif(?:y|ies)|"
    r"reduce[\s_-]*concentration|lower[\s_-]*concentration|complementary)",
    re.IGNORECASE,
)
_NEGATED_POSITIVE_RATIONALE_RE = re.compile(
    r"(?:不能|不可|不应|并非|不是|无法).{0,16}(?:买入|申购|加仓|配置|分散)|"
    r"(?:not|cannot|can['’]?t|should[\s_-]*not).{0,24}(?:buy|purchase|allocate|diversif)",
    re.IGNORECASE,
)
_HIGH_OVERLAP_RE = re.compile(
    r"(?:高(?:重合|重叠|交集)|(?:重合|重叠|交集)(?:率|比例|度)?(?:较高|很高|偏高)|"
    rf"(?:high|substantial)[\s_-]*{_EN_OVERLAP_TERM})",
    re.IGNORECASE,
)

_DISCLOSURE_QUALIFIER_RE = re.compile(r"(?:披露|disclos(?:ed|ure))", re.IGNORECASE)
_REPORT_DATE_QUALIFIER_RE = re.compile(
    r"(?:报告期|报告截止日|截至|季报|年报|定期报告|"
    r"as[\s_-]*of|report(?:ing)?[\s_-]*(?:date|period)|quarter|filing)",
    re.IGNORECASE,
)
_LOWER_BOUND_QUALIFIER_RE = re.compile(
    r"(?:下限|不低于|至少|lower[\s_-]*bound|floor|at[\s_-]*least)",
    re.IGNORECASE,
)

_CN_OVERLAP_PERCENT_RE = re.compile(
    rf"(?:证券|组合|股票|持仓)?(?:重合|重叠|交集)(?:率|比例|度|下限)?"
    rf"(?:为|是|达到|达|约为|约|=|：|:)?\s*(?:≥|>=|不低于|至少)?\s*"
    rf"(?P<value>{_NUMBER_TOKEN})\s*%",
    re.IGNORECASE,
)
_EN_OVERLAP_PERCENT_RE = re.compile(
    rf"{_EN_OVERLAP_TERM}"
    rf"(?:[\s_-]*(?:lower[\s_-]*bound|floor|rate|ratio))?"
    rf"(?:[\s_-]*(?:is|of|=|about|approximately|at[\s_-]*least))?\s*"
    rf"(?P<value>{_NUMBER_TOKEN})\s*%",
    re.IGNORECASE,
)
_REVERSED_EN_OVERLAP_PERCENT_RE = re.compile(
    rf"(?P<value>{_NUMBER_TOKEN})\s*%[\s_-]*"
    rf"{_EN_OVERLAP_TERM}(?:[\s_-]*(?:lower[\s_-]*bound|floor))?",
    re.IGNORECASE,
)

_NO_COMMON_INTERPRETATIONS = {"no_common", "no_common_in_disclosed_scope"}
_CROSS_VINTAGE_INTERPRETATIONS = {
    "cross_vintage_disclosed_similarity",
    "cross_vintage_descriptive_similarity",
    "cross_vintage_no_common_in_disclosed_scope",
    "cross_vintage_identity_evidence_insufficient",
}
_CROSS_VINTAGE_STATUSES = {"cross_vintage", "mixed"}
_IDENTITY_INSUFFICIENT_INTERPRETATIONS = {
    "identity_evidence_insufficient",
    "snapshot_not_eligible",
}
_LOW_NUMERIC_OVERLAP_MAX_PERCENT = Decimal("5")
_HIGH_QUALITATIVE_OVERLAP_MIN_PERCENT = Decimal("20")


@dataclass(frozen=True)
class _Decision:
    reason: str
    replacement: str


@dataclass(frozen=True)
class _Binding:
    candidate: Mapping[str, Any] | None
    fund_code: str | None
    reason: str | None


def validate_fund_lookthrough_claims(
    report: Mapping[str, Any],
    fund_lookthrough: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a sanitized report copy and a redacted, deterministic claim audit.

    Only explicitly enumerated user-visible narrative fields are visited. Fund
    codes, names, actions, numeric amounts, dates, returns, and all unlisted
    structured fields are left untouched.
    """

    if not isinstance(report, Mapping):
        raise TypeError("report must be a mapping")

    cleaned = deepcopy(dict(report))
    facts = dict(fund_lookthrough) if isinstance(fund_lookthrough, Mapping) else {}
    candidates = _candidate_facts(facts)
    facts_available = _facts_available(facts, candidates)
    changes: list[dict[str, Any]] = []
    reason_counts: dict[str, int] = {}
    scanned_field_count = 0
    lookthrough_field_count = 0

    def sanitize_value(
        value: str,
        *,
        path: str,
        context_code: str | None,
    ) -> str:
        nonlocal scanned_field_count, lookthrough_field_count
        scanned_field_count += 1
        if _has_lookthrough_signal(_detection_text(value)):
            lookthrough_field_count += 1
        replacement, decisions = _sanitize_text(
            value,
            context_code=context_code,
            candidates=candidates,
            facts_available=facts_available,
        )
        if replacement == value or not decisions:
            return value
        original_hash = _text_hash(value)
        seen: set[tuple[str, str]] = set()
        for decision in decisions:
            key = (decision.reason, decision.replacement)
            if key in seen:
                continue
            seen.add(key)
            reason_counts[decision.reason] = reason_counts.get(decision.reason, 0) + 1
            changes.append(
                {
                    "path": path,
                    "original_hash": original_hash,
                    "reason": decision.reason,
                    "replacement": decision.replacement,
                }
            )
        return replacement

    for key in _TOP_LEVEL_NARRATIVE_FIELDS:
        if isinstance(cleaned.get(key), str):
            cleaned[key] = sanitize_value(
                cleaned[key], path=f"$.{key}", context_code=None
            )

    _sanitize_string_list(
        cleaned,
        "caveats",
        "$.caveats",
        context_code=None,
        sanitizer=sanitize_value,
    )

    recommendations = cleaned.get("recommendations")
    if isinstance(recommendations, list):
        for index, item in enumerate(recommendations):
            path = f"$.recommendations[{index}]"
            if isinstance(item, str):
                recommendations[index] = sanitize_value(
                    item, path=path, context_code=None
                )
            elif isinstance(item, Mapping):
                if not isinstance(item, dict):
                    item = deepcopy(dict(item))
                    recommendations[index] = item
                _sanitize_recommendation(
                    item,
                    path=path,
                    sanitizer=sanitize_value,
                )

    fund_recommendations = cleaned.get("fund_recommendations")
    if isinstance(fund_recommendations, list):
        for index, item in enumerate(fund_recommendations):
            if not isinstance(item, Mapping):
                continue
            if not isinstance(item, dict):
                item = deepcopy(dict(item))
                fund_recommendations[index] = item
            _sanitize_recommendation(
                item,
                path=f"$.fund_recommendations[{index}]",
                sanitizer=sanitize_value,
            )

    audit: dict[str, Any] = {
        "schema_version": CLAIM_AUDIT_SCHEMA_VERSION,
        "status": "sanitized" if changes else "clean",
        "facts_status": "available" if facts_available else "unavailable",
        "scanned_field_count": scanned_field_count,
        "lookthrough_field_count": lookthrough_field_count,
        "changed_field_count": len({item["path"] for item in changes}),
        "change_count": len(changes),
        "reason_counts": dict(sorted(reason_counts.items())),
        "changes": changes,
        "hash_algorithm": "sha256",
    }
    audit["audit_hash"] = _json_hash(audit)
    return cleaned, audit


def _sanitize_recommendation(
    recommendation: dict[str, Any],
    *,
    path: str,
    sanitizer: Any,
) -> None:
    context_code = _normalize_fund_code(recommendation.get("fund_code"))
    for key in _RECOMMENDATION_SCALAR_FIELDS:
        value = recommendation.get(key)
        if isinstance(value, str):
            recommendation[key] = sanitizer(
                value,
                path=f"{path}.{key}",
                context_code=context_code,
            )
    for key in _RECOMMENDATION_LIST_FIELDS:
        _sanitize_string_list(
            recommendation,
            key,
            f"{path}.{key}",
            context_code=context_code,
            sanitizer=sanitizer,
        )


def _sanitize_string_list(
    container: dict[str, Any],
    key: str,
    path: str,
    *,
    context_code: str | None,
    sanitizer: Any,
) -> None:
    values = container.get(key)
    if not isinstance(values, list):
        return
    for index, value in enumerate(values):
        if isinstance(value, str):
            values[index] = sanitizer(
                value,
                path=f"{path}[{index}]",
                context_code=context_code,
            )


def _sanitize_text(
    text: str,
    *,
    context_code: str | None,
    candidates: Mapping[str, Mapping[str, Any]],
    facts_available: bool,
) -> tuple[str, list[_Decision]]:
    parts = _SENTENCE_BOUNDARY.split(text)
    output: list[str] = []
    decisions: list[_Decision] = []
    changed = False
    for part in parts:
        if not part:
            continue
        stripped = part.strip()
        if not stripped:
            output.append(part)
            continue
        decision = _evaluate_segment(
            stripped,
            context_code=context_code,
            candidates=candidates,
            facts_available=facts_available,
        )
        if decision is None:
            output.append(part)
            continue
        changed = True
        decisions.append(decision)
        leading = part[: len(part) - len(part.lstrip())]
        trailing = part[len(part.rstrip()) :]
        output.append(f"{leading}{decision.replacement}{trailing}")
    if not changed:
        return text, []
    return "".join(output).strip(), decisions


def _evaluate_segment(
    segment: str,
    *,
    context_code: str | None,
    candidates: Mapping[str, Mapping[str, Any]],
    facts_available: bool,
) -> _Decision | None:
    if segment.strip() in _SAFE_STANDARD_NOTICES:
        return None
    detected = _detection_text(segment)
    if not _has_lookthrough_signal(detected):
        return None

    binding = _bind_candidate(detected, context_code=context_code, candidates=candidates)
    interpretation = _candidate_interpretation(binding.candidate)
    cross_vintage = _candidate_is_cross_vintage(binding.candidate)
    numeric_claims = _overlap_percent_claims(detected)
    absolute_claim = bool(_ZERO_OR_ABSOLUTE_DIVERSIFICATION_RE.search(detected))
    positive_rationale = _is_positive_overlap_rationale(
        detected,
        numeric_claims=numeric_claims,
        interpretation=interpretation,
        cross_vintage=cross_vintage,
    )

    # ``vintage_alignment.status`` is the authoritative C2 signal.  A
    # cross/mixed-vintage comparison is descriptive only, regardless of which
    # interpretation string accompanies it, so it must never be promoted to a
    # current zero, qualitative, or numeric overlap claim.
    if cross_vintage and (
        absolute_claim
        or bool(numeric_claims)
        or bool(_LOW_OVERLAP_RE.search(detected))
        or bool(_HIGH_OVERLAP_RE.search(detected))
        or positive_rationale
    ):
        return _Decision(
            reason=(
                "cross_vintage_promoted_to_current_or_zero_overlap"
                if absolute_claim
                else "cross_vintage_numeric_overlap_claim"
            ),
            replacement=_CROSS_VINTAGE_NOTICE,
        )

    if positive_rationale:
        return _Decision(
            reason="overlap_used_as_positive_allocation_rationale",
            replacement=_POSITIVE_RATIONALE_NOTICE,
        )

    if absolute_claim:
        return _Decision(
            reason=_absolute_overlap_reason(
                interpretation,
                cross_vintage=cross_vintage,
            ),
            replacement=_semantic_notice(
                interpretation,
                cross_vintage=cross_vintage,
            ),
        )

    if (
        _CURRENT_OR_COMPLETE_HOLDINGS_RE.search(detected)
        and _DISCLOSURE_CONTEXT_RE.search(detected)
        and not _NEGATED_SCOPE_RE.search(detected)
    ):
        return _Decision(
            reason="unsupported_current_or_complete_holdings_claim",
            replacement=_SCOPE_NOTICE,
        )

    if not facts_available:
        return _Decision(
            reason="lookthrough_facts_unavailable",
            replacement=_MISSING_FACTS_NOTICE,
        )

    if numeric_claims:
        authorization_reason, fact_value = _authorized_overlap_fact(binding)
        if authorization_reason is not None or fact_value is None:
            return _Decision(
                reason=authorization_reason or "candidate_overlap_lower_bound_unavailable",
                replacement=_numeric_failure_notice(
                    interpretation,
                    cross_vintage=cross_vintage,
                ),
            )
        if not all(_formatted_claim_matches(value, fact_value) for value in numeric_claims):
            return _Decision(
                reason="candidate_overlap_value_mismatch",
                replacement=_UNVERIFIED_NUMERIC_NOTICE,
            )
        if not _has_required_overlap_qualifiers(detected):
            return _Decision(
                reason="positive_overlap_claim_missing_scope_qualifiers",
                replacement=_canonical_overlap_claim(
                    binding.fund_code,
                    binding.candidate,
                    fact_value,
                ),
            )
        return None

    if _HIGH_OVERLAP_RE.search(detected):
        authorization_reason, fact_value = _authorized_overlap_fact(binding)
        if authorization_reason is not None or fact_value is None:
            return _Decision(
                reason=authorization_reason or "candidate_overlap_lower_bound_unavailable",
                replacement=_numeric_failure_notice(
                    interpretation,
                    cross_vintage=cross_vintage,
                ),
            )
        if fact_value < _HIGH_QUALITATIVE_OVERLAP_MIN_PERCENT:
            return _Decision(
                reason="qualitative_high_overlap_not_supported",
                replacement=_UNVERIFIED_NUMERIC_NOTICE,
            )
        if not _has_required_overlap_qualifiers(detected):
            return _Decision(
                reason="positive_overlap_claim_missing_scope_qualifiers",
                replacement=_canonical_overlap_claim(
                    binding.fund_code,
                    binding.candidate,
                    fact_value,
                ),
            )
    return None


def _candidate_facts(
    facts: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    raw = facts.get("candidates")
    result: dict[str, Mapping[str, Any]] = {}
    if isinstance(raw, Mapping):
        rows: Sequence[tuple[Any, Any]] = list(raw.items())
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        rows = [(None, item) for item in raw]
    else:
        rows = []
    for key, item in rows:
        if not isinstance(item, Mapping):
            continue
        code = _normalize_fund_code(item.get("fund_code")) or _normalize_fund_code(key)
        if not code:
            continue
        result.setdefault(code, item)
    return result


def _facts_available(
    facts: Mapping[str, Any],
    candidates: Mapping[str, Mapping[str, Any]],
) -> bool:
    return bool(
        candidates
        or (
            isinstance(facts.get("portfolio"), Mapping)
            and bool(facts.get("portfolio"))
        )
        or (
            isinstance(facts.get("existing_funds"), Sequence)
            and not isinstance(facts.get("existing_funds"), (str, bytes, bytearray))
            and bool(facts.get("existing_funds"))
        )
    )


def _bind_candidate(
    text: str,
    *,
    context_code: str | None,
    candidates: Mapping[str, Mapping[str, Any]],
) -> _Binding:
    mentioned = {
        code
        for code in _FUND_CODE_RE.findall(text)
        if code in candidates
    }
    if context_code:
        if mentioned and mentioned != {context_code}:
            return _Binding(None, context_code, "overlap_candidate_mismatch")
        candidate = candidates.get(context_code)
        if candidate is None:
            return _Binding(None, context_code, "overlap_candidate_not_in_facts")
        return _Binding(candidate, context_code, None)
    if len(mentioned) == 1:
        code = next(iter(mentioned))
        return _Binding(candidates[code], code, None)
    if len(mentioned) > 1:
        return _Binding(None, None, "overlap_candidate_ambiguous")
    if len(candidates) == 1:
        code, candidate = next(iter(candidates.items()))
        return _Binding(candidate, code, None)
    return _Binding(None, None, "overlap_candidate_unbound")


def _authorized_overlap_fact(
    binding: _Binding,
) -> tuple[str | None, Decimal | None]:
    if binding.reason is not None:
        return binding.reason, None
    candidate = binding.candidate
    if candidate is None:
        return "overlap_candidate_unbound", None
    interpretation = _candidate_interpretation(candidate)
    if _candidate_is_cross_vintage(candidate):
        return "cross_vintage_numeric_overlap_claim", None
    if interpretation in _NO_COMMON_INTERPRETATIONS:
        return "no_common_numeric_overlap_claim", None
    if interpretation in _IDENTITY_INSUFFICIENT_INTERPRETATIONS:
        return "identity_insufficient_numeric_overlap_claim", None

    capabilities = candidate.get("capabilities")
    nested_eligible = (
        capabilities.get("concentration_risk_guard_eligible") is True
        if isinstance(capabilities, Mapping)
        else False
    )
    eligible = bool(
        nested_eligible
        or candidate.get("concentration_risk_guard_eligible") is True
        or candidate.get("risk_guard_eligible") is True
    )
    if not eligible:
        return "candidate_risk_guard_not_eligible", None

    alignment = candidate.get("vintage_alignment")
    same_as_of_date = (
        alignment.get("status") == "same_as_of_date"
        if isinstance(alignment, Mapping)
        else False
    )
    if not (same_as_of_date or candidate.get("vintage_aligned") is True):
        return "candidate_vintage_not_aligned", None

    value = _decimal_percent(
        candidate.get("portfolio_security_overlap_lower_bound_percent")
    )
    if value is None or value <= 0:
        return "candidate_overlap_lower_bound_unavailable", None
    return None, value


def _candidate_interpretation(candidate: Mapping[str, Any] | None) -> str:
    if not isinstance(candidate, Mapping):
        return ""
    return str(candidate.get("portfolio_overlap_interpretation") or "").strip()


def _candidate_vintage_status(candidate: Mapping[str, Any] | None) -> str:
    if not isinstance(candidate, Mapping):
        return ""
    alignment = candidate.get("vintage_alignment")
    if isinstance(alignment, Mapping):
        return str(alignment.get("status") or "").strip()
    if candidate.get("vintage_aligned") is True:
        return "same_as_of_date"
    return ""


def _candidate_is_cross_vintage(candidate: Mapping[str, Any] | None) -> bool:
    status = _candidate_vintage_status(candidate)
    if status in _CROSS_VINTAGE_STATUSES:
        return True
    # Retain safe compatibility with legacy facts that predate the structured
    # alignment object.  A contradictory legacy interpretation is also failed
    # closed instead of authorizing a numeric claim.
    return _candidate_interpretation(candidate) in _CROSS_VINTAGE_INTERPRETATIONS


def _absolute_overlap_reason(
    interpretation: str,
    *,
    cross_vintage: bool = False,
) -> str:
    if cross_vintage or interpretation in _CROSS_VINTAGE_INTERPRETATIONS:
        return "cross_vintage_promoted_to_current_or_zero_overlap"
    if interpretation in _IDENTITY_INSUFFICIENT_INTERPRETATIONS:
        return "identity_insufficient_promoted_to_zero_overlap"
    if interpretation in _NO_COMMON_INTERPRETATIONS:
        return "disclosed_no_common_promoted_to_zero_overlap"
    return "unsupported_zero_or_complete_diversification_claim"


def _semantic_notice(
    interpretation: str,
    *,
    cross_vintage: bool = False,
) -> str:
    if cross_vintage or interpretation in _CROSS_VINTAGE_INTERPRETATIONS:
        return _CROSS_VINTAGE_NOTICE
    if interpretation in _IDENTITY_INSUFFICIENT_INTERPRETATIONS:
        return _IDENTITY_INSUFFICIENT_NOTICE
    if interpretation in _NO_COMMON_INTERPRETATIONS:
        return _NO_COMMON_NOTICE
    return _UNVERIFIED_NUMERIC_NOTICE


def _numeric_failure_notice(
    interpretation: str,
    *,
    cross_vintage: bool = False,
) -> str:
    if cross_vintage or interpretation in _CROSS_VINTAGE_INTERPRETATIONS:
        return _CROSS_VINTAGE_NOTICE
    if interpretation in _IDENTITY_INSUFFICIENT_INTERPRETATIONS:
        return _IDENTITY_INSUFFICIENT_NOTICE
    if interpretation in _NO_COMMON_INTERPRETATIONS:
        return _NO_COMMON_NOTICE
    return _UNVERIFIED_NUMERIC_NOTICE


def _is_positive_overlap_rationale(
    text: str,
    *,
    numeric_claims: Sequence[Decimal],
    interpretation: str,
    cross_vintage: bool = False,
) -> bool:
    if _NEGATED_POSITIVE_RATIONALE_RE.search(text):
        return False
    if not _OVERLAP_CONTEXT_RE.search(text):
        return False
    has_positive_conclusion = bool(
        _POSITIVE_ALLOCATION_RE.search(text)
        or _DIVERSIFICATION_BENEFIT_RE.search(text)
    )
    if not has_positive_conclusion:
        return False
    if cross_vintage or interpretation in (
        _NO_COMMON_INTERPRETATIONS
        | _CROSS_VINTAGE_INTERPRETATIONS
        | _IDENTITY_INSUFFICIENT_INTERPRETATIONS
    ):
        return True
    if _LOW_OVERLAP_RE.search(text) or _ZERO_OR_ABSOLUTE_DIVERSIFICATION_RE.search(text):
        return True
    return any(value <= _LOW_NUMERIC_OVERLAP_MAX_PERCENT for value in numeric_claims)


def _overlap_percent_claims(text: str) -> list[Decimal]:
    values: list[Decimal] = []
    seen_spans: set[tuple[int, int]] = set()
    for pattern in (
        _CN_OVERLAP_PERCENT_RE,
        _EN_OVERLAP_PERCENT_RE,
        _REVERSED_EN_OVERLAP_PERCENT_RE,
    ):
        for match in pattern.finditer(text):
            if match.span() in seen_spans:
                continue
            seen_spans.add(match.span())
            try:
                value = Decimal(match.group("value"))
            except (InvalidOperation, ValueError):
                continue
            if value.is_finite():
                values.append(value)
    return values


def _formatted_claim_matches(claimed: Decimal, fact: Decimal) -> bool:
    if not claimed.is_finite() or not fact.is_finite():
        return False
    decimals = max(0, -claimed.as_tuple().exponent)
    quantum = Decimal(1).scaleb(-decimals)
    try:
        formatted_fact = fact.quantize(quantum, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return False
    return claimed == formatted_fact


def _has_required_overlap_qualifiers(text: str) -> bool:
    return bool(
        _DISCLOSURE_QUALIFIER_RE.search(text)
        and _REPORT_DATE_QUALIFIER_RE.search(text)
        and _LOWER_BOUND_QUALIFIER_RE.search(text)
    )


def _canonical_overlap_claim(
    fund_code: str | None,
    candidate: Mapping[str, Any] | None,
    value: Decimal,
) -> str:
    report_date = _candidate_report_date(candidate)
    date_label = f"截至 {report_date} 报告截止日" if report_date else "截至对应报告截止日"
    fund_label = f"基金 {fund_code} " if fund_code else "该候选"
    return (
        f"{date_label}的披露范围内，{fund_label}与当前组合的证券重合下限为"
        f" {_format_decimal(value)}%；仅用于集中度风险研究。"
    )


def _candidate_report_date(candidate: Mapping[str, Any] | None) -> str | None:
    if not isinstance(candidate, Mapping):
        return None
    alignment = candidate.get("vintage_alignment")
    snapshot = candidate.get("snapshot")
    for container, keys in (
        (
            alignment,
            (
                "as_of_date",
                "report_period",
                "candidate_as_of_date",
                "portfolio_as_of_date",
            ),
        ),
        (snapshot, ("as_of_date", "report_period")),
        (candidate, ("as_of_date", "report_period")),
    ):
        if not isinstance(container, Mapping):
            continue
        for key in keys:
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _decimal_percent(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not parsed.is_finite() or parsed < 0 or parsed > 100:
        return None
    return parsed


def _format_decimal(value: Decimal) -> str:
    normalized = format(value.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


def _normalize_fund_code(value: Any) -> str | None:
    text = str(value or "").strip()
    if text.isdigit() and 1 <= len(text) <= 6:
        return text.zfill(6)
    return text if text else None


def _detection_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = _CONTROL_CHAR_RE.sub("", normalized)
    normalized = "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Cf"
    )
    return re.sub(r"[\t\r ]+", " ", normalized)


def _has_lookthrough_signal(text: str) -> bool:
    return bool(_LOOKTHROUGH_SIGNAL_RE.search(text))


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_hash(value: Mapping[str, Any]) -> str:
    material = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


__all__ = [
    "CLAIM_AUDIT_SCHEMA_VERSION",
    "validate_fund_lookthrough_claims",
]
