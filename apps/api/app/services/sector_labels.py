from __future__ import annotations

from dataclasses import dataclass
import re

from app.services.sector_registry_data import THEME_BOARD_INDEX

_TOPIC_ALIASES = (
    "人工智能",
    "电网设备",
    "半导体",
    "国防军工",
    "商业航天",
    "红利",
    "新能源",
    "传媒",
    "CPO",
)

_FUND_COMPANY_PREFIXES = (
    "华夏",
    "中欧",
    "天弘",
    "富国",
    "广发",
    "中航",
    "易方达",
    "嘉实",
    "南方",
    "汇添富",
    "招商",
    "博时",
    "工银瑞信",
    "工银",
    "建信",
    "农银汇理",
    "农银",
    "交银施罗德",
    "交银",
    "中银",
    "中信保诚",
    "华安",
    "华泰柏瑞",
    "华泰",
    "华宝",
    "华融",
    "华商",
    "银华",
    "鹏华",
    "国泰",
    "国投瑞银",
    "大成",
    "景顺长城",
    "汇丰晋信",
    "上投摩根",
    "东方",
    "东吴",
    "长城",
    "长信",
    "民生加银",
    "兴证全球",
    "兴业",
    "浦银安盛",
    "平安",
    "泰达宏利",
    "国联安",
    "万家",
    "融通",
    "永赢",
    "创金合信",
    "西部利得",
    "中泰",
    "中金",
    "北信瑞丰",
    "睿远",
    "圆信永丰",
    "红土",
    "诺安",
    "海富通",
    "德邦",
    "财通",
    "金鹰",
    "信达澳亚",
    "摩根士丹利",
    "摩根",
    "汇丰",
    "施罗德",
    "贝莱德",
    "富达",
    "某某",
)

_SEMANTIC_NOISE_TOKENS = (
    "ETF",
    "发起式",
    "发起",
    "联接",
    "主题",
    "指数",
    "混合",
    "QDII",
    "人民币",
)

_GENERIC_SEMANTIC_LABELS = {
    "灵活配置",
    "成长精选股票",
    "稳健回报",
}

# 投资风格/策略描述词（非主题词）。用于把"清洗后名称"判定为泛化风格短语而非板块主题，
# 相比"主题白名单"更稳定——不需要随新主题持续扩容，只需覆盖有限的风格用语。
_STYLE_STOPWORDS = (
    "灵活配置",
    "稳健回报",
    "稳健增值",
    "指数增强",
    "量化对冲",
    "量化选股",
    "灵活",
    "稳健",
    "回报",
    "成长",
    "精选",
    "价值",
    "优选",
    "优质",
    "增长",
    "机会",
    "龙头",
    "量化",
    "策略",
    "配置",
    "平衡",
    "进取",
    "收益",
    "增强",
    "均衡",
    "卓越",
    "领先",
    "智选",
    "稳赢",
    "稳盈",
    "安心",
    "核心",
    "优化",
    "稳定",
    "增利",
    "添利",
    "尊享",
    "动力",
    "发现",
    "轮动",
    "多策略",
    "机遇",
    "领航",
    "远见",
    "远航",
    "启航",
    "致远",
    "睿智",
    "睿享",
    "睿见",
    "精彩",
    "精致",
    "臻选",
    "匠心",
    "匠芯",
    "先行",
    "先锋",
    "焦点",
    "聚焦",
    "汇聚",
    "汇享",
    "共赢",
    "同享",
    "创新驱动",
    "创新成长",
    "新兴成长",
    "价值成长",
    "红利成长",
    "成长动力",
)


def is_generic_style_phrase(cleaned: str) -> bool:
    """清洗后名称是否只是投资风格描述（如"稳健回报"），而非具体主题（如"半导体""全球高端制造"）。"""
    if not cleaned:
        return True
    if cleaned in _GENERIC_SEMANTIC_LABELS:
        return True
    remainder = cleaned
    for word in sorted(_STYLE_STOPWORDS, key=len, reverse=True):
        remainder = remainder.replace(word, "")
    return not remainder.strip()


@dataclass(frozen=True)
class SemanticSectorCandidate:
    sector_name: str
    source: str = "semantic_name"
    confidence: float = 0.0
    reason: str = ""
    quote_key: str | None = None


def normalize_sector_label(label: str | None) -> str:
    if not label:
        return ""
    cleaned = re.sub(r"\.{2,}", "", label.strip())
    cleaned = re.sub(r"\s+", "", cleaned)
    return cleaned


