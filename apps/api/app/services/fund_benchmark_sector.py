from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import sys
from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock

from app.services.amac_benchmark_index_data import amac_name_to_code_pairs
from app.services.sector_registry_data import THEME_BOARD_INDEX

logger = logging.getLogger(__name__)

_SUBPROCESS_TIMEOUT = 45

# AkShare 拉取失败时的兜底（业绩基准文案来自公开基金概况，非持仓种子）
_KNOWN_BENCHMARK_BY_CODE: dict[str, str] = {
    "021533": "中证半导体材料设备主题指数收益率×95%+银行活期存款利率（税后）×5%",
}

_BENCHMARK_FETCH_METADATA: OrderedDict[
    tuple[str, str],
    dict[str, object],
] = OrderedDict()
_BENCHMARK_FETCH_METADATA_MAX_ENTRIES = 512
_BENCHMARK_FETCH_METADATA_LOCK = RLock()

# `fund_individual_basic_info_xq` is an aggregator profile exposed through
# AkShare. It is useful as reference metadata, but it is not a verified
# fund-manager disclosure or contract source.
_XQ_AKSHARE_SOURCE_KIND = "xq_akshare_aggregator"

_INDEX_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")

# 跟踪指数代码 → 展示板块：**人工核过的代理关系**白名单。
#
# 只登记"跟踪指数不是该 label 自身行情码、但属于同一市场同一主题族"的情况。
# 展示板块的涨跌幅会被用户拿去解释自己基金当天的走势，所以代理必须同族：
# 成分不同带来的日偏差通常在 0.3~0.8pp，可接受；跨主题/跨市场代理会到 2pp 以上
# 甚至反向（"中证数字经济主题" 用 "中证信创" 代理时实测日均偏差 1.80pp），不可接受。
#
# 不在本表、且代码又不等于 label 自身行情码时，`_index_code_to_sector_label`
# 一律返回 None（fail-closed）——宁可"暂无可用关联板块"，也不给错的涨跌归因。
_APPROVED_PROXY_INDEX_CODE_TO_SECTOR_LABEL: dict[str, str] = {
    # 周期 / 资源
    "000813": "化工",       # 中证细分化工产业主题
    "399998": "煤炭",       # 中证煤炭
    "399990": "煤炭",       # 中证煤炭等权
    # 医药（A 股医药族，口径宽窄不同但同市场同主题）
    "000841": "医药",       # 中证800制药与生物科技
    "000933": "医药",       # 中证医药卫生
    "000978": "医药",       # 中证医药100
    "000991": "医药",       # 中证全指医药卫生
    "399441": "医药",       # 中证生物医药
    # TMT
    "H30007": "半导体",     # 中证芯片产业
    "931865": "半导体",     # 中证半导体产业（东财简称"中证半导"）
    "932094": "软件",       # 中证全指软件
    "000993": "计算机",     # 中证全指信息技术
    "399970": "互联网",     # 中证移动互联网
    "CN5075": "信创",       # 国证信息技术创新主题
    # 制造 / 新能源
    "930697": "家电",       # 中证全指家用电器
    "399432": "汽车",       # 中证智能汽车主题
    "000097": "机械设备",   # 中证高端装备制造
    "399803": "机械设备",   # 中证工业4.0
    "399976": "新能源车",   # 中证新能源汽车
    "931897": "电力",       # 中证绿色电力
    "930614": "环保",       # 中证环保产业50（本体 000827 的 50 只子集）
    # 金融 / 红利
    "000824": "红利",       # 中证国有企业红利
    "930917": "红利",       # 中证沪港深高股息（含 A 股成分）
    "H30269": "红利",       # 中证红利低波动（东财简称"红利低波"）
    # 商品 / 贵金属
    "931238": "黄金股",     # 中证沪深港黄金产业股票
    "AU9999": "黄金",       # 上金所 Au99.99 现货
    # 跨市场但同主题、且无 A 股替代 label
    "HSSSHID": "创新药",    # 恒生沪深港创新药精选50
}

