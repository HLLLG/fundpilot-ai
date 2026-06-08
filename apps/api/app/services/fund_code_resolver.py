from __future__ import annotations

from functools import lru_cache

from app.services.fund_name_utils import is_fund_name_match, normalize_fund_name


def preload_fund_name_table() -> None:
    """启动时预热基金名称表，避免首次 OCR 查码卡顿。"""
    try:
        _fund_name_table()
    except Exception:
        pass


@lru_cache(maxsize=1)
def _fund_name_table() -> list[tuple[str, str]]:
    try:
        import akshare as ak  # type: ignore[import-not-found]

        frame = ak.fund_name_em()
    except Exception:
        return []

    if frame is None or frame.empty:
        return []

    code_col = "基金代码" if "基金代码" in frame.columns else frame.columns[0]
    name_col = "基金简称" if "基金简称" in frame.columns else frame.columns[1]
    rows: list[tuple[str, str]] = []
    for _, row in frame.iterrows():
        code = str(row[code_col]).strip().zfill(6)
        name = str(row[name_col]).strip()
        if code and name:
            rows.append((code, name))
    return rows


def resolve_holding_fund_code(
    fund_name: str,
    *,
    existing_code: str | None = None,
) -> tuple[str | None, str | None]:
    """按名称查码。返回 (code, source)；source 为 akshare | None。"""
    if existing_code and existing_code != "000000":
        return existing_code, None
    code = lookup_fund_code_by_name(fund_name)
    if code:
        return code, "akshare"
    return None, None


def lookup_fund_code_by_name(fund_name: str) -> str | None:
    target = normalize_fund_name(fund_name)
    if not target:
        return None

    best_code: str | None = None
    best_score = 0
    for code, name in _fund_name_table():
        normalized = normalize_fund_name(name)
        if not normalized:
            continue
        if target == normalized:
            return code
        if is_fund_name_match(target, normalized):
            score = min(len(target), len(normalized))
            if score > best_score:
                best_score = score
                best_code = code
    return best_code
