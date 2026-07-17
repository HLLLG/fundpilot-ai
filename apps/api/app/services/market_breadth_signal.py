from __future__ import annotations

"""大盘情绪温度计（M1.1）。

设计文档：docs/superpowers/specs/2026-07-02-ai-decision-sharpening-design.md 第 M1.1 节。

**口径确认（与设计原稿的偏离及原因）：** 设计原稿假设 `stock_zt_pool_em` /
`stock_zt_pool_dtgc_em` / `stock_zt_pool_zbgc_em`（涨停/跌停/炸板股池）可用于
"近1~2年历史数据分布校准情绪分级阈值"。经在本项目虚拟环境实测（AkShare 1.18.64），
这三个接口实际仅能回溯约 30 个交易日（超出即报错"该接口只能获取最近30个交易日的
数据"），无法支撑历史校准。改为双轨方案（已与用户确认）：

- **收盘锚点（可回测/自校准）：** `stock_a_high_low_statistics` 全市场创新高/创新低
  家数，实测有约 2 年历史。用"今日 20 日净新高家数（high20-low20）在近 2 年分布中的
  百分位"动态计算情绪档位——阈值不是写死的常量，而是每次都用真实历史分布现算，
  这就是设计里"先测算再定阈值"的落地方式，且自动随市场状态漂移更新。
- **盘中主信号（准实时）：** `stock_market_activity_legu` 当前赚钱效应，按上涨/下跌/平盘
  家数与真实涨跌停计算当日情绪档位；交易时段每 5 分钟刷新，显式携带源站统计时间、
  新鲜度和 `decision_eligible`，不把上一交易日收盘百分位冒充为当天实时数据。
- **辅助信号（当日快照，明确不做历史校准）：** 涨停/跌停家数、炸板率、连板高度——
  来自涨跌停池接口，仅用于当日快照解读文案，字段/文案均标注"当日快照"而非可回测结论。
- **两融环比：** `stock_margin_sse`（区间查询，历史稳定）；深市 `stock_margin_szse`
  仅支持单日查询，为保持实现简单、避免引入额外脆弱路径，v1 明确只用沪市数据并标注
  `margin_scope=sse_only`，不冒充"全市场"。披露有 T-1 延迟，已标注 `margin_as_of_date`。

全程 best-effort：任一环节失败/超时返回 `available=False`（顶层）或该子字段
`*_available=False`，绝不阻塞日报生成、绝不编造数值。
"""

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.services.akshare_subprocess import run_akshare_json_script
from app.services.sector_quote_cache import (
    get_spot_snapshot,
    get_spot_snapshot_any_age,
    save_spot_snapshot,
)
from app.services.trading_session import build_trading_session, get_previous_trade_date

logger = logging.getLogger(__name__)

_CACHE_VERSION = "v3"
_CLOSED_TTL_SECONDS = 3600.0
# 涨跌停池按日查询遇到空数据（周末/假日/尚未收盘）时，向前回退查找最近有效交易日的最大尝试次数。
_MAX_LOOKBACK_ATTEMPTS = 6
_MIN_BREADTH_SAMPLE_DAYS = 60
_MIN_INTRADAY_MARKET_SAMPLE = 1000

# 情绪档位：由冷到热；档位序号用于计算 sentiment_level_change（跨档位差）。
SENTIMENT_LEVELS = ("冰点", "低迷", "中性", "偏热", "亢奋")
_CN_TZ = ZoneInfo("Asia/Shanghai")
_LIVE_SESSION_KINDS = {"trading_day_intraday", "trading_day_pre_close"}
_SOURCE_INTRADAY = "intraday_live"
_SOURCE_INTRADAY_FINAL = "intraday_final"
_SOURCE_CLOSING = "closing"
_SOURCE_PREVIOUS_CLOSE_FALLBACK = "previous_close_fallback"
_INTRADAY_FINAL_CUTOFF_HOUR = 14
_INTRADAY_FINAL_CUTOFF_MINUTE = 59


def _cache_ttl_seconds(*, signal_mode: str = "closing") -> float:
    if signal_mode == "intraday":
        settings = get_settings()
        return float(max(60, int(settings.market_breadth_live_refresh_interval_seconds)))
    return _CLOSED_TTL_SECONDS


def _cache_key(trade_date: str, *, signal_mode: str = "closing") -> str:
    return f"market:breadth:{_CACHE_VERSION}:{signal_mode}:{trade_date[:10]}"


