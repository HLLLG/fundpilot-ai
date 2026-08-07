#!/usr/bin/env python3
"""从中基协 API 同步 155 指数要素库，解析东财代码并生成静态 JSON。

用法（在 apps/api 下）：
    python scripts/sync_amac_benchmark_index_library.py
    python scripts/sync_amac_benchmark_index_library.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlencode, quote

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

OUTPUT_PATH = API_ROOT / "app" / "data" / "amac_benchmark_index_library.json"

_CTX = ssl.create_default_context()

# AMAC 官方名称 → 东财检索/别名（自动匹配失败时使用）
_MANUAL_INDEX_CODES: dict[str, tuple[str, str | None]] = {
    "北证50成份指数": ("899050", "北证50"),
    "恒生指数": ("HSI", "恒生指数"),
    "恒生综合指数": ("HSCI", "恒生综合"),
    "恒生科技指数": ("HSTECH", "恒生科技"),
    "恒生消费指数": ("HSCGSI", "恒生消费"),
    "恒生中国企业指数": ("HSCEI", "恒生中国企业"),
    "恒生高股息率指数": ("HSHDYI", "恒生高股息率"),
    "中证全指原材料指数": ("000987", "中证全指原材料"),
    "中证全指主要消费指数": ("000990", "中证全指主要消费"),
    "中证医药卫生指数": ("000933", "800医卫"),
    "中国战略新兴产业成份指数": ("000891", "战略新兴"),
    "中国战略新兴产业综合指数": ("932076", "战略新兴100"),
    "中证新兴产业指数": ("930050", "中证新兴"),
    "中证全指软件指数": ("932094", "中证软件"),
    "中证服务业指数": ("931008", "中证服务"),
    "申银万国消费品指数": ("000103", "申万消费"),
    "中信消费风格指数": ("817001", None),
    "中证高端制造主题指数": ("931066", "制造龙头"),
    "中证中游制造产业指数": ("931468", "中游制造"),
    "申银万国制造业指数": ("801130", None),
    "申万国防军工指数": ("801740", None),
    # 930601 在东财实际是"中证软件"，与环保无关（缓存表与实时接口一致）。
    # 中证环保产业指数的真实行情码是 000827，东财简称"中证环保"。
    "中证环保产业指数": ("000827", "中证环保"),
    "中证智能电动汽车指数": ("930997", "智能电车"),
    "中信汽车指数": ("817018", None),
    # 以下三条曾人工写死，但实测代码都指向别的标的，已移除（宁可 unresolved）：
    #   中证国有企业综合指数 -> 000827   实际是"中证环保"（与上面的环保产业撞码）
    #   中证民营企业综合指数 -> 000828   实际是"300高贝"
    #   中证地方国有企业综合指数 -> 932066 实际是"半导体行业精选"
    # 三者 base_type 都是宽基/策略，theme_label 恒为 None，移除不影响板块解析。
    "中证中央企业综合指数": ("000829", "中证央企"),
    "中证国有企业改革指数": ("399974", "国企改革"),
    "中证新型基础设施建设主题指数": ("931248", "新基建"),
    "中证龙头企业指数": ("931802", "中证龙头"),
    "中信成长风格指数": ("817002", None),
    "华证价值优选50指数": ("931586", "华证价值50"),
    "中证高股息精选指数": ("932305", "高股息精选"),
    "中证国有企业红利指数": ("000824", "国企红利"),
    "中证港股通医药卫生综合指数": ("931787", "港股通医药"),
    # 987008 是"港股通科技"，不是港股通新能源；旧值让 22 只跟踪中证港股通科技
    # 指数的基金被错标成"新能源"。国证港股通新能源的东财代码是 987026。
    "国证港股通新能源指数": ("987026", "港股通新能源"),
    "中证港股通科技指数": ("931573", "港股通科技"),
    # 宽基指数：东财简称过短（"沪深300"→归一化只剩"300"），单向前缀匹配够不到，
    # 且同名候选很多（深证300 / 国证300），必须固定身份。
    "沪深300指数": ("000300", "沪深300"),
    "中证1000指数": ("000852", "中证1000"),
    "深证成份指数": ("399001", "深证成指"),
    "深证综合指数": ("399106", "深证综指"),
    "创业板指数": ("399006", "创业板指"),
    "上证综合指数": ("000001", "上证指数"),
    # 自动匹配易失败或东财命名不一致（已核对 suggest / clist）
    "中证小盘500指数": ("000905", "中证500"),
    "上证科创板综合指数": ("000680", "科创综指"),
    "上证科创板50成份指数": ("000688", "科创50"),
    "中证港股通综合指数": ("930930", "港股综合"),
    "中证TMT产业主题指数": ("000998", "中证TMT"),
    # 东财搜索“人工智能”会优先返回 931071（产业指数），但 AMAC 此处是
    # 中证人工智能主题指数 930713，必须固定精确身份，避免后续同步回归。
    "中证人工智能主题指数": ("930713", "中证人工智能"),
    "中证新能源汽车指数": ("399976", "CS新能车"),
    "中证新材料主题指数": ("H30597", "新材料"),
    "中证中小盘700指数": ("000907", "中证700"),
    "中证全指半导体产品与设备指数": ("H30184", "半导体"),
    "中证沪港深创新药产业指数": ("931409", "SHS创新药"),
    "沪深300碳中和指数": ("931755", "SEEE碳中和"),
    "中证内地新能源主题指数": ("000941", "内地新能源"),
    "中证新能源产业指数": ("930997", "CS新能车"),
    "中证长三角领先指数": ("931559", "长三角领先"),
    "中证长三角龙头企业指数": ("931381", "长三角龙头"),
    "中证粤港澳大湾区发展主题指数": ("931000", "大湾区"),
    # 东财 clist 未收录、需对照中证/国证官网（2026-06 核对）
    "中证港股通工业综合指数": ("930962", "港股通工业"),
    "国证港股通资源指数": ("980106", "港股通资源"),
    "中证800成长指数": ("H30355", "800成长"),
    "中证800质量指数": ("932433", "800质量"),
    "中证800等权重指数": ("000842", "800等权"),
    "中证沪港深互联互通TMT指数": ("H30552", "互联互通TMT"),
    "中证港股通TMT主题指数": ("931026", "港股通TMT"),
    "中证800相对成长指数": ("H30357", "800R成长"),
    "中证800 ESG基准指数": ("931650", "800ESG"),
}

# 指数名 → 展示板块（仅行业/主题类；宽基/策略留空）。
#
# 这里只登记"关键词推不出、但主题明确"的 A 股/沪港深指数。跨市场指数
# （港股通 / 恒生 / 沪港深 / 深港通）**不要**在此映射到 A 股板块——
# `_infer_theme_label` 的跨市场守卫会把它们一律置空，登记了也不会生效。
# 曾经登记过并已移除的错例：
#   恒生消费指数 → 食品饮料           （港股消费 ≠ A 股食品饮料，12 只基金受影响）
#   中证港股通高股息投资指数 → 红利     （港股高息 ≠ A 股中证红利，40 只基金受影响）
#   国证港股通新能源指数 → 新能源
#   中证港股通工业综合指数 → 机械设备
#   国证港股通资源指数 → 有色金属
#   中证沪港深互联互通TMT指数 / 中证港股通TMT主题指数 → 电子
_THEME_OVERRIDES: dict[str, str] = {
    "中证芯片产业指数": "半导体",
    "中证半导体产业指数": "半导体",
    "中证半导体材料设备主题指数": "半导体材料",
    "中证智能电动汽车指数": "新能源车",
    "中证环保产业指数": "环保",
    "中证新型基础设施建设主题指数": "基建",
    "中证国有企业改革指数": "国企改革",
    # 工业自动化主题，归机械设备；不能靠裸"工业"关键词（会误收中证全指工业宽基）。
    "中证工业4.0指数": "机械设备",
    "中证国有企业红利指数": "红利",
    "中证高股息精选指数": "红利",
    # 港股原生标签：不与 A 股板块争行情代理，可保留。
    "中证港股通医药卫生综合指数": "港股医药",
    "恒生科技指数": "恒生科技",
}

# 指数名关键词 → 展示板块。
#
# 匹配规则见 `_infer_theme_label`：**按关键词长度降序**匹配，最长命中优先。
# 不要依赖书写顺序——历史上本表是"首个包含即返回"，导致 ("新能源") 抢在
# ("新能源汽车") 之前命中，把中证新能源汽车指数错标成"新能源"。
#
# 只登记"关键词即主题"的映射。以下几类**故意不登记**，宁可返回 None 让
# 基准链路 fail-closed，也不给一个会误导涨跌幅归因的标签：
#   - 宽基/风格限定词：龙头、工业、科技、战略新兴、TMT、ESG、自由现金流
#   - 上位概念≠具体板块：消费（可选/主要消费成分差异极大）、资源、原材料、新材料
#   - 跨市场：港股通/恒生 系列指数不能用 A 股板块当涨跌代理（成分与交易时段都不同）
_THEME_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("金融科技", "金融科技"),
    ("人工智能", "人工智能"),
    ("半导体材料设备", "半导体材料"),
    ("半导体", "半导体"),
    ("芯片", "半导体"),
    ("存储", "存储芯片"),
    ("新能源汽车", "新能源车"),
    ("智能电动", "新能源车"),
    ("新能源", "新能源"),
    ("光伏", "光伏"),
    ("国防军工", "军工"),
    ("军工", "军工"),
    ("医药卫生", "医药"),
    ("医药", "医药"),
    ("医疗", "医疗"),
    ("白酒", "白酒"),
    ("银行", "银行"),
    ("证券", "证券"),
    ("保险", "保险"),
    ("高股息", "红利"),
    ("红利", "红利"),
    ("互联网", "互联网"),
    ("数字经济", "数字经济"),
    ("机器人", "机器人"),
    ("智能制造", "机械设备"),
    ("高端装备", "机械设备"),
    ("低碳", "环保"),
    ("环保", "环保"),
    ("恒生科技", "恒生科技"),
    ("信息技术", "计算机"),
    ("软件", "软件"),
    # 不用裸"通信"：会把"科技传媒通信150"（TMT 宽口径）错标成通信技术。
    ("通信服务", "通信技术"),
    ("通信设备", "通信技术"),
    ("储能", "储能"),
    ("锂电池", "锂电池"),
    ("风电", "风电"),
    ("氢能", "氢能"),
    ("电力", "电力"),
    ("煤炭", "煤炭"),
    ("钢铁", "钢铁"),
    ("房地产", "房地产"),
    ("农业", "农业"),
    ("畜牧", "畜牧养殖"),
    ("动漫", "动漫游戏"),
    ("游戏", "动漫游戏"),
    ("国企改革", "国企改革"),
    ("新基建", "基建"),
    ("基建", "基建"),
    ("智能汽车", "汽车"),
    ("汽车", "汽车"),
)

# 跨市场指数名特征：命中后不再套用 A 股板块标签。
# 港股通/沪港深/恒生/香港 系列与 A 股板块的成分、交易时段、汇率暴露都不同，
# 用 A 股板块当它们的涨跌代理会把差异当成基金自身的走势。
_CROSS_MARKET_MARKERS: tuple[str, ...] = (
    "港股通",
    "沪港深",
    "沪深港",
    "深港通",
    "恒生",
    "香港",
    "H股",
)

# 允许保留跨市场标签的例外：标签本身就是港股主题，不存在 A 股代理混淆。
_CROSS_MARKET_NATIVE_LABELS: frozenset[str] = frozenset(
    {"港股", "恒生科技", "港股通", "港股医药", "港股银行"}
)


def _secid_for(code: str) -> str:
    c = code.strip().upper()
    if re.fullmatch(r"\d{6}", c):
        if c.startswith("980") or c.startswith("981") or c.startswith("982"):
            return f"0.{c}"
        if c.startswith("93") or c.startswith("95"):
            return f"2.{c}"
        if c.startswith("399"):
            return f"0.{c}"
        if c.startswith("0"):
            return f"1.{c}"
        return f"2.{c}"
    if re.fullmatch(r"H[A-Z0-9]+", c):
        return f"2.{c}"
    return f"2.{c}"


def _norm(name: str) -> str:
    s = re.sub(r"\s+", "", name or "")
    s = s.replace("指数", "").replace("成份", "成分")
    for prefix in ("沪深", "上证", "深证", "中证", "国证", "恒生", "申银万国", "申万", "中信"):
        s = s.replace(prefix, "")
    return s


def _fetch_amac_entries() -> list[dict]:
    items: list[dict] = []
    for tier in ("oneClass", "twoClass"):
        url = (
            "https://www.amac.org.cn/portal/front/performance/comparison/getFundPage"
            f"?pageSize=200&pageNo=1&type={tier}"
        )
        with urllib.request.urlopen(url, context=_CTX, timeout=30) as resp:
            payload = json.load(resp)
        for row in payload["data"]["data"]["dataList"]:
            items.append(
                {
                    "tier": tier,
                    "base_type": row["baseType"],
                    "market_type": row["marketType"],
                    "index_full_name": row["indexFullName"],
                    "update_time": row.get("updateTime"),
                }
            )
    return items


def _em_clist(fs: str, *, pn: int = 1, pz: int = 100) -> list[dict]:
    params = {
        "np": 1,
        "fltt": 1,
        "invt": 2,
        "wbp2u": "|0|0|0|web",
        "fid": "f3",
        "fs": fs,
        "fields": "f12,f14",
        "pn": pn,
        "pz": pz,
        "po": 1,
        "dect": 1,
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "_": int(time.time() * 1000),
    }
    url = "https://push2.eastmoney.com/api/qt/clist/get?" + urlencode(params)
    with urllib.request.urlopen(url, context=_CTX, timeout=30) as resp:
        payload = json.load(resp)
    return payload.get("data", {}).get("diff") or []


# 指数发布机构 → 该机构指数在东财的"原生"代码形态。
# 东财对不同机构的同名指数会给出多个代码（如"数字经济"同时是深证 399262 与
# 中证 931582），仅靠名字反查必然歧义。出现歧义时按发布机构的原生命名空间取值。
_PUBLISHER_NATIVE_CODE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # 中证指数公司：93xxxx / 95xxxx / Hxxxxx 为原生；部分中证指数由深交所挂牌
    # 显示为 399xxx（如 399967 中证军工），无歧义时仍然接受。
    ("中证", (r"9[35][0-9]{4}", r"H[0-9A-Z]+")),
    ("国证", (r"98[0-9]{4}",)),
    ("深证", (r"399[0-9]{3}",)),
    ("创业板", (r"399[0-9]{3}",)),
    ("上证", (r"000[0-9]{3}",)),
    ("沪深", (r"000[0-9]{3}", r"399[0-9]{3}")),
    ("恒生", (r"HS[0-9A-Z]*",)),
)


def _publisher_native_patterns(full_name: str) -> tuple[str, ...]:
    for prefix, patterns in _PUBLISHER_NATIVE_CODE_PATTERNS:
        if full_name.startswith(prefix):
            return patterns
    return ()


def _prefer_native_namespace(
    full_name: str,
    candidates: list[tuple[str, str]],
) -> tuple[str, str] | None:
    """多个同名候选时按发布机构原生命名空间收敛；仍不唯一则返回 None。"""
    unique = list(dict.fromkeys(candidates))
    if len(unique) == 1:
        return unique[0]
    patterns = _publisher_native_patterns(full_name)
    if not patterns:
        return None
    native = [
        item
        for item in unique
        if any(re.fullmatch(pattern, item[0].upper()) for pattern in patterns)
    ]
    return native[0] if len(native) == 1 else None


def _fetch_eastmoney_index_lookup() -> tuple[
    dict[str, list[tuple[str, str]]], dict[str, list[tuple[str, str]]]
]:
    """东财指数名 → 候选 (code, name) 列表。

    刻意保留**全部**同名候选，不做 last-wins 覆盖：历史实现按
    ``by_name[name] = (code, name)`` 写入，而遍历顺序是 m:2 → m:1 → m:0，
    于是深证命名空间会静默覆盖中证命名空间，"中证数字经济主题指数"被解析成
    深证 399262。歧义必须交给 `_prefer_native_namespace` 显式收敛。
    """
    by_name: dict[str, list[tuple[str, str]]] = {}
    by_norm: dict[str, list[tuple[str, str]]] = {}

    def add(bucket: dict[str, list[tuple[str, str]]], key: str, value: tuple[str, str]) -> None:
        if not key:
            return
        entries = bucket.setdefault(key, [])
        if value not in entries:
            entries.append(value)

    for fs in ("m:2", "m:1+t:1", "m:0+t:5"):
        pn = 1
        while True:
            batch = _em_clist(fs, pn=pn)
            if not batch:
                break
            for row in batch:
                code = str(row.get("f12", "")).strip()
                name = str(row.get("f14", "")).strip()
                if not code or not name:
                    continue
                add(by_name, name, (code, name))
                add(by_norm, _norm(name), (code, name))
                add(by_norm, _norm(name + "指数"), (code, name))
            if len(batch) < 100:
                break
            pn += 1
            time.sleep(0.12)
    return by_name, by_norm


def _resolve_code(
    full_name: str,
    *,
    by_name: dict[str, list[tuple[str, str]]],
    by_norm: dict[str, list[tuple[str, str]]],
) -> tuple[str | None, str | None, str]:
    """指数全称 → 东财代码；无法唯一确定时返回 unresolved（fail-closed）。

    模糊匹配要求归一化后两个名字**互为前缀**，而不是任意子串包含。旧的子串
    规则会把父子/跨市场指数族混作同一个代码，例如：
      "中证沪港深数字经济主题指数" ← "数字经济"(399262)
      "中证信息技术指数"          ← "深港通信息技术R（港币）"(483028)
      "中证香港内地国有企业指数"    ← "内地国有"(H11153)
    前缀约束能挡掉这三类，同时保留 "全指信息" → "全指信息技术" 这种真命中。
    """
    if full_name in _MANUAL_INDEX_CODES:
        code, em_name = _MANUAL_INDEX_CODES[full_name]
        return code, em_name, "manual"

    for cand in (full_name, full_name.replace("指数", "")):
        for key in (cand, cand + "指数"):
            hit = _prefer_native_namespace(full_name, by_name.get(key, []))
            if hit:
                return hit[0], hit[1], "auto"

    nf = _norm(full_name)
    hit = _prefer_native_namespace(full_name, by_norm.get(nf, []))
    if hit:
        return hit[0], hit[1], "auto"

    # 只接受"东财简称是 AMAC 全称的前缀"这一个方向：AMAC 名字更具体是正常的
    # （"全指信息" ⊂ "全指信息技术"），反方向说明东财那个标的比 AMAC 指数更具体，
    # 必然是另一只指数（"300" → "300成长"、"成分" → "成份Ｂ指"）。
    if len(nf) < 4:
        return None, None, "unresolved"
    prefix_matches: list[tuple[str, str]] = []
    best_len = 0
    for key, values in by_norm.items():
        if len(key) < 4 or not nf.startswith(key):
            continue
        if len(key) > best_len:
            prefix_matches = list(values)
            best_len = len(key)
        elif len(key) == best_len:
            prefix_matches.extend(values)
    hit = _prefer_native_namespace(full_name, prefix_matches)
    if hit:
        return hit[0], hit[1], "auto_prefix"
    return None, None, "unresolved"


def _infer_theme_label(full_name: str, base_type: str) -> str | None:
    """指数全称 → 展示板块短名；推不出可靠主题时返回 None（fail-closed）。

    三道约束，缺一不可：
    1. 最长关键词优先（避免"新能源"抢在"新能源汽车"之前命中）；
    2. 跨市场指数不套用 A 股板块标签；
    3. 结果必须在 THEME_BOARD_WHITELIST 里，否则下游 merge 会静默丢弃、
       而 benchmark 链路却已经把它当成合法板块名。
    """
    from app.services.sector_registry_data import THEME_BOARD_WHITELIST

    label = _THEME_OVERRIDES.get(full_name)
    if label is None:
        if base_type in ("宽基指数", "策略指数"):
            return None
        for keyword, candidate in sorted(
            _THEME_KEYWORDS, key=lambda item: (-len(item[0]), item[0])
        ):
            if keyword in full_name:
                label = candidate
                break
    if label is None:
        return None
    if label not in THEME_BOARD_WHITELIST:
        return None
    if label in _CROSS_MARKET_NATIVE_LABELS:
        return label
    if any(marker in full_name for marker in _CROSS_MARKET_MARKERS):
        return None
    return label


def _amac_entries_from_cache(path: Path) -> list[dict]:
    """从已生成的库文件里取回 AMAC 要素清单（不含解析结果）。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    items: list[dict] = []
    for entry in payload.get("entries", []):
        name = str(entry.get("index_full_name") or "").strip()
        if not name:
            continue
        items.append(
            {
                "tier": entry.get("tier"),
                "base_type": entry.get("base_type"),
                "market_type": entry.get("market_type"),
                "index_full_name": name,
                "update_time": entry.get("update_time"),
            }
        )
    return items


