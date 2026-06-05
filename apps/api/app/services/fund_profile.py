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


from app.services.fund_name_utils import is_fund_name_match, normalize_fund_name


CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
NUMBER_RE = re.compile(r"^[+-]?\d[\d,]*(?:\.\d+)?$")
PERCENT_RE = re.compile(r"^([+-]?\d+(?:\.\d+)?)%$")
SECTOR_RE = re.compile(r"^(.+?)[▼▲]([+-]?\d+(?:\.\d+)?)%\s*[>＞]?$")
NAME_PERCENT_RE = re.compile(r"^(.+?)\s*([+-]?\d+(?:\.\d+)?)%\s*[>＞]?$")
_RELATED_BOARD_SUMMARY_RE = re.compile(
    r"^关联板块[：:]\s*(.+?)\s*([+-]?\d+(?:\.\d+)?)%\s*[>＞]?\s*$"
)
_DETAIL_TAB_LABELS = frozenset({"关联板块", "业绩走势", "我的收益"})


class FundProfileService:
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
        profile = merge_detail_profile(existing, profile)
        if profile.source == "yangjibao-detail":
            profile = profile.model_copy(update={"is_provisional": False})
        return save_fund_profile(profile)

    def list_profiles(self) -> list[FundProfile]:
        return list_fund_profiles()

    def resolve_holding(self, holding: Holding) -> Holding:
        profile = (
            get_fund_profile_by_code(holding.fund_code)
            if holding.fund_code != "000000"
            else None
        )
        if profile is None:
            profile = self.find_match(holding.fund_name)
        if profile is None:
            return holding

        sector_name = holding.sector_name
        if not _is_valid_sector_label(sector_name):
            sector_name = profile.sector_name if _is_valid_sector_label(profile.sector_name) else None
        elif not sector_name and _is_valid_sector_label(profile.sector_name):
            sector_name = profile.sector_name

        index_name = holding.intraday_index_name
        if not index_name or not _looks_like_index_name(index_name):
            index_name = profile.intraday_index_name
        if not index_name or not _looks_like_index_name(index_name):
            index_name = infer_intraday_index_from_sector(sector_name)
        if not index_name or not _looks_like_index_name(index_name):
            index_name = infer_intraday_index_from_sector(profile.sector_name)
        if not index_name or not _looks_like_index_name(index_name):
            index_name = infer_intraday_index_from_fund_name(
                holding.fund_name or profile.fund_name
            )

        sector_name, index_name = _normalize_index_and_board_fields(sector_name, index_name)

        updates: dict = {
            "sector_name": sector_name,
            "intraday_index_name": index_name,
            "sector_return_percent": holding.sector_return_percent
            if holding.sector_return_percent is not None
            else profile.sector_return_percent,
        }
        if holding.fund_code == "000000" and profile.fund_code != "000000":
            updates["fund_code"] = profile.fund_code
            updates["fund_name"] = profile.fund_name
        return holding.model_copy(update=updates)

    def resolve_holdings(self, holdings: list[Holding]) -> list[Holding]:
        return [self.resolve_holding(holding) for holding in holdings]

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


def merge_detail_profile(existing: FundProfile | None, incoming: FundProfile) -> FundProfile:
    """详情 OCR 再次上传时保留已有板块/指数字段，避免整份覆盖成空。"""
    incoming = _sanitize_profile_sector_fields(incoming)
    if existing is not None:
        existing = _sanitize_profile_sector_fields(existing)
    if incoming.intraday_index_name and not incoming.sector_name:
        incoming = incoming.model_copy(
            update={"sector_name": _infer_related_board_label(incoming.intraday_index_name)}
        )
    if existing is None:
        return incoming

    def pick_sector_name(new: str | None, old: str | None) -> str | None:
        if _is_valid_sector_label(new) and new not in _DETAIL_TAB_LABELS:
            return new
        if _is_valid_sector_label(old) and old not in _DETAIL_TAB_LABELS:
            return old
        return None

    def pick_index_name(new: str | None, old: str | None) -> str | None:
        if new and _looks_like_index_name(new):
            return new
        if old and _looks_like_index_name(old):
            return old
        return None

    def pick_float(new: float | None, old: float | None) -> float | None:
        return new if new is not None else old

    return incoming.model_copy(
        update={
            "aliases": sorted(set(existing.aliases) | set(incoming.aliases)),
            "sector_name": pick_sector_name(incoming.sector_name, existing.sector_name),
            "intraday_index_name": pick_index_name(
                incoming.intraday_index_name, existing.intraday_index_name
            ),
            "sector_return_percent": pick_float(
                incoming.sector_return_percent, existing.sector_return_percent
            ),
            "holding_shares": pick_float(incoming.holding_shares, existing.holding_shares),
            "holding_cost": pick_float(incoming.holding_cost, existing.holding_cost),
            "yesterday_profit": pick_float(incoming.yesterday_profit, existing.yesterday_profit),
            "holding_days": incoming.holding_days if incoming.holding_days is not None else existing.holding_days,
        }
    )


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
    if _is_valid_sector_label(holding.sector_name):
        updates["sector_name"] = holding.sector_name
    if holding.intraday_index_name and _looks_like_index_name(holding.intraday_index_name):
        updates["intraday_index_name"] = holding.intraday_index_name
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
        intraday_index_name=holding.intraday_index_name,
        source="yangjibao-overview",
        is_provisional=is_provisional,
    )


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
    (
        intraday_index_name,
        intraday_index_return,
        sector_name,
        sector_return,
    ) = _find_detail_sector_fields(lines)

    profile = FundProfile(
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
        intraday_index_name=intraday_index_name,
    )
    return _sanitize_profile_sector_fields(profile)


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