# 刻意**不**登记的代理（登记会让涨跌归因失真，已实测/核对）：
#   跨市场套 A 股板块：987008 港股通科技 / HSCGSI 恒生消费 / 987024·931027 港股通消费
#                     / 930914 港股通高股息 / H30178 恒生医疗保健
#   宽口径科技冒充电子：000891·932076 战略新兴 / 000998 中证TMT / 399608 科技100
#                     / H30318 科技传媒通信150
#   上位概念冒充细分板块：000989 全指可选消费 / 000942 内地消费主题 / 000988 全指工业
#                       / 000944 内地资源主题 / 931802 中证龙头企业
#   已知错码（东财该代码是别的标的）：483028 深港通信息技术R / 483022 深港通主要消费R
#                                 / 483020 深港通可选消费R / 399675 深互联网
#                                 / H11153 内地国有 / 399262 深证数字经济
#   身份不可采信：930601（实为"中证软件"）/ 000828（实为"300高贝"）
#                / 932066（实为"半导体行业精选"）

def _build_benchmark_name_to_code() -> tuple[tuple[str, str], ...]:
    """从 THEME_BOARD_INDEX 生成指数名 → 代码表（长匹配优先）。"""
    pairs: set[tuple[str, str]] = set()
    for label, (_secid, source_code, _kind) in THEME_BOARD_INDEX.items():
        if not source_code:
            continue
        # 中证行业指数既有纯数字代码，也有 H30022/H30184 等字母前缀代码。
        # BK 板块代码不是基金跟踪指数身份，不能在此冒充 index_code。
        code = source_code.strip().upper()
        if re.fullmatch(r"(?:\d{6}|H[A-Z0-9]+)", code) is None:
            continue
        pairs.add((label, code))
        if not label.startswith("中证"):
            pairs.add((f"中证{label}", code))
        if "主题" not in label:
            pairs.add((f"{label}主题指数", code))
            pairs.add((f"中证{label}主题指数", code))
    # 历史别名（指数展示名与注册表 label 不完全一致）
    pairs.update(
        {
            # 930713（主题）与 931071（产业）必须按完整名称精确区分。
            ("中证人工智能主题指数", "930713"),
            ("人工智能主题指数", "930713"),
            ("中证人工智能产业指数", "931071"),
            ("人工智能产业指数", "931071"),
            ("半导体材料设备主题指数", "931743"),
            ("半导体材料设备", "931743"),
            ("中证半导体材料设备主题指数", "931743"),
            ("中证半导体产业指数", "931865"),
            # Exact tracked-index aliases used by current passive candidates.
            # These identities are research grouping keys only; they do not
            # turn an aggregator profile into a verified fund contract.
            ("恒生沪深港创新药精选50指数", "HSSSHID"),
            ("创新药精选50", "HSSSHID"),
            ("中证香港银行投资指数", "930792"),
            ("香港银行指数", "930792"),
            ("中证绿色电力指数", "931897"),
            ("绿色电力", "931897"),
            ("中证全指电力公用事业指数", "H30199"),
            ("中证全指电力", "H30199"),
            ("恒生科技指数", "HSTECH"),
            ("恒生科技", "HSTECH"),
            # Exact provider index/contract identities observed in passive
            # fund benchmark text.  The code is retained as source_ref while the
            # label is normalized only by
            # _APPROVED_PROXY_INDEX_CODE_TO_SECTOR_LABEL.
            ("中证细分化工产业主题指数", "000813"),
            ("细分化工产业主题指数", "000813"),
            ("中证煤炭等权指数", "399990"),
            ("煤炭等权指数", "399990"),
            ("中证煤炭指数", "399998"),
            ("中证全指家用电器指数", "930697"),
            ("中证沪深港黄金产业股票指数", "931238"),
            ("中证沪港深高股息指数", "930917"),
            ("中证800制药与生物科技指数", "000841"),
            ("国证信息技术创新主题指数", "CN5075"),
            # "中证信息技术应用创新产业指数" 就是中证信创指数(931247)的全称，
            # 缺这条别名会让 10 只信创指数基金被上位概念"计算机"兜走。
            ("中证信息技术应用创新产业指数", "931247"),
            ("信息技术应用创新产业指数", "931247"),
            ("中证信创指数", "931247"),
            ("上海黄金交易所挂盘交易的Au99.99合约", "AU9999"),
            ("上海黄金交易所Au99.99现货实盘合约", "AU9999"),
            ("上海黄金交易所AU99.99", "AU9999"),
            ("黄金现货实盘合约AU99.99", "AU9999"),
            ("黄金现货实盘合约Au9999", "AU9999"),
        }
    )
    for name, code in amac_name_to_code_pairs():
        pairs.add((name, code))
    return tuple(sorted(pairs, key=lambda item: len(item[0]), reverse=True))


