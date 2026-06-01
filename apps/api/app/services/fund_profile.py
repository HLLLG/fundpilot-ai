from __future__ import annotations

import hashlib
import re

from app.database import (
    delete_fund_profile,
    get_fund_profile_by_code,
    list_fund_profiles,
    save_fund_profile,
)
from app.models import FundProfile, Holding, ProfileSyncResult


CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
NUMBER_RE = re.compile(r"^[+-]?\d[\d,]*(?:\.\d+)?$")
PERCENT_RE = re.compile(r"^([+-]?\d+(?:\.\d+)?)%$")
SECTOR_RE = re.compile(r"^(.+?)[▼▲]([+-]?\d+(?:\.\d+)?)%$")


class FundProfileService:
    def save_profile(self, profile: FundProfile) -> FundProfile:
        existing = self.find_match(profile.fund_name)
        if (
            existing is not None
            and existing.is_provisional
            and existing.fund_code != profile.fund_code
        ):
            delete_fund_profile(existing.fund_code)
        if profile.source == "yangjibao-detail":
            profile = profile.model_copy(update={"is_provisional": False})
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

    def sync_profiles_from_holdings(self, holdings: list[Holding]) -> ProfileSyncResult:
        if not holdings:
            return ProfileSyncResult()

        total_amount = sum(holding.holding_amount for holding in holdings)
        updated = 0
        created = 0

        for holding in holdings:
            profile = self._find_profile_for_holding(holding)
            if profile is None:
                if holding.fund_code == "000000":
                    profile = _holding_to_provisional_profile(holding)
                else:
                    profile = _holding_to_provisional_profile(
                        holding,
                        fund_code=holding.fund_code,
                        is_provisional=False,
                    )
                save_fund_profile(profile)
                created += 1
                continue

            merged = merge_holding_into_profile(
                profile,
                holding,
                total_amount=total_amount if total_amount > 0 else None,
            )
            save_fund_profile(merged)
            updated += 1

        return ProfileSyncResult(updated=updated, created=created)

    def _find_profile_for_holding(self, holding: Holding) -> FundProfile | None:
        if holding.fund_code != "000000":
            by_code = get_fund_profile_by_code(holding.fund_code)
            if by_code is not None:
                return by_code
        return self.find_match(holding.fund_name)


def merge_holding_into_profile(
    profile: FundProfile,
    holding: Holding,
    *,
    total_amount: float | None = None,
) -> FundProfile:
    updates: dict = {
        "fund_name": profile.fund_name if not profile.is_provisional else holding.fund_name,
        "holding_amount": holding.holding_amount,
    }
    if holding.holding_profit is not None:
        updates["holding_profit"] = holding.holding_profit
    if holding.holding_return_percent is not None:
        updates["holding_return_percent"] = holding.holding_return_percent
    elif holding.return_percent:
        updates["holding_return_percent"] = holding.return_percent
    if holding.daily_profit is not None:
        updates["daily_profit"] = holding.daily_profit
    if holding.sector_name:
        updates["sector_name"] = holding.sector_name
    if holding.sector_return_percent is not None:
        updates["sector_return_percent"] = holding.sector_return_percent
    if total_amount and holding.holding_amount > 0:
        updates["position_percent"] = round(holding.holding_amount / total_amount * 100, 2)
    return profile.model_copy(update=updates)


def _holding_to_provisional_profile(
    holding: Holding,
    *,
    fund_code: str | None = None,
    is_provisional: bool = True,
) -> FundProfile:
    code = fund_code or provisional_code_for_name(holding.fund_name)
    return FundProfile(
        fund_code=code,
        fund_name=holding.fund_name,
        aliases=_aliases_for_name(holding.fund_name),
        holding_amount=holding.holding_amount,
        holding_profit=holding.holding_profit,
        holding_return_percent=holding.holding_return_percent or holding.return_percent or None,
        daily_profit=holding.daily_profit,
        sector_name=holding.sector_name,
        sector_return_percent=holding.sector_return_percent,
        source="yangjibao-overview",
        is_provisional=is_provisional,
    )


def provisional_code_for_name(fund_name: str) -> str:
    digest = hashlib.sha256(_normalize_name(fund_name).encode("utf-8")).hexdigest()
    return f"9{int(digest[:8], 16) % 100000:05d}"


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