def _now_cn() -> datetime:
    return datetime.now(_CN_TZ)


def build_market_breadth_signal(
    trade_date: str | None = None,
    *,
    force_refresh: bool = False,
) -> dict:
    """大盘情绪温度计主入口。

    交易时段优先返回乐咕赚钱效应盘中快照；其他时段返回最近完整收盘口径。盘中源
    失败时允许返回上一笔快照供展示，但会显式标成 stale 且禁止参与确定性 guard。
    """
    settings = get_settings()
    if not settings.market_breadth_enabled:
        return {
            "available": False,
            "reason": "disabled",
            "decision_eligible": False,
            "message": "大盘情绪温度计已关闭（FUND_AI_MARKET_BREADTH_ENABLED=false）。",
        }

    session = build_trading_session()
    anchor = (
        trade_date
        or session.get("effective_trade_date")
        or date.today().isoformat()
    )[:10]
    session_kind = str(session.get("session_kind") or "")
    calendar_date = str(session.get("calendar_date") or "")[:10]
    is_current_live = session_kind in _LIVE_SESSION_KINDS and anchor == calendar_date

    if is_current_live:
        return _build_intraday_signal(
            anchor,
            session=session,
            timeout=settings.market_breadth_timeout_seconds,
            force_refresh=force_refresh,
        )

    # 收盘后先把源站当天终值落盘；下一交易日开盘前（含周末）继续展示这笔终值。
    may_use_final = (
        (session_kind == "trading_day_after_close" and anchor == calendar_date)
        or (session_kind in {"trading_day_pre_open", "non_trading_day"} and anchor != calendar_date)
    )
    if may_use_final:
        final = _load_or_build_intraday_final(
            anchor,
            session=session,
            timeout=settings.market_breadth_timeout_seconds,
            allow_fetch=session_kind == "trading_day_after_close",
        )
        if final is not None:
            return final

    return _build_closing_signal(
        anchor,
        timeout=settings.market_breadth_timeout_seconds,
        force_refresh=force_refresh,
    )


def _build_closing_signal(
    anchor: str,
    *,
    timeout: float,
    force_refresh: bool = False,
) -> dict:
    cache_key = _cache_key(anchor, signal_mode="closing")
    if not force_refresh:
        cached = get_spot_snapshot(
            cache_key,
            ttl_seconds=_cache_ttl_seconds(signal_mode="closing"),
        )
        if cached is not None:
            return dict(cached)

    result = _build_market_breadth_signal_uncached(anchor, timeout)
    result = _with_closing_metadata(result, anchor=anchor)
    if result.get("available"):
        save_spot_snapshot(cache_key, result)
        return result

    stale = get_spot_snapshot_any_age(cache_key)
    if stale:
        return _mark_stale(stale, reason="closing_source_failed")
    return result


def _build_intraday_signal(
    anchor: str,
    *,
    session: dict,
    timeout: float,
    force_refresh: bool,
) -> dict:
    cache_key = _cache_key(anchor, signal_mode="intraday")
    if not force_refresh:
        cached = get_spot_snapshot(
            cache_key,
            ttl_seconds=_cache_ttl_seconds(signal_mode="intraday"),
        )
        if cached is not None:
            return _refresh_intraday_metadata(cached, anchor=anchor, session=session)

    activity = _fetch_intraday_market_activity(timeout=timeout)
    if activity is not None:
        previous_trade_date = get_previous_trade_date(anchor) or anchor
        # 日报增强预算很短：盘中请求只读独立的收盘背景缓存，不串行等待历史/两融源。
        # 后台刷新线程会单独预热该缓存；即使暂缺背景，也不影响当天实时广度展示。
        closing = _cached_closing_background(previous_trade_date)
        result = _compose_intraday_signal(
            activity,
            closing=closing,
            anchor=anchor,
            session=session,
        )
        if result.get("available"):
            save_spot_snapshot(cache_key, result)
            return result

    stale = get_spot_snapshot_any_age(cache_key)
    if stale:
        return _mark_stale(stale, reason="intraday_source_failed")

    previous_trade_date = get_previous_trade_date(anchor) or anchor
    closing = _cached_closing_background(previous_trade_date)
    if not closing:
        closing = _build_closing_signal(previous_trade_date, timeout=timeout)
    fallback = dict(closing)
    fallback.update(
        {
            "source_mode": _SOURCE_PREVIOUS_CLOSE_FALLBACK,
            "decision_eligible": False,
            "decision_status": "ineligible_source_fallback",
            "decision_message": "盘中实时源暂不可用，展示上一交易日收盘背景，不参与硬守卫。",
            "stale": True,
            "freshness_status": "stale",
        }
    )
    return fallback


