from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.services.sector_canonical import fetch_canonical_sector_quote
from app.services.sector_labels import build_sector_candidates, normalize_sector_label, sector_label_key
from app.services.sector_quote_provider import SpotBoard

SectorSourceType = str  # index | concept | industry


@dataclass
class SectorMappingCandidate:
    source_type: SectorSourceType
    source_name: str
    change_percent: float
    source_code: str | None = None


@dataclass
class SectorResolveResult:
    confidence: str  # high | medium | low | none
    change_percent: float | None = None
    matched_name: str | None = None
    source_type: SectorSourceType | None = None
    source_code: str | None = None
    candidates: list[SectorMappingCandidate] = field(default_factory=list)
    message: str | None = None


def resolve_sector_quote(
    sector_name: str | None,
    boards: dict[str, SpotBoard],
    *,
    persisted_mapping: dict | None = None,
    quote_label: str | None = None,
) -> SectorResolveResult:
    lookup_label = normalize_sector_label(quote_label or sector_name)
    display_label = normalize_sector_label(sector_name)
    if not lookup_label:
        return SectorResolveResult(confidence="none", message="未识别关联板块名称")
    label = lookup_label

    if persisted_mapping:
        source_type = str(persisted_mapping.get("source_type", ""))
        source_name = str(persisted_mapping.get("source_name", ""))
        board = boards.get(source_type) or {}
        if source_name in board:
            return SectorResolveResult(
                confidence="high",
                change_percent=board[source_name],
                matched_name=source_name,
                source_type=source_type,
                source_code=persisted_mapping.get("source_code"),
            )

    canonical = fetch_canonical_sector_quote(label, boards)
    if canonical is not None:
        return SectorResolveResult(
            confidence="high",
            change_percent=canonical.change_percent,
            matched_name=canonical.matched_name,
            source_type=canonical.source_type,
            source_code=canonical.source_code,
            message=canonical.message,
        )

    exact_matches: list[SectorMappingCandidate] = []
    fuzzy_matches: list[SectorMappingCandidate] = []

    for source_type, board in boards.items():
        for candidate_label in build_sector_candidates(label):
            if candidate_label in board:
                exact_matches.append(
                    SectorMappingCandidate(
                        source_type=source_type,
                        source_name=candidate_label,
                        change_percent=board[candidate_label],
                    )
                )
            for spot_name, change in board.items():
                if not _fuzzy_sector_match(candidate_label, spot_name):
                    continue
                fuzzy_matches.append(
                    SectorMappingCandidate(
                        source_type=source_type,
                        source_name=spot_name,
                        change_percent=change,
                    )
                )

    unique = _dedupe_candidates(exact_matches + fuzzy_matches)
    if not unique:
        return SectorResolveResult(
            confidence="none",
            message=f"未在东财行情中找到「{label}」",
        )

    auto = _auto_pick_candidate(label, unique, exact_matches, boards)
    if auto is not None:
        return SectorResolveResult(
            confidence="high",
            change_percent=auto.change_percent,
            matched_name=auto.source_name,
            source_type=auto.source_type,
            source_code=auto.source_code,
        )

    if len(unique) == 1:
        item = unique[0]
        confidence = "high" if item in exact_matches else "medium"
        return SectorResolveResult(
            confidence=confidence,
            change_percent=item.change_percent,
            matched_name=item.source_name,
            source_type=item.source_type,
            source_code=item.source_code,
        )

    if len(exact_matches) == 1:
        item = exact_matches[0]
        return SectorResolveResult(
            confidence="high",
            change_percent=item.change_percent,
            matched_name=item.source_name,
            source_type=item.source_type,
            source_code=item.source_code,
        )

    return SectorResolveResult(
        confidence="low",
        candidates=unique[:8],
        message=f"「{label}」有 {len(unique)} 个可能映射，请选择",
    )


