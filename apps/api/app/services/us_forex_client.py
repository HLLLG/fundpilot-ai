"""USD/CNY 人民币汇率（AkShare 子进程约定）。

复用 ``akshare_subprocess.py`` / ``us_futures_client.py`` 的写法：在独立子进程中
清代理后调用 AkShare，结果以 JSON 打到 stdout，异常一律返回 ``None``。

GATE 实跑修正（akshare==1.18.64，见 tasks.md 任务 1.1 结论）::

    ``fx_quote_baidu(symbol="美元")`` 在本环境持续返回上游 HTTP 403，已从运行时
    链路移除，避免每次刷新都产生一次确定失败。本环境真实可达的主选为
    ``currency_boc_safe()``（外管局中间价日频，与竞品「汇率」口径一致），其次
    ``currency_boc_sina(symbol="美元", start_date=..., end_date=...)``；数值单位为「分」（如 ``680.96`` →
    ``6.8096`` CNY/USD，需除以 100）。

**硬约束（需求 1.2 / 7.5）：** 禁止填占位常量；数值仅来自真实采集，采集失败返回
``None`` 交由上层走降级。
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_PRIMARY_SOURCE_TIMEOUT = 11.5
_FALLBACK_SOURCE_TIMEOUT = 3.0

# 子进程内清代理的统一前导（与 us_futures_client / diagnose_us_market 一致）。
_CHILD_PREAMBLE = """
import json, os, sys
for key in list(os.environ):
    if "proxy" in key.lower() or "http" in key.lower():
        os.environ.pop(key, None)
os.environ["NO_PROXY"] = "*"
os.environ.pop("REQUESTS_CA_BUNDLE", None)
os.environ.pop("CURL_CA_BUNDLE", None)


def _emit_frame(frame):
    if frame is None or getattr(frame, "empty", True):
        print(json.dumps({"error": "empty"}, ensure_ascii=False))
        return
    columns = [str(c) for c in frame.columns]
    records = json.loads(frame.to_json(orient="records", force_ascii=False))
    print(json.dumps({"columns": columns, "records": records}, ensure_ascii=False))
"""

# 主选：外管局中间价（日频，数据更新至近期，对标小倍「汇率」）。
_BOC_SAFE_SCRIPT = _CHILD_PREAMBLE + """
try:
    import akshare as ak
    frame = ak.currency_boc_safe()
    _emit_frame(frame)
except Exception as exc:  # noqa: BLE001
    print(json.dumps({"error": str(exc)}, ensure_ascii=False))
    sys.exit(1)
"""

# 备选 2：中行人民币牌价（日频历史序列）。AkShare 的默认日期固定在 2023，
# 必须显式传入滚动窗口，否则会把历史值误当成最新汇率。
_BOC_SINA_SCRIPT = _CHILD_PREAMBLE + """
try:
    import akshare as ak
    from datetime import date, timedelta
    end = date.today()
    start = end - timedelta(days=45)
    frame = ak.currency_boc_sina(
        symbol="美元",
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
    )
    _emit_frame(frame)
except Exception as exc:  # noqa: BLE001
    print(json.dumps({"error": str(exc)}, ensure_ascii=False))
    sys.exit(1)
