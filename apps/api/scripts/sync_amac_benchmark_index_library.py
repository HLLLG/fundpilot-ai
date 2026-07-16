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
    "中证环保产业指数": ("930601", "中证环保"),
    "中证智能电动汽车指数": ("930997", "智能电车"),
    "中信汽车指数": ("817018", None),
    "中证国有企业综合指数": ("000827", "中证国企"),
    "中证民营企业综合指数": ("000828", "中证民企"),
    "中证中央企业综合指数": ("000829", "中证央企"),
    "中证国有企业改革指数": ("399974", "国企改革"),
    "中证地方国有企业综合指数": ("932066", "地方国企"),
    "中证新型基础设施建设主题指数": ("931248", "新基建"),
    "中证龙头企业指数": ("931802", "中证龙头"),
    "中信成长风格指数": ("817002", None),
    "华证价值优选50指数": ("931586", "华证价值50"),
    "中证高股息精选指数": ("932305", "高股息精选"),
    "中证国有企业红利指数": ("000824", "国企红利"),
    "中证港股通医药卫生综合指数": ("931787", "港股通医药"),
    "国证港股通新能源指数": ("987008", "港股通新能源"),
    # 自动匹配易失败或东财命名不一致（已核对 suggest / clist）
    "中证小盘500指数": ("000905", "中证500"),
    "上证科创板综合指数": ("000680", "科创综指"),
    "上证科创板50成份指数": ("000688", "科创50"),
    "中证港股通综合指数": ("930930", "港股综合"),
    "中证TMT产业主题指数": ("000998", "中证TMT"),
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

# 指数名 → 展示板块（仅行业/主题类；宽基/策略留空）
_THEME_OVERRIDES: dict[str, str] = {
    "中证芯片产业指数": "半导体",
    "中证半导体产业指数": "半导体",
    "中证半导体材料设备主题指数": "半导体材料",
    "中证智能电动汽车指数": "新能源车",
    "中证环保产业指数": "环保",
    "中证新型基础设施建设主题指数": "基建",
    "中证国有企业改革指数": "国企改革",
    "国证港股通新能源指数": "新能源",
    "中证港股通医药卫生综合指数": "港股医药",
    "恒生科技指数": "恒生科技",
    "恒生消费指数": "食品饮料",
    "中证港股通高股息投资指数": "红利",
    "中证国有企业红利指数": "红利",
    "恒生高股息率指数": "红利",
    "中证高股息精选指数": "红利",
    "中证港股通工业综合指数": "机械设备",
    "国证港股通资源指数": "有色金属",
    "中证沪港深互联互通TMT指数": "电子",
    "中证港股通TMT主题指数": "电子",
}

_THEME_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("人工智能", "人工智能"),
    ("半导体材料设备", "半导体材料"),
    ("半导体", "半导体"),
    ("芯片", "半导体"),
    ("存储", "存储芯片"),
    ("新能源", "新能源"),
    ("新能源汽车", "新能源车"),
    ("智能电动", "新能源车"),
    ("光伏", "光伏"),
    ("军工", "军工"),
    ("国防军工", "军工"),
    ("医药", "医药"),
    ("医疗", "医疗"),
    ("消费", "食品饮料"),
    ("白酒", "白酒"),
    ("银行", "银行"),
    ("证券", "证券"),
    ("保险", "保险"),
    ("红利", "红利"),
    ("高股息", "红利"),
    ("科技", "电子"),
    ("TMT", "电子"),
    ("互联网", "互联网"),
    ("数字经济", "信创"),
    ("机器人", "机器人"),
    ("智能制造", "机械设备"),
    ("高端装备", "机械设备"),
    ("新材料", "化工"),
    ("资源", "有色金属"),
    ("原材料", "有色金属"),
    ("低碳", "环保"),
    ("环保", "环保"),
    ("港股通", "港股通"),
    ("恒生科技", "恒生科技"),
    ("信息技术", "计算机"),
    ("软件", "软件"),
    ("通信", "通信技术"),
    ("金融", "金融科技"),
    ("工业", "机械设备"),
    ("可选消费", "汽车"),
    ("主要消费", "食品饮料"),
    ("医药卫生", "医药"),
    ("战略新兴", "电子"),
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
    ("国企", "国企改革"),
    ("基建", "基建"),
    ("新基建", "基建"),
    ("龙头", "机械设备"),
    ("汽车", "汽车"),
    ("港股", "港股"),
    ("香港", "港股"),
    ("ESG", "红利"),
    ("自由现金流", "红利"),
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


def _fetch_eastmoney_index_lookup() -> tuple[dict[str, tuple[str, str]], dict[str, tuple[str, str]]]:
    by_name: dict[str, tuple[str, str]] = {}
    by_norm: dict[str, tuple[str, str]] = {}
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
                by_name[name] = (code, name)
                by_norm[_norm(name)] = (code, name)
                by_norm[_norm(name + "指数")] = (code, name)
            if len(batch) < 100:
                break
            pn += 1
            time.sleep(0.12)
    return by_name, by_norm


def _resolve_code(
    full_name: str,
    *,
    by_name: dict[str, tuple[str, str]],
    by_norm: dict[str, tuple[str, str]],
) -> tuple[str | None, str | None, str]:
    if full_name in _MANUAL_INDEX_CODES:
        code, em_name = _MANUAL_INDEX_CODES[full_name]
        return code, em_name, "manual"

    candidates = [full_name, full_name.replace("指数", "")]
    for cand in candidates:
        hit = by_name.get(cand) or by_name.get(cand + "指数")
        if hit:
            return hit[0], hit[1], "auto"

    hit = by_norm.get(_norm(full_name))
    if hit:
        return hit[0], hit[1], "auto"

    nf = _norm(full_name)
    best: tuple[str, str] | None = None
    best_len = 0
    for key, value in by_norm.items():
        if len(key) < 4:
            continue
        if key in nf or nf in key:
            if len(key) > best_len:
                best = value
                best_len = len(key)
    if best:
        return best[0], best[1], "auto_fuzzy"
    return None, None, "unresolved"


def _infer_theme_label(full_name: str, base_type: str) -> str | None:
    if full_name in _THEME_OVERRIDES:
        return _THEME_OVERRIDES[full_name]
    if base_type in ("宽基指数", "策略指数"):
        return None
    for keyword, label in _THEME_KEYWORDS:
        if keyword in full_name:
            return label
    return None


def build_library(*, fetch_eastmoney: bool = True) -> dict:
    entries_raw = _fetch_amac_entries()
    by_name: dict[str, tuple[str, str]] = {}
    by_norm: dict[str, tuple[str, str]] = {}
    if fetch_eastmoney:
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
    args = parser.parse_args()

    library = build_library(fetch_eastmoney=not args.offline)
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