_BENCHMARK_NAME_TO_CODE: tuple[tuple[str, str], ...] = _build_benchmark_name_to_code()


@dataclass(frozen=True)
class BenchmarkIndexMatch:
    index_code: str
    index_name: str | None
    benchmark_text: str


def _index_code_to_sector_label(index_code: str) -> str | None:
    """跟踪指数代码 → 展示板块；身份对不上就返回 None。

    只有两种情况可以发出板块：
    1. 代码在 `_APPROVED_PROXY_INDEX_CODE_TO_SECTOR_LABEL`（人工核过的同族代理）；
    2. 代码**就是**该 label 在 THEME_BOARD_INDEX 里的行情码（身份完全一致）。

    历史实现的第三条兜底是 `amac_theme_label_for_code(code)`——直接采信中基协
    库推断出的主题标签，却从不检查"这个标签自己的行情码是不是同一只指数"。
    于是跟踪中证数字经济主题指数的基金拿到了 label"信创"，而页面上显示的涨跌幅
    来自中证信创指数（931247），与基金驱动因素无关：实测日均偏差 1.80pp，
    2026-08-07 甚至方向相反（信创 -0.52% vs 中证数字经济 +1.08%）。

    AMAC 库仍然有用——`merge_amac_into_theme_board_index` 会把它推断出的**新**
    主题连同其自身代码一起注册进 THEME_BOARD_INDEX，那种情况会走上面第 2 条。
    真正被挡掉的只是"AMAC 标签撞上已有 label、但两者是不同指数"这一类冲突。
    """
    code = index_code.strip().upper()
    approved = _APPROVED_PROXY_INDEX_CODE_TO_SECTOR_LABEL.get(code)
    if approved:
        return approved
    for label, (_secid, source_code, _kind) in THEME_BOARD_INDEX.items():
        if source_code and source_code.upper() == code:
            return label
    return None


def _intraday_index_name_for_label(sector_label: str, index_name: str | None) -> str | None:
    from app.services.fund_profile import infer_intraday_index_from_sector

    inferred = infer_intraday_index_from_sector(sector_label)
    if inferred:
        return inferred
    if index_name and len(index_name) >= 4:
        return index_name
    return None


# 市场级标签：只有当指数名整体就是这个宽口径市场时才成立，不能由片段命中得到
# （"港股通" 不是 "港股通科技" 的合理代理）。
_MARKET_LEVEL_SECTOR_LABELS: frozenset[str] = frozenset({"港股", "港股通"})

# 文案里的"<发布机构><主题>指数"短语；用于判断别名命中的是完整指数名还是片段。
# 前缀集合必须与 `_INDEX_PHRASE_PUBLISHER_PREFIX_RE` 完全一致（同样不含"恒生"）：
# 两边口径不一致会让守卫漏判——曾经短语侧剥掉"恒生"得到"港股通高股息低波动"，
# 而别名"恒生港股通"没被剥，于是"不是子串"，恒生港股通高股息低波动基金被标成"港股通"。
_INDEX_NAME_PHRASE_RE = re.compile(
    r"(?:中证|国证|上证|深证|沪深|中债|MSCI|标普|纳斯达克)*"
    r"([\u4e00-\u9fffA-Za-z0-9\.]{2,20}?)指数"
)