def _find_detail_sector_fields(
    lines: list[str],
) -> tuple[str | None, float | None, str | None, float | None]:
    """解析养基宝详情：场内指数（涨跌口径）+ 关联板块（展示）。"""
    intraday_index_name: str | None = None
    intraday_index_return: float | None = None
    sector_name: str | None = None
    sector_return: float | None = None
    last_board_summary: tuple[str, float | None] | None = None

    for index, line in enumerate(lines):
        cleaned = _normalize_ocr_line(line)
        if cleaned.startswith("场内指数"):
            inline = cleaned.removeprefix("场内指数").strip(" ：: \t")
            if inline:
                name, change = _parse_name_percent_line(inline)
                if name and _looks_like_index_name(name):
                    intraday_index_name = name
                    intraday_index_return = change
            if intraday_index_name is None:
                name, change = _name_percent_after(lines, index + 1, limit=8)
                if name:
                    intraday_index_name = name
                    intraday_index_return = change
        if cleaned.startswith("关联板块"):
            summary = _RELATED_BOARD_SUMMARY_RE.match(cleaned)
            if summary:
                board = summary.group(1).strip()
                if _looks_like_board_label(board):
                    last_board_summary = (board, float(summary.group(2)))
                continue
            board, ret = _parse_related_board_line(cleaned)
            if board and _is_valid_sector_label(board):
                last_board_summary = (board, ret)
            else:
                # "关联板块"单独成行，或无效格式时扫描后续行
                board, ret = _related_board_after_heading(lines, index)
                if board:
                    # 这里board可能是指数名（中证/上证/深证开头）
                    # 在关联板块上下文中接受它作为板块名
                    last_board_summary = (board, ret)

    if last_board_summary is not None:
        sector_name, sector_return = last_board_summary

    for line in lines:
        cleaned = _normalize_ocr_line(line)
        match = SECTOR_RE.match(cleaned) or NAME_PERCENT_RE.match(cleaned)
        if not match:
            continue
        name = match.group(1).strip()
        change = float(match.group(2))
        if _looks_like_index_name(name):
            if intraday_index_name is None:
                intraday_index_name = name
                intraday_index_return = change
        elif sector_name is None and _looks_like_board_label(name):
            sector_name = name
            sector_return = change

    return _finalize_sector_fields(
        intraday_index_name,
        intraday_index_return,
        sector_name,
        sector_return,
    )


def _normalize_ocr_line(line: str) -> str:
    return re.sub(r"\s*[>＞]+\s*$", "", line.strip())


def _finalize_sector_fields(
    intraday_index_name: str | None,
    intraday_index_return: float | None,
    sector_name: str | None,
    sector_return: float | None,
) -> tuple[str | None, float | None, str | None, float | None]:
    if intraday_index_name and sector_name is None:
        sector_name = _infer_related_board_label(intraday_index_name)
    # 养基宝有场内指数时，涨跌口径以指数为准（非关联板块概念涨跌）
    if intraday_index_name and intraday_index_return is not None:
        sector_return = intraday_index_return
    elif sector_return is None:
        sector_return = intraday_index_return
    return intraday_index_name, intraday_index_return, sector_name, sector_return


def _parse_name_percent_line(text: str) -> tuple[str | None, float | None]:
    text = _normalize_ocr_line(text)
    match = SECTOR_RE.match(text) or NAME_PERCENT_RE.match(text)
    if not match:
        return None, None
    return match.group(1).strip(), float(match.group(2))


