from __future__ import annotations

"""大盘情绪温度计（M1.1）。

设计文档：docs/superpowers/specs/2026-07-02-ai-decision-sharpening-design.md 第 M1.1 节。

**口径确认（与设计原稿的偏离及原因）：** 设计原稿假设 `stock_zt_pool_em` /
`stock_zt_pool_dtgc_em` / `stock_zt_pool_zbgc_em`（涨停/跌停/炸板股池）可用于
"近1~2年历史数据分布校准情绪分级阈值"。经在本项目虚拟环境实测（AkShare 1.18.64），
这三个接口实际仅能回溯约 30 个交易日（超出即报错"该接口只能获取最近30个交易日的
数据"），无法支撑历史校准。改为双轨方案（已与用户确认）：

- **主信号（可回测/自校准）：** `stock_a_high_low_statistics` 全市场创新高/创新低
  家数，实测有约 2 年历史。用"今日 20 日净新高家数（high20-low20）在近 2 年分布中的
  百分位"动态计算情绪档位——阈值不是写死的常量，而是每次都用真实历史分布现算，
  这就是设计里"先测算再定阈值"的落地方式，且自动随市场状态漂移更新。
- **辅助信号（当日快照，明确不做历史校准）：** 涨停/跌停家数、炸板率、连板高度——
  来自涨跌停池接口，仅用于当日快照解读文案，字段/文案均标注"当日快照"而非可回测结论。
- **两融环比：** `stock_margin_sse`（区间查询，历史稳定）；深市 `stock_margin_szse`
  仅支持单日查询，为保持实现简单、避免引入额外脆弱路径，v1 明确只用沪市数据并标注
  `margin_scope=sse_only`，不冒充"全市场"。披露有 T-1 延迟，已标注 `margin_as_of_date`。

全程 best-effort：任一环节失败/超时返回 `available=False`（顶层）或该子字段
`*_available=False`，绝不阻塞日报生成、绝不编造数值。
"""

import logging
from datetime import date, timedelta

from app.config import get_settings
from app.services.akshare_subprocess import run_akshare_json_script
from app.services.sector_quote_cache import (
    get_spot_snapshot,
    get_spot_snapshot_any_age,
    save_spot_snapshot,
)
from app.services.trading_session import build_trading_session

logger = logging.getLogger(__name__)

_CACHE_VERSION = "v1"
_LIVE_TTL_SECONDS = 1800.0
_CLOSED_TTL_SECONDS = 3600.0
_INTRADAY_SESSIONS = {
    "trading_day_intraday",
    "trading_day_pre_close",
    "trading_day_pre_open",
}
# 涨跌停池按日查询遇到空数据（周末/假日/尚未收盘）时，向前回退查找最近有效交易日的最大尝试次数。
_MAX_LOOKBACK_ATTEMPTS = 6
_MIN_BREADTH_SAMPLE_DAYS = 60

# 情绪档位：由冷到热；档位序号用于计算 sentiment_level_change（跨档位差）。
SENTIMENT_LEVELS = ("冰点", "低迷", "中性", "偏热", "亢奋")


def _cache_ttl_seconds() -> float:
    session_kind = str(build_trading_session().get("session_kind") or "")
    if session_kind in _INTRADAY_SESSIONS:
        return _LIVE_TTL_SECONDS
    return _CLOSED_TTL_SECONDS


def _cache_key(trade_date: str) -> str:
    return f"market:breadth:{_CACHE_VERSION}:{trade_date[:10]}"