# 只剥"纯发布机构"前缀。刻意不含"恒生"：恒生科技 / 恒生消费 里的"恒生"是主题名
# 的一部分，剥掉会把"恒生科技"退化成"科技"，反而制造出新的片段误判。
_INDEX_PHRASE_PUBLISHER_PREFIX_RE = re.compile(
    r"^(?:中证|国证|上证|深证|沪深|中债|MSCI|标普|纳斯达克)+"
)

# 否定限定词：出现在别名之前会把主题反转（"非银行金融" vs "银行"），
# 这类片段命中必须判否。
_NEGATION_QUALIFIERS: tuple[str, ...] = ("非", "不含", "剔除", "除外")

# 市场限定词：出现在指数名里会实质改变可交易成分（不同市场、不同交易时段、
# 不同汇率暴露），所以别名命中后残余里出现这些词，说明命中的是另一个市场的指数。
# 板块 label 的行情标的都是 A 股/境内口径，因此港股与境外市场都要拦。
# 典型误判："费城半导体指数" 会被裸别名 "半导体" 命中，把一只 70% 由美股半导体
# 驱动的 QDII 标成 A 股半导体板块。
_CROSS_MARKET_MARKERS: tuple[str, ...] = (
    # 港股 / 跨境互联互通
    "港股通",
    "沪港深",
    "沪深港",
    "深港通",
    "恒生",
    "香港",
    "H股",
    # 境外市场
    "费城",
    "纳斯达克",
    "纳指",
    "标普",
    "道琼斯",
    "罗素",
    "富时",
    "日经",
    "MSCI",
    "美国",
    "美股",
    "欧洲",
    "德国",
    "法国",
    "日本",
    "韩国",
    "印度",
    "越南",
    "全球",
    "海外",
    "新兴市场",
)


def _normalize_index_phrase(value: str) -> str:
    """去掉发布机构前缀与"指数"后缀，让别名和文案短语可比。

    别名表里既有裸主题名（"数字经济"）也有合成名（"中证数字经济主题指数"），
    不归一化就无法判断命中的是完整指数名还是片段。
    """
    text = str(value or "").strip()
    text = _INDEX_PHRASE_PUBLISHER_PREFIX_RE.sub("", text)
    while text.endswith("指数"):
        text = text[: -len("指数")]
    return text.strip()


def _index_name_phrases(text: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            phrase
            for match in _INDEX_NAME_PHRASE_RE.finditer(text)
            if (phrase := _normalize_index_phrase(match.group(1)))
        )
    )


def _phrases_without_verifiable_identity(phrases: tuple[str, ...]) -> frozenset[str]:
    """找出"整名能对上别名表、但那个代码没有板块归属"的指数短语。

    这类指数（中证科技传媒通信150、中国战略新兴产业成份…）本身通不过身份门槛。
    如果不把它们标出来，`parse_benchmark_index` 会在最具体的别名解析失败后继续往下
    找，退化成用其中一个主题词片段冒名——"科技传媒通信150" 就会被别名 "传媒" 命中。

    注意不能一刀切：同一个短语可能同时对应有 label 的别名（复合基准里的其它成分项），
    只有当**所有**整名别名都没有归属时才封锁这个短语。
    """
    if not phrases:
        return frozenset()
    phrase_set = set(phrases)
    seen: set[str] = set()
    labelled: set[str] = set()
    for name, mapped_code in _BENCHMARK_NAME_TO_CODE:
        normalized = _normalize_index_phrase(name)
        if normalized not in phrase_set:
            continue
        seen.add(normalized)
        if _index_code_to_sector_label(mapped_code):
            labelled.add(normalized)
    return frozenset(seen - labelled)


