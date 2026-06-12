from __future__ import annotations

import re

FUND_ISSUER_PREFIX = (
    "景顺长城",
    "易方达",
    "汇添富",
    "海富通",
    "摩根",
    "华夏",
    "银河",
    "广发",
    "中欧",
    "招商",
    "天弘",
    "南方",
    "嘉实",
    "博时",
    "平安",
    "富国",
    "鹏华",
    "诺安",
    "景顺",
    "长城",
    "华安",
    "东财",
    "中信",
    "国投",
    "申万",
    "中银",
    "建信",
    "交银",
    "农银",
    "工银",
    "民生",
    "光大",
    "新华",
    "前海",
    "兴全",
    "国寿",
    "华泰",
    "金地",
    "信澳",
    "国泰",
    "融通",
    "银华",
    "万家",
    "中邮",
    "德邦",
    "永赢",
    "国联",
    "东吴",
    "宝盈",
    "东海",
    "九泰",
    "东方",
    "红土",
    "华润",
    "汇安",
    "湘财",
)
PROMO_MARKERS = (
    "投资锦囊",
    "基金经理说",
    "市场解读",
    "财富号",
)
FUND_PRODUCT_SUFFIX_RE = re.compile(
    r"(混合[A-CEH]?|联接[A-CEH]|ETF联接[A-CEH]|ETF联[A-CEH]|主题ETF联接[A-CEH]|发起式联接[A-CEH])$",
    re.IGNORECASE,
)
FUND_NAME_HINTS = (
    "ETF",
    "混合",
    "混",
    "联接",
    "债券",
    "指数",
    "主题",
    "发起",
    "军工",
    "成长",
)
PARTIAL_FUND_NAME_ENDINGS = ("混合", "联接", "ETF", "ETF联", "主", "混")
# 东财简称相对 OCR/支付宝展示的差异（查码时双方都要归一化）
LOOKUP_NAME_STRIP_TOKENS = ("发起式",)
SHARE_CLASS_SUFFIX_RE = re.compile(
    r"(混合|联接|ETF联接|ETF联)([A-CEH])$",
    re.IGNORECASE,
)


def sanitize_fund_name(name: str) -> str:
    """Strip Alipay promo banners and OCR junk prepended to fund names."""
    cleaned = name.strip()
    if not cleaned:
        return cleaned

    extracted = _extract_fund_name_by_issuer(cleaned)
    if extracted is not None:
        return _normalize_fund_suffix(extracted)

    return _normalize_fund_suffix(cleaned)


def normalize_fund_name(name: str) -> str:
    return _normalize_fund_suffix(sanitize_fund_name(name))


def normalize_fund_name_for_lookup(name: str) -> str:
    """东财查码专用：去掉发起式等展示差异后再比对。"""
    result = normalize_fund_name(name)
    for token in LOOKUP_NAME_STRIP_TOKENS:
        result = result.replace(token, "")
    return result


def extract_share_class_letter(name: str) -> str | None:
    match = SHARE_CLASS_SUFFIX_RE.search(normalize_fund_name(name))
    if not match:
        return None
    return match.group(2).upper()


def _extract_fund_name_by_issuer(text: str) -> str | None:
    issuers = sorted(FUND_ISSUER_PREFIX, key=len, reverse=True)
    candidates: list[tuple[int, str]] = []

    for issuer in issuers:
        start = 0
        while True:
            idx = text.find(issuer, start)
            if idx < 0:
                break
            candidate = text[idx:]
            if FUND_PRODUCT_SUFFIX_RE.search(candidate):
                candidates.append((idx, candidate))
            start = idx + 1

    if not candidates:
        return None

    if any(marker in text for marker in PROMO_MARKERS):
        _, candidate = max(candidates, key=lambda item: item[0])
        return candidate

    idx, candidate = min(candidates, key=lambda item: item[0])
    if idx <= 2:
        return candidate
    return None


def _normalize_fund_suffix(name: str) -> str:
    result = (
        name.replace("...", "")
        .replace("..", "")
        .replace(".", "")
        .replace("·", "")
        .replace(" ", "")
        .strip()
    )
    return re.sub(r"ETF联([A-CEH])$", r"ETF联接\1", result, flags=re.IGNORECASE)


def is_fund_name_match(left: str, right: str) -> bool:
    if not left or not right:
        return False
    left_lookup = normalize_fund_name_for_lookup(left)
    right_lookup = normalize_fund_name_for_lookup(right)
    if left_lookup == right_lookup:
        return True
    return left_lookup in right_lookup or right_lookup in left_lookup


def lookup_match_score(left: str, right: str) -> int:
    left_lookup = normalize_fund_name_for_lookup(left)
    right_lookup = normalize_fund_name_for_lookup(right)
    if left_lookup == right_lookup:
        return 1_000_000 + len(left_lookup)
    if left_lookup in right_lookup:
        return len(left_lookup) * 100 + len(right_lookup)
    if right_lookup in left_lookup:
        return len(right_lookup) * 100 + len(left_lookup)
    return 0


def looks_like_fund_product_name(line: str) -> bool:
    """Loose fund-name detector used as block anchor (Yangjibao-style)."""
    cleaned = line.strip()
    if not cleaned or len(cleaned) < 3:
        return False
    if cleaned.endswith("》"):
        return False
    if any(marker in cleaned for marker in PROMO_MARKERS) and not FUND_PRODUCT_SUFFIX_RE.search(cleaned):
        return False
    if FUND_PRODUCT_SUFFIX_RE.search(cleaned.rstrip(".…")):
        return any(issuer in cleaned for issuer in FUND_ISSUER_PREFIX) or len(cleaned) >= 10
    if cleaned.endswith(("...", "..", ".")) and any(hint in cleaned for hint in FUND_NAME_HINTS):
        return True
    if any(cleaned.endswith(suffix) for suffix in PARTIAL_FUND_NAME_ENDINGS):
        return any(issuer in cleaned for issuer in FUND_ISSUER_PREFIX)
    return False