def build_market_breadth_signal(trade_date: str | None = None) -> dict:
    """大盘情绪温度计主入口。`available=False` 时不阻塞日报（详见模块 docstring）。"""
    settings = get_settings()
    if not settings.market_breadth_enabled:
        return {
            "available": False,
            "reason": "disabled",
            "message": "大盘情绪温度计已关闭（FUND_AI_MARKET_BREADTH_ENABLED=false）。",
        }

    anchor = (
        trade_date
        or build_trading_session().get("effective_trade_date")
        or date.today().isoformat()
    )[:10]
    cache_key = _cache_key(anchor)
    cached = get_spot_snapshot(cache_key, ttl_seconds=_cache_ttl_seconds())
    if cached is not None:
        return dict(cached)

    result = _build_market_breadth_signal_uncached(anchor, settings.market_breadth_timeout_seconds)
    if result.get("available"):
        save_spot_snapshot(cache_key, result)
        return result

    stale = get_spot_snapshot_any_age(cache_key)
    if stale:
        stale_copy = dict(stale)
        stale_copy["stale"] = True
        return stale_copy
    return result


def _build_market_breadth_signal_uncached(anchor: str, timeout: float) -> dict:
    breadth_rows = _fetch_high_low_breadth_history(timeout=timeout)
    sentiment = _compute_sentiment(breadth_rows) if breadth_rows else None

    if sentiment is None:
        return {
            "available": False,
            "trade_date": anchor,
            "reason": "breadth_history_unavailable",
            "message": "全市场创新高/创新低家数历史暂不可用，情绪温度计本次跳过。",
        }

    limit_pool = _fetch_limit_pool_snapshot(anchor, timeout=timeout)
    margin = _fetch_margin_balance_change(anchor, timeout=timeout)

    return {
        "available": True,
        "trade_date": sentiment["trade_date"],
        "breadth_percentile": sentiment["breadth_percentile"],
        "breadth_sample_days": sentiment["sample_days"],
        "sentiment_level": sentiment["sentiment_level"],
        "sentiment_level_change": sentiment["sentiment_level_change"],
        "limit_up_count": (limit_pool or {}).get("limit_up_count"),
        "limit_down_count": (limit_pool or {}).get("limit_down_count"),
        "limit_up_broken_ratio_percent": (limit_pool or {}).get("limit_up_broken_ratio_percent"),
        "max_consecutive_boards": (limit_pool or {}).get("max_consecutive_boards"),
        "limit_pool_as_of_date": (limit_pool or {}).get("as_of_date"),
        "limit_pool_available": limit_pool is not None,
        "margin_balance_change_yi": (margin or {}).get("margin_balance_change_yi"),
        "margin_scope": (margin or {}).get("margin_scope"),
        "margin_as_of_date": (margin or {}).get("as_of_date"),
        "margin_available": margin is not None,
        "interpretation": _build_interpretation(sentiment, limit_pool),
        "basis": (
            f"情绪档位基于近2年全市场创新高低家数分布第{sentiment['breadth_percentile']}百分位"
            "（自校准，非固定阈值）；涨跌停/炸板家数为当日快照，非历史回测校准。"
        ),
    }


# --- 主信号：全市场创新高/创新低家数（历史约2年，可自校准） -----------------------


def _fetch_high_low_breadth_history(*, timeout: float) -> list[dict] | None:
    script = """
import akshare as ak
import json
try:
    frame = ak.stock_a_high_low_statistics(symbol="all")
    if frame is None or frame.empty:
        print(json.dumps({"error": "empty"}))
    else:
        def _num(row, key):
            raw = row.get(key)
            if raw is None:
                return None
            try:
                return float(raw)
            except (TypeError, ValueError):
                return None
        rows = []
        for _, row in frame.iterrows():
            rows.append({
                "date": str(row.get("date", ""))[:10],
                "high20": _num(row, "high20"),
                "low20": _num(row, "low20"),
            })
        print(json.dumps({"data": rows}, ensure_ascii=True))
except Exception as e:
    print(json.dumps({"error": str(e)}, ensure_ascii=True))
"""
    payload = run_akshare_json_script(script, label="market_breadth_high_low", timeout=timeout)
    if not isinstance(payload, dict) or payload.get("error"):
        return None
    rows = payload.get("data")
    if not isinstance(rows, list) or not rows:
        return None
    # 已知 AkShare 返回样本存在同日重复行（见接口文档示例），按日期去重取最后一条并排序。
    by_date: dict[str, dict] = {}
    for row in rows:
        day = str(row.get("date") or "")[:10]
        if day:
            by_date[day] = row
    return [by_date[day] for day in sorted(by_date)]