def _eastmoney_lookup_from_cache(
    path: Path,
) -> tuple[dict[str, list[tuple[str, str]]], dict[str, list[tuple[str, str]]]]:
    """用缓存的 东财 code→name 表重建候选索引，用于离线确定性重算。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    by_name: dict[str, list[tuple[str, str]]] = {}
    by_norm: dict[str, list[tuple[str, str]]] = {}

    def add(bucket: dict[str, list[tuple[str, str]]], key: str, value: tuple[str, str]) -> None:
        if not key:
            return
        entries = bucket.setdefault(key, [])
        if value not in entries:
            entries.append(value)

    for code, name in sorted(raw.items()):
        code = str(code or "").strip()
        name = str(name or "").strip()
        if not code or not name:
            continue
        add(by_name, name, (code, name))
        add(by_norm, _norm(name), (code, name))
        add(by_norm, _norm(name + "指数"), (code, name))
    return by_name, by_norm


def build_library(
    *,
    fetch_eastmoney: bool = True,
    em_lookup_cache: Path | None = None,
    amac_cache: Path | None = None,
) -> dict:
    entries_raw = (
        _amac_entries_from_cache(amac_cache)
        if amac_cache is not None
        else _fetch_amac_entries()
    )
    by_name: dict[str, list[tuple[str, str]]] = {}
    by_norm: dict[str, list[tuple[str, str]]] = {}
    if em_lookup_cache is not None:
        by_name, by_norm = _eastmoney_lookup_from_cache(em_lookup_cache)
    elif fetch_eastmoney:
        by_name, by_norm = _fetch_eastmoney_index_lookup()

    entries: list[dict] = []
    unresolved: list[str] = []
    update_time = entries_raw[0].get("update_time") if entries_raw else None

    for item in entries_raw:
        full_name = item["index_full_name"]
        code, em_name, resolution = _resolve_code(
            full_name, by_name=by_name, by_norm=by_norm
        )
        theme_label = _infer_theme_label(full_name, item["base_type"])
        if code is None:
            unresolved.append(full_name)
            entries.append(
                {
                    **item,
                    "source_code": None,
                    "eastmoney_secid": None,
                    "eastmoney_name": None,
                    "theme_label": theme_label,
                    "resolution": resolution,
                }
            )
            continue
        entries.append(
            {
                **item,
                "source_code": code.upper(),
                "eastmoney_secid": _secid_for(code),
                "eastmoney_name": em_name,
                "theme_label": theme_label,
                "resolution": resolution,
            }
        )

    return {
        "version": update_time or "unknown",
        "source": "amac",
        "total": len(entries),
        "resolved": sum(1 for e in entries if e.get("source_code")),
        "unresolved": unresolved,
        "entries": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="同步中基协指数要素库到静态 JSON")
    parser.add_argument("--dry-run", action="store_true", help="不写文件，仅打印摘要")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="跳过东财拉取，仅使用手工映射（用于 CI）",
    )
    parser.add_argument(
        "--em-lookup-cache",
        type=Path,
        default=None,
        help="用缓存的东财 code→name JSON 重建候选索引（离线确定性重算）",
    )
    parser.add_argument(
        "--amac-cache",
        type=Path,
        default=None,
        help="从已生成的库文件读取 AMAC 要素清单，跳过中基协接口",
    )
    args = parser.parse_args()

    library = build_library(
        fetch_eastmoney=not args.offline,
        em_lookup_cache=args.em_lookup_cache,
        amac_cache=args.amac_cache,
    )
    print(
        f"AMAC entries={library['total']} resolved={library['resolved']} "
        f"unresolved={len(library['unresolved'])}"
    )
    if library["unresolved"]:
        print("Unresolved:", ", ".join(library["unresolved"][:10]))
        if len(library["unresolved"]) > 10:
            print(f"  ... and {len(library['unresolved']) - 10} more")

    if args.dry_run:
        return 0

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(library, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
