from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta

from app.database import (
    delete_fund_profile,
    get_fund_profile_by_code,
    list_fund_profiles,
    save_fund_profile,
)
from app.models import FundProfile, Holding, ProfileSyncResult


from app.services.fund_name_utils import is_fund_name_match, normalize_fund_name


# 关联板块判定时需要排除的养基宝详情页 Tab 标签（被 _looks_like_board_label 复用）
_DETAIL_TAB_LABELS = frozenset({"关联板块", "业绩走势", "我的收益"})


class FundProfileService:
    def __init__(self) -> None:
        self._profiles_cache: list[FundProfile] | None = None

    def _invalidate_profiles_cache(self) -> None:
        self._profiles_cache = None

    def save_profile(self, profile: FundProfile) -> FundProfile:
        existing = self.find_match(profile.fund_name)
        if (
            existing is not None
            and existing.is_provisional
            and existing.fund_code != profile.fund_code
        ):
            delete_fund_profile(existing.fund_code)
        by_code = get_fund_profile_by_code(profile.fund_code)
        if by_code is not None and (existing is None or by_code.fund_code == profile.fund_code):
            existing = by_code
        elif existing is not None and existing.fund_code != profile.fund_code:
            existing = None
        reconciled_first_seen = reconcile_first_seen_date(existing, profile)
        if existing is not None:
            profile = profile.model_copy(
                update={
                    "aliases": sorted(set(existing.aliases) | set(profile.aliases)),
                    "first_seen_date": reconciled_first_seen,
                    "profit_accrual_deferred_until": (
                        profile.profit_accrual_deferred_until
                        if profile.profit_accrual_deferred_until is not None
                        else existing.profit_accrual_deferred_until
                    ),
                }
            )
        elif reconciled_first_seen:
            profile = profile.model_copy(update={"first_seen_date": reconciled_first_seen})
        saved = save_fund_profile(profile)
        from app.services.fund_primary_sector_service import upsert_primary_sector_from_profile

        upsert_primary_sector_from_profile(saved)
        self._invalidate_profiles_cache()
        return saved

    def list_profiles(self) -> list[FundProfile]:
        if self._profiles_cache is None:
            self._profiles_cache = list_fund_profiles()
        return self._profiles_cache

    def resolve_holding(
        self,
        holding: Holding,
        *,
        fetch_benchmark: bool = True,
    ) -> Holding:
        profile = (
            get_fund_profile_by_code(holding.fund_code)
            if holding.fund_code != "000000"
            else None
        )
        if profile is None:
            profile = self.find_match(holding.fund_name)

        sector_name = holding.sector_name
        index_name = holding.intraday_index_name
        fund_name = holding.fund_name or (profile.fund_name if profile else None)
        fund_code = holding.fund_code if holding.fund_code != "000000" else (
            profile.fund_code if profile and profile.fund_code != "000000" else ""
        )
        if fund_code:
            from app.services.fund_primary_sector_service import resolve_primary_sector

            record = resolve_primary_sector(
                fund_code,
                fund_name=fund_name,
                allow_name_infer=False,
                fetch_benchmark=fetch_benchmark,
            )
            if record and record.source == "benchmark_index":
                sector_name = record.sector_name
                if record.intraday_index_name:
                    index_name = record.intraday_index_name
                if profile is not None and (
                    profile.sector_name != sector_name
                    or (
                        index_name
                        and profile.intraday_index_name != index_name
                    )
                ):
                    from app.database import save_fund_profile

                    save_fund_profile(
                        profile.model_copy(
                            update={
                                "sector_name": sector_name,
                                **(
                                    {"intraday_index_name": index_name}
                                    if index_name
                                    else {}
                                ),
                            }
                        )
                    )

        if profile is None:
            from app.services.fund_primary_sector_service import primary_sector_fields_for_holding

            fields = primary_sector_fields_for_holding(
                holding,
                allow_name_infer=False,
                fetch_benchmark=fetch_benchmark,
            )
            if fields:
                return holding.model_copy(update={**fields, "sector_name": sector_name or fields.get("sector_name")})
            if sector_name or index_name:
                return holding.model_copy(
                    update={
                        **({"sector_name": sector_name} if sector_name else {}),
                        **({"intraday_index_name": index_name} if index_name else {}),
                    }
                )
            return holding

        if not _is_valid_sector_label(sector_name):
            from app.services.fund_primary_sector_service import primary_sector_fields_for_holding

            fields = primary_sector_fields_for_holding(
                holding,
                fallback_code=profile.fund_code,
                allow_name_infer=False,
                fetch_benchmark=fetch_benchmark,
            )
            if fields.get("sector_name"):
                sector_name = fields["sector_name"]
            elif _is_valid_sector_label(profile.sector_name):
                sector_name = profile.sector_name

        if not index_name or not _looks_like_index_name(index_name):
            index_name = profile.intraday_index_name
        if not index_name or not _looks_like_index_name(index_name):
            index_name = infer_intraday_index_from_sector(sector_name)
        if not index_name or not _looks_like_index_name(index_name):
            index_name = infer_intraday_index_from_sector(profile.sector_name)
        if not index_name or not _looks_like_index_name(index_name):
            index_name = infer_intraday_index_from_fund_name(fund_name)

        sector_name, index_name = _normalize_index_and_board_fields(sector_name, index_name)

        updates: dict = {
            "sector_name": sector_name,
            "intraday_index_name": index_name,
            "sector_return_percent": holding.sector_return_percent
            if holding.sector_return_percent is not None
            else profile.sector_return_percent,
        }
        if (
            holding.fund_code == "000000"
            and profile.fund_code != "000000"
            and not profile.is_provisional
        ):
            updates["fund_code"] = profile.fund_code
        if profile.fund_name and normalize_fund_name(holding.fund_name) != normalize_fund_name(
            profile.fund_name
        ):
            if is_fund_name_match(
                normalize_fund_name(holding.fund_name),
                normalize_fund_name(profile.fund_name),
            ):
                updates["fund_name"] = profile.fund_name
        return holding.model_copy(update=updates)

    def resolve_holdings(
        self,
        holdings: list[Holding],
        *,
        fetch_benchmark: bool = True,
    ) -> list[Holding]:
        return [
            self.resolve_holding(holding, fetch_benchmark=fetch_benchmark)
            for holding in holdings
        ]

    def find_match(self, fund_name: str) -> FundProfile | None:
        target = normalize_fund_name(fund_name)
        if not target:
            return None
        for profile in self.list_profiles():
            candidates = [profile.fund_name, *profile.aliases]
            if any(is_fund_name_match(target, normalize_fund_name(candidate)) for candidate in candidates):
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
            if (
                profile is not None
                and holding.fund_code != "000000"
                and profile.fund_code != holding.fund_code
            ):
                # 早期 OCR 可能把错误代码写进 profile，确认后用东财查码结果覆盖
                delete_fund_profile(profile.fund_code)
                profile = None

            if profile is None:
                if holding.fund_code == "000000":
                    profile = _holding_to_provisional_profile(holding)
                else:
                    profile = _holding_to_provisional_profile(
                        holding,
                        fund_code=holding.fund_code,
                        is_provisional=False,
                    )
                self.save_profile(profile)
                created += 1
                continue

            merged = merge_holding_into_profile(
                profile,
                holding,
                total_amount=total_amount if total_amount > 0 else None,
            )
            self.save_profile(merged)
            updated += 1

        self._invalidate_profiles_cache()
        return ProfileSyncResult(updated=updated, created=created)

    def _find_profile_for_holding(self, holding: Holding) -> FundProfile | None:
        if holding.fund_code != "000000":
            by_code = get_fund_profile_by_code(holding.fund_code)
            if by_code is not None:
                return by_code
        return self.find_match(holding.fund_name)