def _percentile_rank(values: list[float], target: float) -> float:
    """target 在 values 中的百分位（0~100，<=target 的占比）。"""
    if not values:
        return 50.0
    below_or_equal = sum(1 for value in values if value <= target)
    return round(below_or_equal / len(values) * 100, 1)


def _sentiment_level_from_percentile(pct: float) -> str:
    if pct <= 10:
        return "冰点"
    if pct <= 35:
        return "低迷"
    if pct <= 65:
        return "中性"
    if pct <= 90:
        return "偏热"
    return "亢奋"


def _breadth_series(rows: list[dict]) -> list[tuple[str, float]]:
    result: list[tuple[str, float]] = []
    for row in rows:
        high20 = row.get("high20")
        low20 = row.get("low20")
        day = row.get("date")
        if high20 is None or low20 is None or not day:
            continue
        result.append((str(day), float(high20) - float(low20)))
    return result


def _compute_sentiment(rows: list[dict]) -> dict | None:
    series = _breadth_series(rows)
    if len(series) < _MIN_BREADTH_SAMPLE_DAYS:
        return None

    values = [value for _, value in series]
    latest_date, latest_value = series[-1]
    latest_pct = _percentile_rank(values, latest_value)
    latest_level = _sentiment_level_from_percentile(latest_pct)

    level_change: int | None = None
    if len(series) >= 2:
        _, prev_value = series[-2]
        prev_pct = _percentile_rank(values[:-1], prev_value)
        prev_level = _sentiment_level_from_percentile(prev_pct)
        level_change = SENTIMENT_LEVELS.index(latest_level) - SENTIMENT_LEVELS.index(prev_level)

    return {
        "trade_date": latest_date,
        "breadth_percentile": latest_pct,
        "sentiment_level": latest_level,
        "sentiment_level_change": level_change,
        "sample_days": len(series),
    }


# --- 辅助信号：涨停/跌停/炸板当日快照（不做历史校准） -----------------------------


def _fetch_limit_pool_snapshot(anchor: str, *, timeout: float) -> dict | None:
    try:
        anchor_date = date.fromisoformat(anchor)
    except ValueError:
        anchor_date = date.today()
    for offset in range(_MAX_LOOKBACK_ATTEMPTS):
        query_date = anchor_date - timedelta(days=offset)
        result = _fetch_limit_pool_for_date(query_date.strftime("%Y%m%d"), timeout=timeout)
        if result is not None:
            result["as_of_date"] = query_date.isoformat()
            return result
    return None


def _fetch_limit_pool_for_date(query_date: str, *, timeout: float) -> dict | None:
    script = f"""
import akshare as ak
import json

try:
    up = ak.stock_zt_pool_em(date="{query_date}")
    down = ak.stock_zt_pool_dtgc_em(date="{query_date}")
    broken = ak.stock_zt_pool_zbgc_em(date="{query_date}")
    up_count = 0 if up is None else len(up)
    down_count = 0 if down is None else len(down)
    broken_count = 0 if broken is None else len(broken)
    max_board = 0
    if up is not None and not up.empty and "\\u8fde\\u677f\\u6570" in up.columns:
        max_board = int(up["\\u8fde\\u677f\\u6570"].max())
    print(json.dumps({{
        "limit_up_count": up_count,
        "limit_down_count": down_count,
        "broken_count": broken_count,
        "max_consecutive_boards": max_board,
    }}, ensure_ascii=True))
except Exception as e:
    print(json.dumps({{"error": str(e)}}, ensure_ascii=True))
"""
    payload = run_akshare_json_script(
        script,
        label=f"market_breadth_limit_pool:{query_date}",
        timeout=timeout,
    )
    if not isinstance(payload, dict) or payload.get("error"):
        return None
    up_count = int(payload.get("limit_up_count") or 0)
    down_count = int(payload.get("limit_down_count") or 0)
    broken_count = int(payload.get("broken_count") or 0)
    if up_count == 0 and down_count == 0 and broken_count == 0:
        # 空数据日（非交易日/尚未开盘），交给调用方向前回退查找。
        return None
    broken_ratio = None
    denom = up_count + broken_count
    if denom > 0:
        broken_ratio = round(broken_count / denom * 100, 1)
    return {
        "limit_up_count": up_count,
        "limit_down_count": down_count,
        "limit_up_broken_ratio_percent": broken_ratio,
        "max_consecutive_boards": int(payload.get("max_consecutive_boards") or 0),
    }