def _load_or_build_intraday_final(
    anchor: str,
    *,
    session: dict,
    timeout: float,
    allow_fetch: bool,
) -> dict | None:
    cache_key = _cache_key(anchor, signal_mode="intraday")
    cached = get_spot_snapshot_any_age(cache_key)
    if cached and _is_valid_intraday_final(cached, anchor=anchor):
        return _freeze_intraday_snapshot(cached)

    if not allow_fetch:
        return None

    activity = _fetch_intraday_market_activity(timeout=timeout)
    if activity is None or not _is_valid_intraday_final(activity, anchor=anchor):
        return None
    previous_trade_date = get_previous_trade_date(anchor) or anchor
    closing = _cached_closing_background(previous_trade_date)
    result = _compose_intraday_signal(
        activity,
        closing=closing,
        anchor=anchor,
        session=session,
        final=True,
    )
    save_spot_snapshot(cache_key, result)
    return result


def _fetch_intraday_market_activity(*, timeout: float) -> dict | None:
    """获取乐咕当前赚钱效应。该接口含源站统计时间，可据此做严格时效校验。"""
    script = """
import akshare as ak
import json
try:
    frame = ak.stock_market_activity_legu()
    if frame is None or frame.empty:
        print(json.dumps({"error": "empty"}))
    else:
        items = {}
        for _, row in frame.iterrows():
            key = str(row.get("item", "")).strip()
            if key:
                items[key] = row.get("value")
        def _number(key):
            raw = items.get(key)
            if raw is None:
                return None
            try:
                return float(str(raw).replace("%", "").strip())
            except (TypeError, ValueError):
                return None
        print(json.dumps({
            "advance_count": _number("上涨"),
            "decline_count": _number("下跌"),
            "flat_count": _number("平盘"),
            "suspended_count": _number("停牌"),
            "limit_up_count": _number("涨停"),
            "limit_down_count": _number("跌停"),
            "real_limit_up_count": _number("真实涨停"),
            "real_limit_down_count": _number("真实跌停"),
            "activity_percent": _number("活跃度"),
            "as_of_datetime": str(items.get("统计日期") or ""),
        }, ensure_ascii=True))
except Exception as e:
    print(json.dumps({"error": str(e)}, ensure_ascii=True))
"""
    payload = run_akshare_json_script(
        script,
        label="market_breadth_intraday_activity",
        timeout=timeout,
    )
    if not isinstance(payload, dict) or payload.get("error"):
        return None
    advance = _as_number(payload.get("advance_count"))
    decline = _as_number(payload.get("decline_count"))
    flat = _as_number(payload.get("flat_count"))
    suspended = _as_number(payload.get("suspended_count"))
    if advance is None or decline is None:
        return None
    traded_total = advance + decline + (flat or 0)
    market_total = traded_total + (suspended or 0)
    activity = _as_number(payload.get("activity_percent"))
    if market_total < _MIN_INTRADAY_MARKET_SAMPLE:
        return None
    if activity is None and market_total > 0:
        activity = round(advance / market_total * 100, 2)
    parsed_as_of = _parse_as_of_datetime(payload.get("as_of_datetime"))
    return {
        "available": True,
        "source_name": "乐咕赚钱效应",
        "universe_scope": "沪深两市",
        "advance_count": int(advance),
        "decline_count": int(decline),
        "flat_count": int(flat or 0),
        "suspended_count": int(suspended or 0),
        "traded_sample_count": int(traded_total),
        "market_sample_count": int(market_total),
        "activity_percent": activity,
        # 涨跌/平盘比例只在实际交易样本内计算；源站活跃度则包含停牌股分母。
        "advance_ratio_percent": (
            round(advance / traded_total * 100, 2) if traded_total > 0 else None
        ),
        "decline_ratio_percent": (
            round(decline / traded_total * 100, 2) if traded_total > 0 else None
        ),
        "flat_ratio_percent": (
            round((flat or 0) / traded_total * 100, 2) if traded_total > 0 else None
        ),
        "limit_up_count": _as_int(payload.get("limit_up_count")),
        "limit_down_count": _as_int(payload.get("limit_down_count")),
        "real_limit_up_count": _as_int(payload.get("real_limit_up_count")),
        "real_limit_down_count": _as_int(payload.get("real_limit_down_count")),
        # 对外统一为带 Asia/Shanghai 偏移的 ISO 8601，避免浏览器按本机时区误解无时区字符串。
        "as_of_datetime": parsed_as_of.isoformat() if parsed_as_of is not None else None,
    }