def resolve_first_seen_anchor(profile: FundProfile, *, today: date | None = None) -> str:
    """首次录入持有时的稳定锚点日期：用户购入日 > OCR 持有天数回推 > 份额基准日 > 今天。"""
    today = today or date.today()
    if profile.first_purchase_date:
        return profile.first_purchase_date
    if profile.holding_days is not None and profile.holding_days >= 0:
        return (today - timedelta(days=profile.holding_days)).isoformat()
    if profile.shares_baseline_date:
        return profile.shares_baseline_date
    return today.isoformat()


def _anchor_signals_present(profile: FundProfile) -> bool:
    return bool(
        profile.first_purchase_date
        or profile.holding_days is not None
        or profile.shares_baseline_date
    )


def _merge_profile_fields_for_anchor(existing: FundProfile, profile: FundProfile) -> FundProfile:
    merged_updates: dict[str, str | int | None] = {}
    for key in ("holding_days", "holding_days_as_of", "shares_baseline_date", "first_purchase_date"):
        incoming = getattr(profile, key, None)
        current = getattr(existing, key, None)
        if incoming is not None:
            merged_updates[key] = incoming
        elif current is not None:
            merged_updates[key] = current
    return existing.model_copy(update=merged_updates)


def _repair_first_seen_against_baseline(first_seen: str, profile: FundProfile) -> str:
    if not profile.shares_baseline_date:
        return first_seen
    try:
        baseline = date.fromisoformat(profile.shares_baseline_date)
        seen = date.fromisoformat(first_seen)
    except ValueError:
        return first_seen
    if baseline < seen:
        return profile.shares_baseline_date
    return first_seen


