from __future__ import annotations

from dataclasses import dataclass

from app.services.sector_labels import build_sector_candidates, normalize_sector_label
from app.services.sector_registry_data import (
    CANONICAL_SECTORS,
    DISCOVERY_CHIP_LABELS,
    THEME_BOARD_ALIAS,
    THEME_BOARD_INDEX,
    THEME_BOARD_WHITELIST,
)

BoardKind = str  # industry | concept | index


@dataclass(frozen=True)
class SectorQuoteRef:
    eastmoney_secid: str
    source_code: str | None
    source_type: str
    source_name: str


@dataclass(frozen=True)
class SectorRegistryEntry:
    label: str
    aliases: tuple[str, ...] = ()
    market_quote: SectorQuoteRef | None = None
    discovery_quote: SectorQuoteRef | None = None
    board_kind: str = "concept"
    discovery_eligible: bool = False
    theme_board_eligible: bool = False


def _quote_from_tuple(
    label: str,
    secid: str,
    code: str | None,
    kind: str,
    *,
    source_name: str | None = None,
) -> SectorQuoteRef:
    return SectorQuoteRef(
        eastmoney_secid=secid,
        source_code=code,
        source_type=kind,
        source_name=source_name or label,
    )


def _market_quote_for_label(label: str) -> SectorQuoteRef | None:
    if label in THEME_BOARD_INDEX:
        secid, code, kind = THEME_BOARD_INDEX[label]
        return _quote_from_tuple(label, secid, code, kind)
    if label in THEME_BOARD_ALIAS:
        secid, code, kind = THEME_BOARD_ALIAS[label]
        return _quote_from_tuple(label, secid, code, kind)
    if label in CANONICAL_SECTORS:
        secid, code, kind, source_name = CANONICAL_SECTORS[label]
        return _quote_from_tuple(label, secid, code, kind, source_name=source_name)
    return None


def _discovery_quote_for_label(
    label: str,
    market_quote: SectorQuoteRef | None,
) -> SectorQuoteRef | None:
    if label == "军工":
        secid, code, kind, source_name = CANONICAL_SECTORS["国防军工"]
        return _quote_from_tuple("国防军工", secid, code, kind, source_name=source_name)
    if label in CANONICAL_SECTORS:
        secid, code, kind, source_name = CANONICAL_SECTORS[label]
        return _quote_from_tuple(label, secid, code, kind, source_name=source_name)
    return market_quote


def _board_kind_for_entry(
    market_quote: SectorQuoteRef | None,
    discovery_quote: SectorQuoteRef | None,
) -> BoardKind:
    if market_quote is not None:
        return market_quote.source_type
    if discovery_quote is not None:
        return discovery_quote.source_type
    return "concept"


def _build_entries() -> dict[str, SectorRegistryEntry]:
    all_labels = (
        set(THEME_BOARD_WHITELIST)
        | set(CANONICAL_SECTORS)
        | set(DISCOVERY_CHIP_LABELS)
        | {"军工"}
    )
    theme_eligible = set(THEME_BOARD_WHITELIST)
    discovery_eligible = set(DISCOVERY_CHIP_LABELS)

    entries: dict[str, SectorRegistryEntry] = {}
    for label in sorted(all_labels):
        market_quote = _market_quote_for_label(label)
        discovery_quote = _discovery_quote_for_label(label, market_quote)
        entries[label] = SectorRegistryEntry(
            label=label,
            market_quote=market_quote,
            discovery_quote=discovery_quote,
            board_kind=_board_kind_for_entry(market_quote, discovery_quote),
            discovery_eligible=label in discovery_eligible,
            theme_board_eligible=label in theme_eligible,
        )
    return entries


_ENTRIES: dict[str, SectorRegistryEntry] = _build_entries()


def get_sector_entry(label: str | None) -> SectorRegistryEntry | None:
    normalized = normalize_sector_label(label)
    if not normalized:
        return None
    if normalized in _ENTRIES:
        return _ENTRIES[normalized]
    for candidate in build_sector_candidates(label):
        if candidate in _ENTRIES:
            return _ENTRIES[candidate]
    return None


def list_discovery_sector_labels() -> list[str]:
    return [
        label
        for label in DISCOVERY_CHIP_LABELS
        if _ENTRIES.get(label) is not None and _ENTRIES[label].discovery_eligible
    ]


def list_theme_board_labels() -> list[str]:
    return [
        label
        for label in THEME_BOARD_WHITELIST
        if _ENTRIES.get(label) is not None and _ENTRIES[label].theme_board_eligible
    ]


def resolve_market_quote(label: str | None) -> SectorQuoteRef | None:
    entry = get_sector_entry(label)
    if entry is None:
        return None
    return entry.market_quote


def resolve_discovery_quote(label: str | None) -> SectorQuoteRef | None:
    entry = get_sector_entry(label)
    if entry is None:
        return None
    return entry.discovery_quote
