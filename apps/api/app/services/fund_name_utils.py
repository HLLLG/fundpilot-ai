from __future__ import annotations

import re

FUND_ISSUER_PREFIX = (
    "中航",
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
# 允许 混合/股票/指数/联接 与份额字母间出现 (QDII)/（QDII）/(QDII-ETF) 等括注
_QDII_INFIX = r"(?:[（(](?:QDII|LOF|FOF|QDII-ETF)[)）])?"
FUND_PRODUCT_SUFFIX_RE = re.compile(
    r"(混合" + _QDII_INFIX + r"[A-CEH]?"
    r"|联接" + _QDII_INFIX + r"[A-CEH]"
    r"|ETF联接" + _QDII_INFIX + r"[A-CEH]"
    r"|ETF联" + _QDII_INFIX + r"[A-CEH]"
    r"|主题ETF联接" + _QDII_INFIX + r"[A-CEH]"
    r"|发起式联接" + _QDII_INFIX + r"[A-CEH]"
    r"|股票" + _QDII_INFIX + r"[A-CEH]?"
    r"|指数" + _QDII_INFIX + r"[A-CEH])$",
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
PARTIAL_FUND_NAME_ENDINGS = ("混合", "联接", "ETF", "ETF联", "主", "混", "股票", "股", "指数")
# 东财简称相对 OCR/支付宝展示的差异（查码时双方都要归一化）
LOOKUP_NAME_STRIP_TOKENS = ("发起式", "主题")
# 东财 QDII 全称常带「人民币/美元/港币」份额币种，支付宝 OCR 常省略
LOOKUP_CURRENCY_SUFFIXES = ("人民币", "美元", "港币")
SHARE_CLASS_SUFFIX_RE = re.compile(
    r"(?:混合|联接|ETF联接|ETF联|股票|指数)"
    + _QDII_INFIX
    + r"(?:人民币|美元|港币)?"
    + r"([A-CEH])$",
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
    """东财查码专用：去掉发起式/主题/板块缩写等展示差异后再比对。"""
    result = normalize_fund_name(name)
    result = result.replace("（", "(").replace("）", ")")
    for token in LOOKUP_NAME_STRIP_TOKENS:
        result = result.replace(token, "")
    # 支付宝 OCR 常省略「发起」：混合发起C ↔ 混合C；ETF发起联接C ↔ ETF联接C
    result = re.sub(r"混合发起([A-CEH])$", r"混合\1", result, flags=re.IGNORECASE)
    result = re.sub(r"ETF发起联接", "ETF联接", result, flags=re.IGNORECASE)
    result = re.sub(r"发起联接", "联接", result, flags=re.IGNORECASE)
    # 支付宝常写「科创」；东财全称「上证科创板」
    result = result.replace("上证科创板", "科创")
    # 广发全球精选股票(QDII)C ↔ 东财 广发全球精选股票(QDII)人民币C
    for currency in LOOKUP_CURRENCY_SUFFIXES:
        result = re.sub(
            rf"(?<=[)）]){re.escape(currency)}(?=[A-CEH]$)",
            "",
            result,
            flags=re.IGNORECASE,
        )
    # 支付宝展示名常带「材料」：天弘半导体材料设备指数C ↔ 东财 天弘半导体设备指数C
    result = result.replace("半导体材料设备", "半导体设备")
    return result


def extract_share_class_letter(name: str) -> str | None:
    normalized = normalize_fund_name(name)
    match = SHARE_CLASS_SUFFIX_RE.search(normalized)
    if match:
        return match.group(1).upper()
    # QDII 括注 + 可选币种 + 份额字母（如 (QDII)人民币C / (QDII)C）
    qdii_match = re.search(
        r"(?:[（(](?:QDII|LOF|FOF|QDII-ETF)[)）])?(?:人民币|美元|港币)?([A-CEH])$",
        normalized,
        flags=re.IGNORECASE,
    )
    if qdii_match:
        return qdii_match.group(1).upper()
    return None


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