def _alias_match_is_specific(
    alias: str,
    sector_label: str,
    phrases: tuple[str, ...],
    unverifiable_phrases: frozenset[str] = frozenset(),
) -> bool:
    """别名只是文案里某个更长指数名的片段时判否。

    `_BENCHMARK_NAME_TO_CODE` 里存在大量由板块短名合成的别名（"数字经济"、
    "互联网"、"传媒"、"港股通"…），裸子串匹配会让它们命中完全不同的指数：
        "中证沪港深数字经济主题指数"  命中别名 "数字经济" → 931582（A 股口径）
        "中证港股通科技指数"          命中别名 "港股通"   → H50069（港股通宽基）
        "中证沪港深互联网指数"        命中别名 "互联网"   → H30535（A 股口径）
        "中证科技传媒通信150指数"     命中别名 "传媒"     → H30365（三主题里挑一个）
        "沪深300非银行金融指数"       命中别名 "银行"     → H30022（语义相反）

    别名与短语完整相等时无条件放行。别名只是短语的**真子串**时按四条判否：
      1. 该短语整体就通不过身份门槛（见 `_phrases_without_verifiable_identity`）；
      2. 别名本身是市场级标签（港股 / 港股通）；
      3. 别名前紧邻否定词（"非"银行金融）；
      4. 残余里出现市场限定词（沪港深 / 港股通 / 恒生…）。

    残余是同市场的口径限定或简称补足时仍然放行，因为东财简称本来就比指数全称短：
    "全指房地产" 命中 "房地产"、"云计算与大数据主题" 命中 "云计算" 都是同一只指数。
    """
    normalized_alias = _normalize_index_phrase(alias)
    if not normalized_alias:
        return True
    for phrase in phrases:
        if normalized_alias == phrase or normalized_alias not in phrase:
            continue
        if phrase in unverifiable_phrases:
            return False
        if sector_label in _MARKET_LEVEL_SECTOR_LABELS:
            return False
        position = phrase.find(normalized_alias)
        if any(phrase[:position].endswith(word) for word in _NEGATION_QUALIFIERS):
            return False
        residue = phrase.replace(normalized_alias, "", 1)
        if any(marker in residue for marker in _CROSS_MARKET_MARKERS):
            return False
    return True


# 固收/现金类腿的特征词。整段命中就把该段从"可用于推板块的文本"里剔除：
# 股票板块的涨跌幅不可能是债券或存款腿的驱动因素。
#
# 这一步同时挡掉一整类假阳性——裸标签"银行"会命中"银行间""政策性银行债"
# "中国人民银行"这些机构/市场名，把纯债和理财产品标成银行板块：
#   中证银行50金融债指数 / CFETS银行间绿色债券指数 / 上海清算所银行间1-3年中高等级信用债
#   / 中债-国债及政策性银行债财富 / 七天通知存款利率
_FIXED_INCOME_SEGMENT_TOKENS: tuple[str, ...] = (
    "债",
    "存单",
    "票据",
    "回购",
    "存款",
    "活期",
    "货币",
)

# 各腿权重：支持"指数收益率×95%""95%×指数收益率""*60%"等写法。
_SEGMENT_WEIGHT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def _segment_weight(segment: str) -> float:
    """取该腿的配置权重；没写百分比时按 100 处理（单腿基准）。"""
    matches = _SEGMENT_WEIGHT_RE.findall(segment)
    if not matches:
        return 100.0
    # 指数名里可能带数字（如"科技100""1-3年"），但百分号只出现在权重上。
    return max(float(value) for value in matches)


def _dominant_benchmark_segment(text: str) -> str | None:
    """返回权重最大的非固收腿；全是固收/现金腿时返回 None。

    只看主导腿是刻意的：复合基准里用小权重腿定板块会造成严重误归因，例如
    "费城半导体指数×70%+中证芯片产业指数×20%" 若取 20% 那条腿，就会拿 A 股
    芯片的涨跌去解释一只 70% 由美股半导体驱动的 QDII。主导腿解析不出板块时
    fail-closed，而不是退到次要腿。
    """
    segments = [
        part.strip()
        for part in re.split(r"[+＋]", text)
        if part.strip()
        and not any(token in part for token in _FIXED_INCOME_SEGMENT_TOKENS)
    ]
    if not segments:
        return None
    return max(segments, key=_segment_weight)