def _compose_intraday_signal(
    activity: dict,
    *,
    closing: dict,
    anchor: str,
    session: dict,
    final: bool = False,
) -> dict:
    activity_percent = _as_number(activity.get("activity_percent"))
    live_level = (
        _sentiment_level_from_advance_ratio(activity_percent)
        if activity_percent is not None
        else None
    )
    breadth_tone = (
        _breadth_tone_from_advance_ratio(activity_percent)
        if activity_percent is not None
        else None
    )
    closing_level = str(closing.get("sentiment_level") or "") or None
    # 盘中赚钱效应档位与收盘创新高/低历史百分位不是同一统计口径，禁止跨口径相减。
    # 后续若积累同日盘中平滑基准，可在同口径内恢复 level_change；目前安全返回 None。
    level_change = None

    result = {
        "available": True,
        "trade_date": anchor,
        "signal_mode": "intraday",
        "source_mode": _SOURCE_INTRADAY_FINAL if final else _SOURCE_INTRADAY,
        "as_of_datetime": activity.get("as_of_datetime"),
        "breadth_percentile": None,
        "breadth_sample_days": closing.get("breadth_sample_days"),
        "sentiment_level": live_level,
        "breadth_tone": breadth_tone,
        "sentiment_level_change": level_change,
        "source_name": activity.get("source_name"),
        "universe_scope": activity.get("universe_scope"),
        "advance_count": activity.get("advance_count"),
        "decline_count": activity.get("decline_count"),
        "flat_count": activity.get("flat_count"),
        "suspended_count": activity.get("suspended_count"),
        "traded_sample_count": activity.get("traded_sample_count"),
        "market_sample_count": activity.get("market_sample_count"),
        "activity_percent": activity_percent,
        "advance_ratio_percent": activity.get("advance_ratio_percent"),
        "decline_ratio_percent": activity.get("decline_ratio_percent"),
        "flat_ratio_percent": activity.get("flat_ratio_percent"),
        "limit_up_count": activity.get("limit_up_count"),
        "limit_down_count": activity.get("limit_down_count"),
        "real_limit_up_count": activity.get("real_limit_up_count"),
        "real_limit_down_count": activity.get("real_limit_down_count"),
        "limit_pool_as_of_date": anchor,
        "limit_pool_available": True,
        "limit_up_broken_ratio_percent": None,
        "max_consecutive_boards": None,
        "margin_balance_change_yi": closing.get("margin_balance_change_yi"),
        "margin_scope": closing.get("margin_scope"),
        "margin_as_of_date": closing.get("margin_as_of_date"),
        "margin_available": bool(closing.get("margin_available")),
        "closing_trade_date": closing.get("trade_date"),
        "closing_breadth_percentile": closing.get("breadth_percentile"),
        "closing_sentiment_level": closing_level,
        "interpretation": _build_intraday_interpretation(activity, breadth_tone),
        "basis": (
            "盘中展示基于乐咕沪深两市赚钱效应（上涨/下跌/平盘/停牌及涨跌停）准实时计算；"
            "个股广度描述与用于守卫的五档情绪分开表达，避免把原始上涨占比冒充历史百分位；"
            "近2年创新高低百分位仅作为上一完整交易日背景，不冒充盘中历史分位。"
        ),
    }
    if final:
        return _freeze_intraday_snapshot(result)
    return _refresh_intraday_metadata(result, anchor=anchor, session=session)