def mapping_record_from_result(
    sector_name: str | None,
    result: SectorResolveResult,
) -> dict | None:
    if (
        result.confidence not in {"high", "medium"}
        or result.matched_name is None
        or result.source_type not in {"index", "concept", "industry"}
    ):
        return None
    return {
        "sector_label": sector_label_key(sector_name),
        "source_type": result.source_type,
        "source_code": result.source_code,
        "source_name": result.matched_name,
        "confidence": result.confidence,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _fuzzy_sector_match(candidate_label: str, spot_name: str) -> bool:
    """收紧模糊匹配，避免「商业航天」命中「航天装备」等误匹配。"""
    if candidate_label == spot_name:
        return True
    if len(candidate_label) < 3:
        return False
    if "商业航天" in candidate_label:
        return "商业航天" in spot_name
    if "国防军工" in candidate_label:
        return "国防军工" in spot_name or spot_name == "军工"
    if candidate_label in spot_name:
        return len(spot_name) - len(candidate_label) <= 6
    if spot_name in candidate_label:
        return True
    return False


def _dedupe_candidates(items: list[SectorMappingCandidate]) -> list[SectorMappingCandidate]:
    seen: set[tuple[str, str]] = set()
    unique: list[SectorMappingCandidate] = []
    for item in items:
        key = (item.source_type, item.source_name)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _auto_pick_candidate(
    label: str,
    unique: list[SectorMappingCandidate],
    exact_matches: list[SectorMappingCandidate],
    boards: dict[str, SpotBoard],
) -> SectorMappingCandidate | None:
    """养基宝常见板块名的默认映射，避免多候选时刷新无法更新。"""
    if "人工智能" in label:
        index_ai = [
            candidate
            for candidate in exact_matches
            if candidate.source_type == "index" and candidate.source_name == "人工智能"
        ]
        if index_ai:
            return index_ai[0]
        index_board = boards.get("index") or {}
        if "人工智能" in index_board:
            return SectorMappingCandidate(
                source_type="index",
                source_name="人工智能",
                change_percent=index_board["人工智能"],
            )

    if "电网设备" in label or label == "电网设备":
        concept_board = boards.get("concept") or {}
        is_index_label = label.startswith("中证") or label.startswith("上证") or label.startswith("深证")
        if not is_index_label and label == "电网设备" and "电网设备" in concept_board:
            return SectorMappingCandidate(
                source_type="concept",
                source_name="电网设备",
                change_percent=concept_board["电网设备"],
            )
        index_board = boards.get("index") or {}
        for preferred in ("中证电网设备", "中证全指电网", "电力设备主题", "电网设备"):
            if preferred in index_board:
                return SectorMappingCandidate(
                    source_type="index",
                    source_name=preferred,
                    change_percent=index_board[preferred],
                )
        for spot_name, change in index_board.items():
            if "电力设备" in spot_name and "主题" in spot_name:
                return SectorMappingCandidate(
                    source_type="index",
                    source_name=spot_name,
                    change_percent=change,
                )

    if label == "半导体" or label.endswith("半导体"):
        industry_board = boards.get("industry") or {}
        if "半导体" in industry_board:
            return SectorMappingCandidate(
                source_type="industry",
                source_name="半导体",
                change_percent=industry_board["半导体"],
            )
        industry = [
            candidate
            for candidate in exact_matches
            if candidate.source_type == "industry" and candidate.source_name == "半导体"
        ]
        if industry:
            return industry[0]

    if "商业航天" in label:
        concept_board = boards.get("concept") or {}
        if label in concept_board:
            return SectorMappingCandidate(
                source_type="concept",
                source_name=label,
                change_percent=concept_board[label],
            )
        concept = [
            candidate
            for candidate in exact_matches
            if candidate.source_type == "concept" and candidate.source_name == "商业航天"
        ]
        if concept:
            return concept[0]

    if label.startswith("中证"):
        index_hits = [candidate for candidate in unique if candidate.source_type == "index"]
        for token in build_sector_candidates(label):
            if len(token) < 2:
                continue
            token_hits = [
                candidate
                for candidate in index_hits
                if candidate.source_name == token or token in candidate.source_name
            ]
            if len(token_hits) == 1:
                return token_hits[0]

    return None