def parse_benchmark_index(benchmark_text: str) -> BenchmarkIndexMatch | None:
    """从业绩比较基准/跟踪标的文案解析指数代码与名称。"""
    text = (benchmark_text or "").strip()
    if not text:
        return None

    # 先收敛到主导腿，再做代码/名称匹配：否则固收腿里的指数代码或"银行间"
    # 这类词会抢先命中（历史实现的 `or text` 兜底会把被剔除的整段又拿回来）。
    name_search_text = _dominant_benchmark_segment(text)
    if not name_search_text:
        return None

    code: str | None = None
    for match in _INDEX_CODE_RE.finditer(name_search_text):
        candidate = match.group(1)
        if _index_code_to_sector_label(candidate):
            code = candidate
            break

    phrases = _index_name_phrases(name_search_text)
    unverifiable_phrases = _phrases_without_verifiable_identity(phrases)
    index_name: str | None = None
    if code is None:
        for name, mapped_code in _BENCHMARK_NAME_TO_CODE:
            # AMAC 库也包含“中证全指”之类宽基前缀。若该代码没有明确板块
            # 归属，不能因为它更长就截断搜索；继续寻找同一完整基准文本里的
            # 银行/房地产/食品饮料等可核验行业指数项。但这种"继续往下找"不能
            # 退化成用同一只指数的主题词片段冒名，见 _alias_match_is_specific。
            if name not in name_search_text:
                continue
            label = _index_code_to_sector_label(mapped_code)
            if not label:
                continue
            if not _alias_match_is_specific(
                name,
                label,
                phrases,
                unverifiable_phrases,
            ):
                continue
            code = mapped_code
            index_name = name
            break
    else:
        for name, mapped_code in _BENCHMARK_NAME_TO_CODE:
            if mapped_code == code and name in name_search_text:
                index_name = name
                break

    if code is None:
        return None
    return BenchmarkIndexMatch(index_code=code, index_name=index_name, benchmark_text=text)


def resolve_sector_from_benchmark(
    benchmark_text: str,
) -> tuple[str, str | None, BenchmarkIndexMatch] | None:
    """指数代码 → 展示板块名 + 分时指数名。"""
    match = parse_benchmark_index(benchmark_text)
    if match is None:
        return None
    sector_label = _index_code_to_sector_label(match.index_code)
    if not sector_label:
        return None
    intraday = _intraday_index_name_for_label(sector_label, match.index_name)
    return sector_label, intraday, match


_FREEFORM_INDEX_NAME_RE = re.compile(
    r"(?:中证|国证|上证|深证|沪深300|沪深|MSCI|标普|纳斯达克|中债)?"
    r"([\u4e00-\u9fff]{2,14}?)(?:主题)?指数"
)



# 宽基/固收类指数命名里常见的"限定词"，本身不是行业主题（例如"中债综合指数""中证全债
# 指数""中证港股通综合指数"），出现在抠取结果里基本必然是误判——不像风格停用词那样要求
# 整段短语都是这些词，只要包含其中之一就足以说明抠出来的不是一个真正的板块名。
_BENCHMARK_NOISE_SUBSTRINGS = ("综合", "全债", "存单", "短融")


def extract_freeform_theme_from_benchmark(benchmark_text: str) -> str | None:
    """业绩基准里的标的指数未注册在白名单时，直接从文案抠出主题短语兜底展示。

    只做"能不能展示一个具体主题标签"，不保证有实时行情——没有白名单命中就没有涨跌%，
    但至少不会因为指数没注册而把整条记录丢弃（对齐养基宝对生僻/新主题基金的处理）。
    """
    text = (benchmark_text or "").strip()
    if not text:
        return None
    from app.services.sector_labels import is_generic_style_phrase

    for match in _FREEFORM_INDEX_NAME_RE.finditer(text):
        phrase = match.group(1).strip()
        if not phrase or len(phrase) < 2 or len(phrase) > 12:
            continue
        if is_generic_style_phrase(phrase):
            continue
        if any(noise in phrase for noise in _BENCHMARK_NOISE_SUBSTRINGS):
            continue
        return phrase
    return None


