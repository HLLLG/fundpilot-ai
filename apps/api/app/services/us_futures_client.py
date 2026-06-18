"""美股指数期货数据源 client（AkShare 子进程约定）.

完全复用 ``akshare_subprocess.py`` / ``akshare_spot_client.py`` 的写法：

    - ``subprocess.run([sys.executable, "-c", script], timeout=60)`` 在独立解释器执行
    - 子进程内清理所有代理 / CA 环境变量，确保直连
    - 结果以 ``print(json.dumps(...))`` 打到 stdout
    - 任何异常一律返回 ``None``（绝不抛出，交由上层走降级）

数据源（任务 1.1 GATE 实跑修正，akshare==1.18.64）::

    设计文档拟用的 ``futures_global_em()`` 在 akshare 1.18.64 **不存在**。本环境
    真实期货实时源为 ``futures_global_spot_em()``，美股指数期货以 CME E-mini 命名：
    ``小型纳指当月连续`` (NASDAQ_FUT) / ``小型标普当月连续`` (SP500_FUT) /
    ``小型道指当月连续`` (DOW_FUT)。按「当月连续」优先取主力合约。

    列：序号/代码/名称/最新价/涨跌额/涨跌幅/今开/最高/最低/昨结/...
    —— 用「名称」匹配品种，「最新价」取 last_price，「涨跌幅」取 change_percent。

严禁使用指数/收盘接口（如 ``index_us_stock_sina`` / ``stock_us_*``）作为数值来源
或降级回退（违反需求 1.3 / 7.5）。

公开函数 ``fetch_us_index_futures()`` 返回形如::

    [{"symbol": "NASDAQ_FUT", "display_name": "纳指期货", "last_price": 30459.63,
      "change_percent": 0.48, "quote_time": "2026-06-17T08:12:00-04:00"}]
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_SUBPROCESS_TIMEOUT = 60

US_TZ = ZoneInfo("America/New_York")

# 名称列与数值列（futures_global_spot_em 口径）
_NAME_COL = "名称"
_LAST_PRICE_COL = "最新价"
_CHANGE_PERCENT_COL = "涨跌幅"

# 内部 symbol → 展示名
_DISPLAY_NAMES: dict[str, str] = {
    "NASDAQ_FUT": "纳斯达克",
    "SP500_FUT": "标普500",
    "DOW_FUT": "道琼斯",
}

# 品种匹配关键字（任一命中即算匹配）。akshare 1.18.64 的 futures_global_spot_em
# 以 CME E-mini 命名美股指数期货：小型纳指 / 小型标普 / 小型道指。
# 顺序固定，保证输出列表顺序为 NASDAQ_FUT → SP500_FUT → DOW_FUT。
_FUTURES_TARGETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("NASDAQ_FUT", ("小型纳指", "纳斯达克", "纳指")),
    ("SP500_FUT", ("小型标普", "标普500", "标普")),
    ("DOW_FUT", ("小型道指", "道琼斯", "道指")),
)

# 同一品种存在多个到期月合约时，优先选取「当月连续」主力合约。
_FRONT_MONTH_HINTS = ("当月连续", "连续")


# 子进程脚本：清代理后调用 futures_global_spot_em，以 JSON 输出 columns + records。
_FUTURES_SCRIPT = """
import json, os, sys
# 清除所有代理 / CA 环境变量，确保子进程直连
for key in list(os.environ):
    if "proxy" in key.lower() or "http" in key.lower():
        os.environ.pop(key, None)
os.environ["NO_PROXY"] = "*"
os.environ.pop("REQUESTS_CA_BUNDLE", None)
os.environ.pop("CURL_CA_BUNDLE", None)

try:
    import akshare as ak
    if not hasattr(ak, "futures_global_spot_em"):
        print(json.dumps({"error": "futures_global_spot_em missing in this akshare"}, ensure_ascii=False))
        sys.exit(1)
    frame = ak.futures_global_spot_em()
    if frame is None or getattr(frame, "empty", True):
        print(json.dumps({"error": "empty"}, ensure_ascii=False))
        sys.exit(0)
    columns = [str(c) for c in frame.columns]
    records = json.loads(frame.to_json(orient="records", force_ascii=False))
    print(json.dumps({"columns": columns, "records": records}, ensure_ascii=False))
