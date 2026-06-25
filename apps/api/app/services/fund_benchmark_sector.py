from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass

from app.services.sector_registry_data import THEME_BOARD_INDEX

logger = logging.getLogger(__name__)

_SUBPROCESS_TIMEOUT = 45

_INDEX_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")

# 业绩基准文案中的指数名 → 指数代码（长匹配优先）
_BENCHMARK_NAME_TO_CODE: tuple[tuple[str, str], ...] = tuple(
    sorted(
        [
            ("中证半导体材料设备主题指数", "931743"),
            ("半导体材料设备主题指数", "931743"),
            ("半导体材料设备", "931743"),
            ("中证半导体产业指数", "931865"),
            ("中证半导体", "931865"),
            ("中证人工智能主题指数", "930713"),
            ("中证人工智能", "930713"),
            ("中证电网设备主题指数", "931994"),
            ("中证电网设备", "931994"),
            ("中证新能源指数", "931151"),
            ("中证新能源", "931151"),
        ],
        key=lambda item: len(item[0]),
        reverse=True,
    )
)


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
    return None


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
            return None
        raw = completed.stdout.strip()
        if raw == "null":
            return None
        return json.loads(raw)
    except Exception:
        logger.info("benchmark fetch failed for %s", code, exc_info=True)
        return None