def _parse_related_board_line(line: str) -> tuple[str | None, float | None]:
    """解析'关联板块'行，支持多种格式：
    1. 关联板块：板块名 +3.2%
    2. 关联板块：板块名 （涨跌可能在下行）
    3. 关联板块 +3.2% （板块名在前面或后面）
    """
    match = re.match(
        r"^关联板块[：:\s]+(.+?)(?:\s*([+-]?\d+(?:\.\d+)?)%)?\s*$",
        line.strip(),
    )
    if not match:
        return None, None
    tail = match.group(1).strip()
    if "同类基金" in tail:
        tail = re.split(r"\d*只同类基金", tail)[0].strip()
    if not tail or tail in _DETAIL_TAB_LABELS:
        return None, None
    if _looks_like_index_name(tail):
        return None, None
    ret = float(match.group(2)) if match.group(2) else None

    # 尝试从tail中提取 "名称 百分比" 的完整格式
    pct_inline = re.search(r"^(.+?)\s*([+-]?\d+(?:\.\d+)?)%\s*$", tail)
    if pct_inline:
        inline_name = pct_inline.group(1).strip()
        if _looks_like_board_label(inline_name):
            return inline_name, float(pct_inline.group(2))

    # tail本身可能是板块名（即使没有百分比）
    if _looks_like_board_label(tail):
        return tail, ret

    # 最后的备选：即使tail不完全匹配板块标签，但包含中文且长度合理
    if any("一" <= char <= "鿿" for char in tail) and 2 <= len(tail) <= 20:
        return tail, ret

    return None, None


def _related_board_after_heading(
    lines: list[str],
    index: int,
) -> tuple[str | None, float | None]:
    """扫描关联板块标签后的行，支持：(1) 名称+涨跌同行 (2) 仅名称，涨跌在下行 (3) 无涨跌但有百分比标记"""
    name_candidate: str | None = None
    tab_count = 0

    for offset in range(1, 12):
        if index + offset >= len(lines):
            break
        line = lines[index + offset]
        cleaned = _normalize_ocr_line(line)
        if not cleaned:
            continue

        # tab标签后继续扫描（但超过2个就break）
        if cleaned in _DETAIL_TAB_LABELS:
            tab_count += 1
            if tab_count > 2:
                break
            continue

        # 复位tab计数
        tab_count = 0

        if "同类基金" in cleaned:
            cleaned = re.split(r"\d*只同类基金", cleaned)[0].strip()

        # 先尝试完整格式：名称 + 百分比
        name, change = _parse_name_percent_line(cleaned)
        if name and _looks_like_board_label(name):
            return name, change

        # 特殊处理：中证/上证/深证开头的需要作为板块名处理
        if not name and (cleaned.startswith("中证") or cleaned.startswith("上证") or cleaned.startswith("深证")):
            # 从本行提取百分比
            pct_match = re.search(r"([+-]?\d+(?:\.\d+)?)%", cleaned)
            change = float(pct_match.group(1)) if pct_match else None
            if change is not None:
                return cleaned, change
            # 即使没有百分比也保留为候选
            name_candidate = cleaned
            continue

        # 备选：仅看是否是有效的板块标签
        if not name_candidate and _looks_like_board_label(cleaned):
            name_candidate = cleaned
            continue

        # 如果已有板块名，尝试从本行或下行提取百分比
        if name_candidate:
            # 本行中查找百分比
            pct_match = re.search(r"([+-]?\d+(?:\.\d+)?)%", cleaned)
            if pct_match:
                change = float(pct_match.group(1))
                return name_candidate, change

    return None, None


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


def _name_percent_after(
    lines: list[str],
    start: int,
    *,
    limit: int = 6,
) -> tuple[str | None, float | None]:
    for line in lines[start : start + limit]:
        cleaned = _normalize_ocr_line(line)
        if not cleaned or cleaned in {"关联板块", "业绩走势", "我的收益"}:
            break
        match = SECTOR_RE.match(cleaned) or NAME_PERCENT_RE.match(cleaned)
        if match and _looks_like_index_name(match.group(1).strip()):
            change = float(match.group(2))
            return match.group(1).strip(), change
    return None, None


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


def _find_sector(lines: list[str]) -> tuple[str | None, float | None]:
    fields = _find_detail_sector_fields(lines)
    lookup = fields[0] or fields[2]
    ret = fields[1] if fields[0] else fields[3]
    return lookup, ret