def _refresh_intraday_metadata(payload: dict, *, anchor: str, session: dict) -> dict:
    result = dict(payload)
    settings = get_settings()
    current = _now_cn()
    age = _intraday_snapshot_age_seconds(result.get("as_of_datetime"), current=current)
    source_date = str(result.get("as_of_datetime") or "")[:10]
    ready_at = current.replace(hour=9, minute=30, second=0, microsecond=0) + timedelta(
        minutes=max(0, int(settings.market_breadth_live_guard_delay_minutes))
    )
    source_matches = source_date == anchor
    fresh = age is not None and age <= settings.market_breadth_live_freshness_seconds
    in_live_session = str(session.get("session_kind") or "") in _LIVE_SESSION_KINDS
    is_lunch_break = str(session.get("market_phase") or "") == "lunch_break"
    eligible = bool(source_matches and fresh and in_live_session and current >= ready_at)
    result.update(
        {
            "signal_mode": "intraday",
            "source_mode": _SOURCE_INTRADAY,
            "freshness_seconds": round(age, 1) if age is not None else None,
            "freshness_status": "live" if fresh and source_matches else "stale",
            "stale": not (fresh and source_matches),
            "decision_eligible": eligible,
        }
    )
    if not source_matches:
        status = "ineligible_date_mismatch"
        message = "实时源统计日期不是当前交易日，仅供背景展示，不参与硬守卫。"
    elif not fresh:
        status = "ineligible_stale"
        message = "实时快照已超过时效阈值，仅供背景展示，不参与硬守卫。"
    elif current < ready_at:
        status = "opening_observation"
        message = "开盘初期波动较大，当前仅观察，达到稳定窗口后再参与硬守卫。"
    elif not in_live_session:
        status = "ineligible_session"
        message = "当前不在交易时段，盘中快照不参与硬守卫。"
    elif is_lunch_break:
        status = "eligible_lunch_break"
        message = "午间休市，上午收盘快照仍在有效交易时钟内。"
    else:
        status = "eligible"
        message = "当前交易日盘中快照新鲜，可参与决策守卫。"
    result["decision_status"] = status
    result["decision_message"] = message
    return result


def _freeze_intraday_snapshot(payload: dict) -> dict:
    result = dict(payload)
    result.update(
        {
            "signal_mode": "intraday",
            "source_mode": _SOURCE_INTRADAY_FINAL,
            "freshness_status": "fresh",
            "stale": False,
            "decision_eligible": True,
            "decision_status": "eligible_final",
            "decision_message": "已冻结为最近完整交易日终值，可参与决策守卫。",
        }
    )
    return result


def _cached_closing_background(trade_date: str) -> dict:
    cached = get_spot_snapshot_any_age(_cache_key(trade_date, signal_mode="closing"))
    return dict(cached) if cached else {}


def refresh_market_breadth_closing_background() -> dict:
    """供共享后台线程预热最近完整交易日背景，避免挤占日报/页面请求预算。"""
    session = build_trading_session()
    anchor = str(session.get("effective_trade_date") or "")[:10]
    if str(session.get("session_kind") or "") in _LIVE_SESSION_KINDS:
        anchor = get_previous_trade_date(anchor) or anchor
    return _build_closing_signal(
        anchor or date.today().isoformat(),
        timeout=get_settings().market_breadth_timeout_seconds,
        force_refresh=False,
    )


def _is_valid_intraday_final(payload: dict, *, anchor: str) -> bool:
    parsed = _parse_as_of_datetime(payload.get("as_of_datetime"))
    if parsed is None or parsed.date().isoformat() != anchor:
        return False
    # 终值确认与盘中 freshness 是两种语义：不得把 14:50 等仍在交易的快照冻结成收盘值。
    final_cutoff = parsed.replace(
        hour=_INTRADAY_FINAL_CUTOFF_HOUR,
        minute=_INTRADAY_FINAL_CUTOFF_MINUTE,
        second=0,
        microsecond=0,
    )
    return parsed >= final_cutoff


def _with_closing_metadata(payload: dict, *, anchor: str) -> dict:
    result = dict(payload)
    breadth_date = str(result.get("trade_date") or "")[:10]
    eligible = bool(result.get("available") and breadth_date == anchor)
    result.update(
        {
            "signal_mode": "closing",
            "source_mode": _SOURCE_CLOSING,
            "as_of_datetime": f"{breadth_date}T15:00:00+08:00" if breadth_date else None,
            "freshness_seconds": None,
            "freshness_status": "fresh" if eligible else "stale",
            "stale": not eligible,
            "decision_eligible": eligible,
            "decision_status": "eligible" if eligible else "ineligible_stale",
            "decision_message": (
                "最近完整交易日收盘信号可参与决策守卫。"
                if eligible
                else "收盘信号未更新到有效交易日，仅供背景展示，不参与硬守卫。"
            ),
        }
    )
    return result