def reconcile_first_seen_date(
    existing: FundProfile | None,
    profile: FundProfile,
) -> str | None:
    """在 save_profile 时确定 first_seen_date，避免读取路径写入 today 重置天数。"""
    first_seen = profile.first_seen_date or (existing.first_seen_date if existing else None)
    merged = _merge_profile_fields_for_anchor(existing, profile) if existing is not None else profile

    if existing is None:
        return first_seen or resolve_first_seen_anchor(merged)

    if first_seen:
        return _repair_first_seen_against_baseline(first_seen, merged)

    if _anchor_signals_present(merged):
        return resolve_first_seen_anchor(merged)
    return None


def merge_holding_into_profile(
    profile: FundProfile,
    holding: Holding,
    *,
    total_amount: float | None = None,
) -> FundProfile:
    updates: dict = {
        "fund_name": profile.fund_name if not profile.is_provisional else holding.fund_name,
        "holding_amount": holding.settled_holding_amount or holding.holding_amount,
        "settled_holding_amount": holding.settled_holding_amount or holding.holding_amount,
    }
    if holding.holding_profit is not None:
        updates["holding_profit"] = holding.holding_profit
    if holding.holding_return_percent is not None:
        updates["holding_return_percent"] = holding.holding_return_percent
    elif holding.return_percent is not None:
        updates["holding_return_percent"] = holding.return_percent
    if holding.yesterday_profit is not None:
        updates["yesterday_profit"] = holding.yesterday_profit
    if holding.daily_profit is not None:
        updates["daily_profit"] = holding.daily_profit
    if _is_valid_sector_label(holding.sector_name):
        updates["sector_name"] = holding.sector_name
    if holding.intraday_index_name and _looks_like_index_name(holding.intraday_index_name):
        updates["intraday_index_name"] = holding.intraday_index_name
    if holding.sector_return_percent is not None:
        updates["sector_return_percent"] = holding.sector_return_percent
    if total_amount and holding.holding_amount > 0:
        updates["position_percent"] = round(holding.holding_amount / total_amount * 100, 2)
    from app.services.profit_accrual_defer import apply_defer_to_profile

    merged = profile.model_copy(update=updates)
    return apply_defer_to_profile(merged, holding)