except Exception as exc:  # noqa: BLE001
    print(json.dumps({"error": str(exc)}, ensure_ascii=False))
    sys.exit(1)
"""


def _to_float(value: object) -> float | None:
    """安全转 float；不可转返回 None。"""
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ("", "nan", "none", "null", "--"):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _row_name(record: dict[str, object]) -> str:
    name = record.get(_NAME_COL)
    if name is not None:
        return str(name)
    for value in record.values():
        if isinstance(value, str):
            return value
    return ""


def _match_symbol(name: str) -> str | None:
    for symbol, keywords in _FUTURES_TARGETS:
        if any(keyword in name for keyword in keywords):
            return symbol
    return None


def _pick_best(candidates: list[dict[str, object]]) -> dict[str, object] | None:
    """优先选「当月连续」主力合约，且其数值（最新价）可转 float。

    退化顺序：含「当月连续/连续」且数值有效 → 任一数值有效 → None。
    """
    valid = [
        cand
        for cand in candidates
        if _to_float(cand.get(_LAST_PRICE_COL)) is not None
    ]
    if not valid:
        return None
    for hint in _FRONT_MONTH_HINTS:
        for cand in valid:
            if hint in _row_name(cand):
                return cand
    return valid[0]


def parse_us_index_futures(
    records: list[dict[str, object]],
    *,
    quote_time: str | None = None,
) -> list[dict[str, object]]:
    """将 futures_global_spot_em 的 records 解析为内部期货报价列表。

    纯函数，便于离线 fixture 校验（不触发子进程）。
    """
    when = quote_time or datetime.now(US_TZ).isoformat()

    # 按 symbol 收集候选行
    buckets: dict[str, list[dict[str, object]]] = {
        symbol: [] for symbol, _ in _FUTURES_TARGETS
    }
    for record in records:
        if not isinstance(record, dict):
            continue
        name = _row_name(record)
        if not name:
            continue
        symbol = _match_symbol(name)
        if symbol is not None:
            buckets[symbol].append(record)

    result: list[dict[str, object]] = []
    for symbol, _ in _FUTURES_TARGETS:
        chosen = _pick_best(buckets[symbol])
        if chosen is None:
            continue
        last_price = _to_float(chosen.get(_LAST_PRICE_COL))
        change_percent = _to_float(chosen.get(_CHANGE_PERCENT_COL))
        if last_price is None:
            continue
        result.append(
            {
                "symbol": symbol,
                "display_name": _DISPLAY_NAMES[symbol],
                "last_price": last_price,
                "change_percent": change_percent,
                "quote_time": when,
            }
        )
    return result


def fetch_us_index_futures() -> list[dict[str, object]] | None:
    """子进程拉取美股指数期货实时行情（小型纳指/标普/道指 当月连续）。

    返回内部报价列表；子进程失败 / 解析异常一律返回 ``None``（交由上层降级）。
    """
    try:
        result = subprocess.run(
            [sys.executable, "-c", _FUTURES_SCRIPT],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("us futures subprocess timeout")
        return None
    except OSError as exc:
        logger.warning("us futures subprocess OSError: %s", exc)
        return None

    if result.returncode != 0 and not result.stdout.strip():
        logger.warning(
            "us futures subprocess failed: rc=%s stderr=%s",
            result.returncode,
            (result.stderr or "")[:200],
        )
        return None
    if not result.stdout.strip():
        logger.warning("us futures subprocess produced no stdout")
        return None

    try:
        payload = json.loads(result.stdout.strip())
    except json.JSONDecodeError as exc:
        logger.warning("us futures subprocess JSON parse failed: %s", exc)
        return None

    if not isinstance(payload, dict) or payload.get("error"):
        logger.debug(
            "us futures source error: %s",
            payload.get("error") if isinstance(payload, dict) else payload,
        )
        return None

    records = payload.get("records")
    if not isinstance(records, list):
        return None

    parsed = parse_us_index_futures(records)
    return parsed