def _mark_stale(payload: dict, *, reason: str) -> dict:
    result = dict(payload)
    result.update(
        {
            "stale": True,
            "freshness_status": "stale",
            "decision_eligible": False,
            "decision_status": "ineligible_source_fallback",
            "decision_message": "数据源刷新失败，当前为降级快照，仅供展示，不参与硬守卫。",
            "stale_reason": reason,
        }
    )
    return result


def _parse_as_of_datetime(value: object) -> datetime | None:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=_CN_TZ)
    return parsed.astimezone(_CN_TZ)


def _snapshot_age_seconds(value: object) -> float | None:
    parsed = _parse_as_of_datetime(value)
    if parsed is None:
        return None
    return max(0.0, (_now_cn() - parsed).total_seconds())


def _intraday_snapshot_age_seconds(
    value: object,
    *,
    current: datetime | None = None,
) -> float | None:
    """按连续交易时间计算盘中快照年龄，午间休市不消耗 freshness。"""
    parsed = _parse_as_of_datetime(value)
    if parsed is None:
        return None
    now = current or _now_cn()
    now = now.replace(tzinfo=_CN_TZ) if now.tzinfo is None else now.astimezone(_CN_TZ)
    elapsed = max(0.0, (now - parsed).total_seconds())
    if parsed.date() != now.date() or now <= parsed:
        return elapsed

    lunch_start = parsed.replace(hour=11, minute=30, second=0, microsecond=0)
    lunch_end = parsed.replace(hour=13, minute=0, second=0, microsecond=0)
    overlap_start = max(parsed, lunch_start)
    overlap_end = min(now, lunch_end)
    if overlap_end > overlap_start:
        elapsed -= (overlap_end - overlap_start).total_seconds()
    return max(0.0, elapsed)


def _as_number(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_int(value: object) -> int | None:
    number = _as_number(value)
    return int(number) if number is not None else None


def _build_intraday_interpretation(activity: dict, breadth_tone: str | None) -> str:
    advance = activity.get("advance_count")
    decline = activity.get("decline_count")
    activity_percent = activity.get("activity_percent")
    return (
        f"沪深个股广度{breadth_tone or '待确认'}，上涨{advance}家、下跌{decline}家，"
        f"上涨占全样本约{activity_percent}%。该信号按源站统计时间动态更新。"
    )


def _build_market_breadth_signal_uncached(anchor: str, timeout: float) -> dict:
    breadth_rows = _fetch_high_low_breadth_history(timeout=timeout)
    # 历史日期调用必须截断到 anchor，避免使用未来数据；当天盘中则自然只会取到最近收盘日。
    rows_to_anchor = (
        [row for row in breadth_rows if str(row.get("date") or "")[:10] <= anchor]
        if breadth_rows
        else []
    )
    sentiment = _compute_sentiment(rows_to_anchor) if rows_to_anchor else None

    if sentiment is None:
        return {
            "available": False,
            "trade_date": anchor,
            "reason": "breadth_history_unavailable",
            "decision_eligible": False,
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


def _sentiment_level_from_advance_ratio(pct: float) -> str:
    """把原始上涨占比映射到兼容既有守卫的五档情绪。

    五档字段被确定性守卫消费，现阶段保留原有强度边界，避免未经回测擅自改变动作；
    面向用户的细粒度语义由 ``_breadth_tone_from_advance_ratio`` 独立提供。
    """

    if pct <= 10:
        return "冰点"
    if pct <= 35:
        return "低迷"
    if pct <= 65:
        return "中性"
    if pct <= 90:
        return "偏热"
    return "亢奋"


def _breadth_tone_from_advance_ratio(pct: float) -> str:
    """原始上涨占比的可读描述；不冒充历史百分位。"""

    if pct <= 20:
        return "普跌冰点"
    if pct <= 35:
        return "普跌低迷"
    if pct < 45:
        return "整体偏弱"
    if pct < 50:
        return "分化偏弱"
    if pct <= 55:
        return "多空均衡"
    if pct <= 65:
        return "分化偏强"
    if pct <= 80:
        return "多数活跃"
    return "普涨亢奋"


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