def _holding_to_provisional_profile(
    holding: Holding,
    *,
    fund_code: str | None = None,
    is_provisional: bool = True,
) -> FundProfile:
    code = fund_code or provisional_code_for_name(holding.fund_name)
    profile = FundProfile(
        fund_code=code,
        fund_name=holding.fund_name,
        aliases=_aliases_for_name(holding.fund_name),
        holding_amount=holding.settled_holding_amount or holding.holding_amount,
        settled_holding_amount=holding.settled_holding_amount or holding.holding_amount,
        holding_profit=holding.holding_profit,
        holding_return_percent=holding.holding_return_percent or holding.return_percent or None,
        daily_profit=holding.daily_profit,
        yesterday_profit=holding.yesterday_profit,
        sector_name=holding.sector_name,
        sector_return_percent=holding.sector_return_percent,
        intraday_index_name=holding.intraday_index_name,
        source="alipay-overview",
        is_provisional=is_provisional,
    )
    from app.services.profit_accrual_defer import apply_defer_to_profile

    return apply_defer_to_profile(profile, holding)


def _aliases_for_name(name: str) -> list[str]:
    compact = normalize_fund_name(name)
    aliases = {name}
    for length in (6, 8, 10):
        if len(compact) >= length:
            aliases.add(compact[:length])
    return sorted(aliases)


def provisional_code_for_name(fund_name: str) -> str:
    digest = hashlib.sha256(normalize_fund_name(fund_name).encode("utf-8")).hexdigest()
    return f"9{int(digest[:8], 16) % 100000:05d}"


def migrate_fund_profile_code(
    old_code: str,
    new_code: str,
    *,
    fund_name: str | None = None,
) -> FundProfile:
    """将档案从旧代码迁移到新代码（纠正 OCR 误码 / 临时代码）。"""
    from app.database import delete_fund_profile, get_fund_profile_by_code, save_fund_profile

    old = get_fund_profile_by_code(old_code)
    if old is None:
        raise ValueError("原基金档案不存在")

    new_code = new_code.strip().zfill(6)
    if len(new_code) != 6 or not new_code.isdigit():
        raise ValueError("新基金代码格式无效")

    existing = get_fund_profile_by_code(new_code)
    merged = old.model_copy(
        update={
            "fund_code": new_code,
            "fund_name": fund_name or old.fund_name,
            "is_provisional": False,
        },
    )
    if existing is not None and existing.fund_code != old_code:
        merged = merged.model_copy(
            update={"aliases": sorted(set(existing.aliases) | set(merged.aliases))}
        )

    delete_fund_profile(old_code)
    return save_fund_profile(merged)


def _is_valid_sector_label(name: str | None) -> bool:
    if not name:
        return False
    return _looks_like_board_label(name.strip())


def _sanitize_profile_sector_fields(profile: FundProfile) -> FundProfile:
    sector_name = profile.sector_name if _is_valid_sector_label(profile.sector_name) else None
    intraday_index_name = (
        profile.intraday_index_name
        if profile.intraday_index_name and _looks_like_index_name(profile.intraday_index_name)
        else None
    )
    if sector_name and _looks_like_index_name(sector_name):
        if not intraday_index_name:
            intraday_index_name = sector_name
        board = _infer_related_board_label(intraday_index_name)
        sector_name = board if _is_valid_sector_label(board) else None

    sector_name, intraday_index_name = _normalize_index_and_board_fields(
        sector_name,
        intraday_index_name,
    )
    if not intraday_index_name:
        inferred = infer_intraday_index_from_sector(sector_name)
        if inferred:
            intraday_index_name = inferred
    if not intraday_index_name:
        inferred = infer_intraday_index_from_fund_name(profile.fund_name)
        if inferred:
            intraday_index_name = inferred
            if not sector_name:
                board = _infer_related_board_label(inferred)
                if _is_valid_sector_label(board):
                    sector_name = board
    if intraday_index_name and infer_intraday_index_from_fund_name(profile.fund_name) is None:
        inferred_board_index = infer_intraday_index_from_sector(sector_name)
        if inferred_board_index and intraday_index_name == inferred_board_index:
            intraday_index_name = None

    if sector_name == profile.sector_name and intraday_index_name == profile.intraday_index_name:
        return profile
    return profile.model_copy(
        update={
            "sector_name": sector_name,
            "intraday_index_name": intraday_index_name,
        }
    )