def fetch_fund_benchmark_text(fund_code: str) -> str | None:
    """拉取基金业绩比较基准原文（子进程 AkShare，失败返回 None）。"""
    code = fund_code.strip().zfill(6)
    if len(code) != 6:
        return None
    script = r"""
import json
import sys
import akshare as ak

code = sys.argv[1]
try:
    frame = ak.fund_individual_basic_info_xq(symbol=code)
except Exception:
    print("null")
    raise SystemExit(0)
if frame is None or frame.empty:
    print("null")
    raise SystemExit(0)
for _, row in frame.iterrows():
    item = str(row.get("item", "")).strip()
    if "业绩比较基准" in item or "跟踪标的" in item or item == "标的指数":
        value = row.get("value")
        if value is not None and str(value).strip():
            kind = "performance_benchmark" if "业绩比较基准" in item else "tracking_target"
            print(json.dumps({"text": str(value).strip(), "kind": kind}, ensure_ascii=True))
            raise SystemExit(0)
print("null")
"""
    try:
        completed = subprocess.run(
            [sys.executable, "-c", script, code],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            return _static_benchmark_fallback(code)
        raw = completed.stdout.strip()
        if raw == "null":
            return _static_benchmark_fallback(code)
        decoded = json.loads(raw)
        if isinstance(decoded, dict):
            text = str(decoded.get("text") or "").strip()
            kind = str(decoded.get("kind") or "unknown").strip()
        else:
            # Compatibility with an older subprocess payload.  Unknown field
            # provenance is intentionally not eligible for a formal benchmark.
            text = str(decoded or "").strip()
            kind = "unknown"
        if not text:
            return _static_benchmark_fallback(code)
        _remember_benchmark_fetch_metadata(
            code,
            text,
            kind=kind,
            source_kind=_XQ_AKSHARE_SOURCE_KIND,
        )
        return text
    except Exception:
        logger.info("benchmark fetch failed for %s", code, exc_info=True)
        return _static_benchmark_fallback(code)


def get_fund_benchmark_fetch_metadata(
    fund_code: str,
    benchmark_text: str,
) -> dict[str, object]:
    code = fund_code.strip().zfill(6)
    text = str(benchmark_text or "").strip()
    key = (code, text)
    with _BENCHMARK_FETCH_METADATA_LOCK:
        metadata = _BENCHMARK_FETCH_METADATA.get(key)
        if metadata is not None:
            _BENCHMARK_FETCH_METADATA.move_to_end(key)
            return dict(metadata)
    return {
        "benchmark_text_kind": "unknown",
        "benchmark_text_source_kind": "unknown",
        "benchmark_text_length": len(text),
        "benchmark_text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "benchmark_text_truncated": False,
    }


def _remember_benchmark_fetch_metadata(
    code: str,
    text: str,
    *,
    kind: str,
    source_kind: str,
) -> None:
    key = (code, text)
    metadata = {
        "benchmark_text_kind": kind,
        "benchmark_text_source_kind": source_kind,
        "benchmark_text_length": len(text),
        "benchmark_text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "benchmark_text_truncated": False,
    }
    with _BENCHMARK_FETCH_METADATA_LOCK:
        _BENCHMARK_FETCH_METADATA[key] = metadata
        _BENCHMARK_FETCH_METADATA.move_to_end(key)
        while (
            len(_BENCHMARK_FETCH_METADATA)
            > _BENCHMARK_FETCH_METADATA_MAX_ENTRIES
        ):
            _BENCHMARK_FETCH_METADATA.popitem(last=False)


def _static_benchmark_fallback(code: str) -> str | None:
    text = _KNOWN_BENCHMARK_BY_CODE.get(code)
    if text:
        _remember_benchmark_fetch_metadata(
            code,
            text,
            kind="performance_benchmark",
            source_kind="static_fallback",
        )
    return text