# --- 两融余额环比（沪市，T-1 披露延迟） ------------------------------------------


def _fetch_margin_balance_change(anchor: str, *, timeout: float) -> dict | None:
    try:
        end = date.fromisoformat(anchor)
    except ValueError:
        end = date.today()
    start = end - timedelta(days=20)
    script = f"""
import akshare as ak
import json
try:
    frame = ak.stock_margin_sse(start_date="{start.strftime('%Y%m%d')}", end_date="{end.strftime('%Y%m%d')}")
    if frame is None or frame.empty:
        print(json.dumps({{"error": "empty"}}))
    else:
        frame = frame.sort_values("\\u4fe1\\u7528\\u4ea4\\u6613\\u65e5\\u671f")
        rows = []
        for _, row in frame.iterrows():
            balance = row.get("\\u878d\\u8d44\\u878d\\u5238\\u4f59\\u989d")
            if balance is None:
                continue
            rows.append({{
                "date": str(row.get("\\u4fe1\\u7528\\u4ea4\\u6613\\u65e5\\u671f", ""))[:10],
                "balance_yuan": float(balance),
            }})
        print(json.dumps({{"data": rows}}, ensure_ascii=True))
except Exception as e:
    print(json.dumps({{"error": str(e)}}, ensure_ascii=True))
"""
    payload = run_akshare_json_script(script, label="market_breadth_margin_sse", timeout=timeout)
    if not isinstance(payload, dict) or payload.get("error"):
        return None
    rows = payload.get("data")
    if not isinstance(rows, list) or len(rows) < 2:
        return None
    latest = rows[-1]
    prev = rows[-2]
    try:
        change_yuan = float(latest["balance_yuan"]) - float(prev["balance_yuan"])
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "as_of_date": latest.get("date"),
        "margin_balance_change_yi": round(change_yuan / 1e8, 2),
        # 诚实划界：深市 stock_margin_szse 仅支持单日查询，v1 不叠加，避免引入额外脆弱路径。
        "margin_scope": "sse_only",
    }


def _build_interpretation(sentiment: dict, limit_pool: dict | None) -> str:
    level = sentiment["sentiment_level"]
    change = sentiment.get("sentiment_level_change")
    parts = [f"市场情绪{level}（近2年分布第{sentiment['breadth_percentile']}百分位）"]
    if change is not None and change != 0:
        direction = "转冷" if change < 0 else "转热"
        parts.append(f"较上一交易日{direction}{abs(change)}档")
    if limit_pool:
        up = limit_pool.get("limit_up_count")
        down = limit_pool.get("limit_down_count")
        broken = limit_pool.get("limit_up_broken_ratio_percent")
        if up is not None and down is not None:
            if down > up:
                parts.append(f"跌停家数({down})超过涨停家数({up})，情绪偏冷")
            elif up > 0 and up > down * 2:
                parts.append(f"涨停家数({up})明显多于跌停({down})，情绪偏暖")
        if broken is not None and broken >= 40:
            parts.append(f"炸板率{broken}%偏高，资金封板意愿弱")
    return "；".join(parts) + "。短线宜结合仓位敏感度参考，不构成投资建议。"