def _looks_like_board_label(name: str) -> bool:
    if not name or len(name) < 2 or len(name) > 16:
        return False
    if name.strip() in _DETAIL_TAB_LABELS or name.strip() in {"场内指数", "数据来源"}:
        return False
    if not re.search(r"[\u4e00-\u9fff]", name):
        from app.services.sector_canonical import get_canonical_sector

        return get_canonical_sector(name.strip()) is not None
    from app.services.fund_name_utils import FUND_PRODUCT_SUFFIX_RE, looks_like_fund_product_name

    compact = re.sub(r"\s+", "", name.strip())
    if looks_like_fund_product_name(name):
        return False
    if FUND_PRODUCT_SUFFIX_RE.search(compact):
        return False
    if re.search(r"(混合|联接|链接|发起|精选|股票)[A-CEH]?$", compact, re.IGNORECASE):
        return False
    if len(compact) > 8 and any(
        token in compact for token in ("混合", "联接", "链接", "发起", "精选", "ETF", "LOF")
    ):
        return False
    if _looks_like_index_name(name):
        return False
    if re.fullmatch(r"[+-]?\d[\d,]*(?:\.\d+)?", name.replace(",", "")):
        return False
    skip_tokens = (
        "同类基金",
        "看涨",
        "看跌",
        "持有人数",
        "排名",
        "日期",
        "国产算力",
    )
    if name in {"算力", "军工", "设备"}:
        return False
    return not any(token in name for token in skip_tokens)


def _looks_like_index_name(name: str) -> bool:
    if name.startswith("中证") or name.startswith("上证") or name.startswith("深证"):
        return True
    return name.endswith("指数") or "ETF" in name


def infer_intraday_index_from_sector(sector_name: str | None) -> str | None:
    """关联板块短名 → 东财 zz 指数（如 半导体→中证半导体 931865）。"""
    if not sector_name or not _is_valid_sector_label(sector_name):
        return None
    from app.services.sector_canonical import _BOARD_TO_INTRADAY_INDEX
    from app.services.sector_labels import normalize_sector_label

    label = normalize_sector_label(sector_name)
    if not label:
        return None
    return _BOARD_TO_INTRADAY_INDEX.get(label)


def infer_intraday_index_from_fund_name(fund_name: str | None) -> str | None:
    """从 ETF 联接/主题基金名称推断场内指数（档案 OCR 漏识别时补全）。"""
    if not fund_name:
        return None
    normalized = fund_name.replace("...", "").strip()
    compact = re.sub(r"\s+", "", normalized)
    for token in (
        "中证电网设备",
        "中证人工智能",
        "中证半导体",
        "中证新能源",
        "中证军工",
    ):
        if token in normalized:
            return token
    _feeder_theme_to_index = {
        "人工智能": "中证人工智能",
        "电网设备": "中证电网设备",
        "半导体": "中证半导体",
        "新能源": "中证新能源",
        "军工": "中证军工",
    }
    for theme, index_name in _feeder_theme_to_index.items():
        if f"{theme}ETF" in compact:
            return index_name
    match = re.search(r"(中证[\u4e00-\u9fff]{2,12})(?:主题|ETF|指数|联接)", normalized)
    if match and _looks_like_index_name(match.group(1)):
        return match.group(1)
    return None


def _normalize_index_and_board_fields(
    sector_name: str | None,
    intraday_index_name: str | None,
) -> tuple[str | None, str | None]:
    """列表 OCR 常把场内指数误写入 sector_name；拆成指数 + 关联板块短名。"""
    if sector_name and _looks_like_index_name(sector_name):
        if not intraday_index_name:
            intraday_index_name = sector_name
        board = _infer_related_board_label(intraday_index_name)
        if sector_name == intraday_index_name or not _is_valid_sector_label(sector_name):
            sector_name = board if _is_valid_sector_label(board) else sector_name
    return sector_name, intraday_index_name


def _infer_related_board_label(index_name: str) -> str:
    if "电网设备" in index_name:
        return "电网设备"
    if "人工智能" in index_name:
        return "人工智能"
    if "半导体" in index_name:
        return "半导体"
    for prefix in ("中证", "国证", "上证", "深证"):
        if index_name.startswith(prefix) and len(index_name) > len(prefix):
            return index_name[len(prefix) :]
    return index_name
