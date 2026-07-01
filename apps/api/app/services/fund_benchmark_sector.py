from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass

from app.services.amac_benchmark_index_data import (
    amac_name_to_code_pairs,
    amac_theme_label_for_code,
)
from app.services.sector_registry_data import THEME_BOARD_INDEX

logger = logging.getLogger(__name__)

_SUBPROCESS_TIMEOUT = 45

# AkShare 拉取失败时的兜底（业绩基准文案来自公开基金概况，非持仓种子）
_KNOWN_BENCHMARK_BY_CODE: dict[str, str] = {
    "021533": "中证半导体材料设备主题指数收益率×95%+银行活期存款利率（税后）×5%",
}

_INDEX_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")

def _build_benchmark_name_to_code() -> tuple[tuple[str, str], ...]:
    """从 THEME_BOARD_INDEX 生成指数名 → 代码表（长匹配优先）。"""
    pairs: set[tuple[str, str]] = set()
    for label, (_secid, source_code, _kind) in THEME_BOARD_INDEX.items():
        if not source_code or not source_code.isdigit():
            continue
        code = source_code
        pairs.add((label, code))
        if not label.startswith("中证"):
            pairs.add((f"中证{label}", code))
        if "主题" not in label:
            pairs.add((f"{label}主题指数", code))
            pairs.add((f"中证{label}主题指数", code))
    # 历史别名（指数展示名与注册表 label 不完全一致）
    pairs.update(
        {
            ("半导体材料设备主题指数", "931743"),
            ("半导体材料设备", "931743"),
            ("中证半导体材料设备主题指数", "931743"),
            ("中证半导体产业指数", "931865"),
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
    code = index_code.strip().upper()
    for label, (_secid, source_code, _kind) in THEME_BOARD_INDEX.items():
        if source_code and source_code.upper() == code:
            return label
    return amac_theme_label_for_code(code)


def _intraday_index_name_for_label(sector_label: str, index_name: str | None) -> str | None:
    from app.services.fund_profile import infer_intraday_index_from_sector

    inferred = infer_intraday_index_from_sector(sector_label)
    if inferred:
        return inferred
    if index_name and len(index_name) >= 4:
        return index_name
    return None


def parse_benchmark_index(benchmark_text: str) -> BenchmarkIndexMatch | None:
    """从业绩比较基准/跟踪标的文案解析指数代码与名称。"""
    text = (benchmark_text or "").strip()
    if not text:
        return None

    code: str | None = None
    for match in _INDEX_CODE_RE.finditer(text):
        candidate = match.group(1)
        if _index_code_to_sector_label(candidate):
            code = candidate
            break

    index_name: str | None = None
    if code is None:
        for name, mapped_code in _BENCHMARK_NAME_TO_CODE:
            if name in text:
                code = mapped_code
                index_name = name
                break
    else:
        for name, mapped_code in _BENCHMARK_NAME_TO_CODE:
            if mapped_code == code and name in text:
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
            print(json.dumps(str(value).strip(), ensure_ascii=True))
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
            return _KNOWN_BENCHMARK_BY_CODE.get(code)
        raw = completed.stdout.strip()
        if raw == "null":
            return _KNOWN_BENCHMARK_BY_CODE.get(code)
        return json.loads(raw)
    except Exception:
        logger.info("benchmark fetch failed for %s", code, exc_info=True)
        return _KNOWN_BENCHMARK_BY_CODE.get(code)