def build_sector_candidates(label: str | None) -> list[str]:
    base = normalize_sector_label(label)
    if not base:
        return []

    seen: set[str] = set()
    candidates: list[str] = []

    def add(value: str) -> None:
        normalized = normalize_sector_label(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            candidates.append(normalized)

    add(base)
    for prefix in ("中证", "国证", "上证", "深证"):
        if base.startswith(prefix) and len(base) > len(prefix):
            add(base[len(prefix) :])
    for suffix in ("主题", "指数", "ETF", "板块"):
        if base.endswith(suffix) and len(base) > len(suffix):
            add(base[: -len(suffix)])
    for token in _TOPIC_ALIASES:
        if token in base:
            add(token)
    return candidates


def sector_label_key(label: str | None) -> str:
    return normalize_sector_label(label).lower()


def _clean_semantic_fund_name(fund_name: str) -> str:
    cleaned = normalize_sector_label(fund_name.replace("...", ""))
    cleaned = re.sub(r"[（(]QDII[）)]", "", cleaned, flags=re.IGNORECASE)
    for prefix in sorted(_FUND_COMPANY_PREFIXES, key=len, reverse=True):
        if cleaned.startswith(prefix) and len(cleaned) > len(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    for prefix in ("中证", "国证", "上证", "深证"):
        if cleaned.startswith(prefix) and len(cleaned) > len(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    # 保留"科创"语义（如"上证科创板芯片设计"→"科创芯片设计"），不整段丢弃，
    # 否则后续无法再识别出这是科创板相关主题。
    cleaned = cleaned.replace("科创板", "科创")
    for token in _SEMANTIC_NOISE_TOKENS:
        cleaned = re.sub(re.escape(token), "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[A-Z]$", "", cleaned, flags=re.IGNORECASE)
    return cleaned


def _theme_board_match(normalized: str) -> SemanticSectorCandidate | None:
    names = tuple(dict.fromkeys((*THEME_BOARD_INDEX.keys(), *_TOPIC_ALIASES)))
    for token in sorted(names, key=lambda label: (-len(label), label)):
        if token in normalized:
            row = THEME_BOARD_INDEX.get(token)
            quote_key = row[1] if row else None
            return SemanticSectorCandidate(
                sector_name=token,
                confidence=0.86 if quote_key else 0.72,
                reason="registered_theme_keyword",
                quote_key=quote_key,
            )
    return None


def infer_semantic_sector_from_fund_name(
    fund_name: str | None,
) -> SemanticSectorCandidate | None:
    if not fund_name:
        return None
    normalized = normalize_sector_label(fund_name.replace("...", ""))
    if not normalized:
        return None

    is_overseas = bool(re.search(r"QDII|全球|海外", normalized, flags=re.IGNORECASE))
    if is_overseas and any(token in normalized for token in ("科技互联网", "科技先锋", "全球科技")):
        return SemanticSectorCandidate(
            sector_name="海外基金",
            confidence=0.62,
            reason="overseas_generic_technology",
        )

    registered = _theme_board_match(normalized)
    if registered is not None:
        return registered

    cleaned = _clean_semantic_fund_name(normalized)
    if (
        not cleaned
        or all(token in cleaned for token in ("成长", "精选", "股票"))
        or is_generic_style_phrase(cleaned)
    ):
        return None

    registered = _theme_board_match(cleaned)
    if registered is not None:
        return registered

    # 跨市场基金去掉"全球/海外"后，剩下的部分若本身就是纯风格/泛化描述词
    # （如"精选"→"广发全球精选股票(QDII)人民币C"清洗后剩"全球精选"），说明这
    # 段文字并非真实主题，只是"全球+营销描述"的组合，不该直接展示成"全球精选"
    # 这种没有实际含义的伪主题——退回"海外基金"通用兜底更贴切。
    if is_overseas:
        # "股票"只是产品类型描述（跟"混合/指数"一样），不是主题词，只在判断"是否
        # 只是泛化描述"时额外剔除，不影响最终展示文案（仍保留"全球高端制造"这类
        # 完整短语，不会被这里误删）。
        theme_only = re.sub(r"全球|海外", "", cleaned).replace("股票", "")
        if not theme_only or is_generic_style_phrase(theme_only):
            return SemanticSectorCandidate(
                sector_name="海外基金",
                confidence=0.6,
                reason="overseas_generic_fallback",
            )

    # 兜底：清洗后仍是一个具体、非泛化风格的短语时，直接把它当作主题标签展示
    # （对齐养基宝"全球高端制造""科创芯片设计"等做法），不要求命中任何主题白名单，
    # 这样新上传的基金也能自动匹配到合理的关联板块，而不必持续维护白名单。
    # 标记为独立的 semantic_name_freeform 来源（信任度低于持仓穿透/LLM 兜底），
    # 一旦后续有更可靠的来源（重仓行业穿透、LLM 结合持仓判断）算出结果，可以覆盖修正——
    # 避免像"机遇领航"这类清洗后仍残留、但其实只是基金自身营销短语的误判被永久锁死。
    if 2 <= len(cleaned) <= 10 and re.search(r"[\u4e00-\u9fff]", cleaned):
        return SemanticSectorCandidate(
            sector_name=cleaned,
            source="semantic_name_freeform",
            confidence=0.61 if is_overseas else 0.58,
            reason="freeform_theme_phrase",
        )
    return None


def infer_sector_label_from_fund_name(fund_name: str | None) -> str | None:
    """总览 OCR 无关联板块时，从基金名称推断主题短名（如 国防军工混合 → 国防军工）。"""
    if not fund_name:
        return None
    normalized = normalize_sector_label(fund_name.replace("...", ""))
    if not normalized:
        return None
    for token in sorted(_TOPIC_ALIASES, key=len, reverse=True):
        if token in normalized:
            return token
    return None
