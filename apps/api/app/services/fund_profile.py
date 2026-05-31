from __future__ import annotations

import re

from app.database import list_fund_profiles, save_fund_profile
from app.models import FundProfile, Holding


CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
NUMBER_RE = re.compile(r"^[+-]?\d[\d,]*(?:\.\d+)?$")
PERCENT_RE = re.compile(r"^([+-]?\d+(?:\.\d+)?)%$")
SECTOR_RE = re.compile(r"^(.+?)[▼▲]([+-]?\d+(?:\.\d+)?)%$")


class FundProfileService:
    def save_profile(self, profile: FundProfile) -> FundProfile:
        return save_fund_profile(profile)

    def list_profiles(self) -> list[FundProfile]:
        return list_fund_profiles()

    def resolve_holding(self, holding: Holding) -> Holding:
        if holding.fund_code != "000000":
            return holding

        profile = self.find_match(holding.fund_name)
        if profile is None:
            return holding

        return holding.model_copy(
            update={
                "fund_code": profile.fund_code,
                "fund_name": profile.fund_name,
                "sector_name": holding.sector_name or profile.sector_name,
                "sector_return_percent": holding.sector_return_percent
                if holding.sector_return_percent is not None
                else profile.sector_return_percent,
            }
        )

    def resolve_holdings(self, holdings: list[Holding]) -> list[Holding]:
        return [self.resolve_holding(holding) for holding in holdings]

    def find_match(self, fund_name: str) -> FundProfile | None:
        target = _normalize_name(fund_name)
        if not target:
            return None
        for profile in self.list_profiles():
            candidates = [profile.fund_name, *profile.aliases]
            if any(_is_name_match(target, _normalize_name(candidate)) for candidate in candidates):
                return profile
        return None


def parse_profile_from_text(text: str) -> FundProfile | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    code_index, code = _find_code(lines)
    if code_index is None or code is None:
        return None

    fund_name = _find_name_before_code(lines, code_index)
    if not fund_name:
        return None

    amount_group = _numbers_after_label(lines, "持有金额", 3)
    profit_group = _numbers_after_label(lines, "持有收益", 3)
    daily_group = _numbers_after_label(lines, "当日收益", 3)
    sector_name, sector_return = _find_sector(lines)

    return FundProfile(
        fund_code=code,
        fund_name=fund_name,
        aliases=_aliases_for_name(fund_name),
        holding_amount=amount_group[0] if len(amount_group) > 0 else None,
        holding_shares=amount_group[1] if len(amount_group) > 1 else None,
        position_percent=amount_group[2] if len(amount_group) > 2 else None,
        holding_profit=profit_group[0] if len(profit_group) > 0 else None,
        holding_return_percent=profit_group[1] if len(profit_group) > 1 else None,
        holding_cost=profit_group[2] if len(profit_group) > 2 else None,
        daily_profit=daily_group[0] if len(daily_group) > 0 else None,
        yesterday_profit=daily_group[1] if len(daily_group) > 1 else None,
        holding_days=int(daily_group[2]) if len(daily_group) > 2 and daily_group[2] is not None else None,
        sector_name=sector_name,
        sector_return_percent=sector_return,
    )


def _find_code(lines: list[str]) -> tuple[int | None, str | None]:
    for index, line in enumerate(lines):
        match = CODE_RE.search(line)
        if match:
            return index, match.group(1)
    return None, None


def _find_name_before_code(lines: list[str], code_index: int) -> str | None:
    for index in range(code_index - 1, -1, -1):
        line = lines[index]
        if any("\u4e00" <= char <= "\u9fff" for char in line):
            return line
    return None


def _numbers_after_label(lines: list[str], label: str, count: int) -> list[float]:
    try:
        start = lines.index(label)
    except ValueError:
        return []

    values: list[float] = []
    for line in lines[start + 1 : start + 12]:
        cleaned = line.replace(",", "").strip()
        percent_match = PERCENT_RE.match(cleaned)
        if percent_match:
            values.append(float(percent_match.group(1)))
        elif NUMBER_RE.match(cleaned):
            values.append(float(cleaned))
        if len(values) >= count:
            break
    return values


def _find_sector(lines: list[str]) -> tuple[str | None, float | None]:
    for line in lines:
        match = SECTOR_RE.match(line)
        if match:
            return match.group(1).strip(), float(match.group(2))
    return None, None


def _aliases_for_name(name: str) -> list[str]:
    compact = _normalize_name(name)
    aliases = {name}
    for length in (6, 8, 10):
        if len(compact) >= length:
            aliases.add(compact[:length])
    return sorted(aliases)


def _normalize_name(name: str) -> str:
    return (
        name.replace("...", "")
        .replace(".", "")
        .replace("·", "")
        .replace(" ", "")
        .strip()
    )


def _is_name_match(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return left in right or right in left