"""

# ---------------------------------------------------------------------------
# 列名候选
# ---------------------------------------------------------------------------

# currency_boc_sina：中行牌价列名。折算价为我们采用的「中间价」口径。
_BOC_DATE_COL = "日期"
_BOC_CONVERT_COL = "中行折算价"
_BOC_SAFE_USD_COL = "美元"
# 分 → 元 的换算系数（如 689.51 分 → 6.8951 元/美元）。
_BOC_FEN_TO_YUAN = 100.0


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ("", "nan", "none", "--"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date_to_iso(value: Any) -> str | None:
    """将中行牌价的「日期」字段规整为 ISO 日期串。

    AkShare 返回的 Timestamp 经 ``to_json`` 后为 epoch 毫秒（int）；同时兼容直接的
    日期字符串。无法解析则返回 ``None``。
    """
    if value is None:
        return None
    # epoch 毫秒（to_json 对 Timestamp 的默认序列化）
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return (
                datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
                .date()
                .isoformat()
            )
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text or text.lower() in ("nan", "none"):
        return None
    # 纯数字字符串也按 epoch 毫秒处理
    if text.isdigit():
        try:
            return (
                datetime.fromtimestamp(int(text) / 1000.0, tz=timezone.utc)
                .date()
                .isoformat()
            )
        except (OverflowError, OSError, ValueError):
            return None
    # 形如 2023-11-09 / 2023/11/09 / 2023-11-09T00:00:00
    return text.replace("/", "-")[:10]


# ---------------------------------------------------------------------------
# 解析（与子进程解耦，便于离线 fixture 回归）
# ---------------------------------------------------------------------------


def _parse_boc_safe_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    """解析 ``currency_boc_safe``（外管局中间价日频表）为 USD/CNY 报价。"""
    records: list[dict[str, Any]] = payload.get("records") or []
    if not records:
        return None

    series: list[tuple[str | None, float]] = []
    for record in records:
        usd_fen = _as_float(record.get(_BOC_SAFE_USD_COL))
        if usd_fen is None or usd_fen <= 0:
            continue
        series.append((_date_to_iso(record.get(_BOC_DATE_COL)), usd_fen))

    if not series:
        return None

    latest_date, latest_fen = series[-1]
    last_price = round(latest_fen / _BOC_FEN_TO_YUAN, 4)

    change_percent: float | None = None
    if len(series) >= 2:
        _prev_date, prev_fen = series[-2]
        if prev_fen > 0:
            change_percent = round((latest_fen - prev_fen) / prev_fen * 100, 2)

    return {
        "last_price": last_price,
        "change_percent": change_percent,
        "quote_time": latest_date,
        "source": "currency_boc_safe",
        "stale": False,
        "frequency": "daily",
    }


def _parse_boc_sina_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    """解析 currency_boc_sina（中行牌价日频序列）为 USD/CNY 报价。

    - 取最新一行「中行折算价」（分）÷ 100 作 ``last_price``；
    - ``quote_time`` 取该行日期；
    - ``change_percent`` 由相邻两行折算价计算 ``(cur-prev)/prev*100``；
    - 标注 ``stale=True`` / ``frequency="daily"`` 表明日频时效偏差。

    无任何有效折算价时返回 ``None``。
    """
    records: list[dict[str, Any]] = payload.get("records") or []
    if not records:
        return None

    # 提取按时间顺序（fixture 为升序）的有效折算价及其日期。
    series: list[tuple[str | None, float]] = []
    for record in records:
        convert = _as_float(record.get(_BOC_CONVERT_COL))
        if convert is None or convert <= 0:
            continue
        series.append((_date_to_iso(record.get(_BOC_DATE_COL)), convert))

    if not series:
        return None

    latest_date, latest_convert = series[-1]
    last_price = round(latest_convert / _BOC_FEN_TO_YUAN, 4)

    change_percent: float | None = None
    if len(series) >= 2:
        _prev_date, prev_convert = series[-2]
        if prev_convert > 0:
            change_percent = round((latest_convert - prev_convert) / prev_convert * 100, 2)

    return {
        "last_price": last_price,
        "change_percent": change_percent,
        "quote_time": latest_date,
        "source": "currency_boc_sina",
        # 日频源：相对实时盘存在时效偏差，明确标注供上层置 stale。
        "stale": True,
        "frequency": "daily",
    }


# ---------------------------------------------------------------------------
# 子进程执行
# ---------------------------------------------------------------------------


def _run_akshare(
    script: str,
    *,
    label: str,
    timeout_seconds: float,
) -> dict[str, Any] | None:
    """运行子进程并返回 {"columns","records"} payload；任何失败返回 ``None``。"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.info("us forex source timeout (%s, %.1fs)", label, timeout_seconds)
        return None
    except OSError as exc:
        logger.error("us forex subprocess OSError (%s): %s", label, exc)
        return None

    stdout = result.stdout or ""
    if not stdout.strip():
        logger.debug(
            "us forex subprocess failed (%s): rc=%s stderr=%s",
            label,
            result.returncode,
            (result.stderr or "")[:200],
        )
        return None

    payload = _parse_json_stdout(stdout)
    if payload is None:
        logger.warning(
            "us forex subprocess JSON parse failed (%s): rc=%s stdout=%r",
            label,
            result.returncode,
            stdout[-200:],
        )
        return None

    if result.returncode != 0 or payload.get("error"):
        logger.debug(
            "us forex source error (%s): %s",
            label,
            payload.get("error") or f"subprocess rc={result.returncode}",
        )
        return None
    return payload


def _parse_json_stdout(stdout: str) -> dict[str, Any] | None:
    """解析 AkShare stdout，兼容上游在 JSON 前打印诊断信息。

    部分 AkShare 上游会先向 stdout 打印诊断文本，随后才由我们的子进程脚本输出
    结构化 ``{"error": ...}``。只解析整段文本会把这种源级失败误报成 JSON 异常；
    倒序读取最后一个合法 JSON 行即可保留降级语义。
    """
    text = (stdout or "").strip()
    if not text:
        return None

    candidates = [text, *reversed(text.splitlines())]
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def fetch_usd_cny() -> dict[str, Any] | None:
    """子进程拉取 USD/CNY 人民币汇率。

    主选 ``currency_boc_safe()``（外管局中间价），失败时回退
    ``currency_boc_sina``。两个源的超时之和严格小于上层 15 秒共享预算，避免
    上层返回后子进程仍继续运行并重复告警。

    返回含 ``last_price`` / ``change_percent`` / ``quote_time`` / ``source`` /
    ``stale`` / ``frequency`` 的字典；全部失败时返回 ``None``（交由上层降级）。
    绝不填占位常量。
    """
    # 1) 主选：外管局中间价（日频，与竞品汇率口径一致）。
    safe_payload = _run_akshare(
        _BOC_SAFE_SCRIPT,
        label="currency_boc_safe",
        timeout_seconds=_PRIMARY_SOURCE_TIMEOUT,
    )
    if safe_payload is not None:
        quote = _parse_boc_safe_payload(safe_payload)
        if quote is not None:
            return quote

    # 2) 最后备选：中行牌价（日频滚动 45 天窗口）。
    boc_payload = _run_akshare(
        _BOC_SINA_SCRIPT,
        label="currency_boc_sina",
        timeout_seconds=_FALLBACK_SOURCE_TIMEOUT,
    )
    if boc_payload is not None:
        quote = _parse_boc_sina_payload(boc_payload)
        if quote is not None:
            return quote

    logger.warning(
        "us forex unavailable: currency_boc_safe and currency_boc_sina failed"
    )
    return None
