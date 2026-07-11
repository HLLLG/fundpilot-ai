from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypeVar

from app.config import get_settings
from app.models import FundNavHistory, FundNavPoint, FundSnapshot, Holding
from app.services.nav_trend_summary import summarize_nav_history

_T = TypeVar("_T")
_R = TypeVar("_R")

# 逐只基金的 AkShare 拉取并发上限：每只是独立子进程 + 网络等待（IO 密集），
# 并发可显著压低冷缓存耗时；上限避免一次拉太多基金时打爆源站/子进程数。
_MAX_FETCH_WORKERS = 8


def _map_holdings_concurrently(
    items: list[_T],
    worker: Callable[[_T], _R],
) -> list[_R]:
    """按原序并发执行 worker；worker 须自行捕获异常返回兜底值。"""
    if not items:
        return []
    if len(items) == 1:
        return [worker(items[0])]
    max_workers = min(_MAX_FETCH_WORKERS, len(items))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(worker, items))


class FundDataService:
    def get_snapshots_with_nav_trends(
        self,
        holdings: list[Holding],
        *,
        trading_days: int | None = None,
    ) -> tuple[list[FundSnapshot], dict[str, dict]]:
        settings = get_settings()
        days = trading_days if trading_days is not None else settings.nav_cache_pull_days
        sample = settings.nav_trend_recent_sample
        window = settings.nav_trend_window

        # 逐只 AkShare 拉取是 IO 密集（子进程 + 网络），并发以缩短冷缓存耗时；
        # _snapshot_and_trend_for_holding 内部已捕获异常，返回顺序按持仓原序对齐。
        results = _map_holdings_concurrently(
            holdings,
            lambda holding: self._snapshot_and_trend_for_holding(
                holding, trading_days=days
            ),
        )

        snapshots: list[FundSnapshot] = []
        trends: dict[str, dict] = {}
        for holding, (snapshot, trend) in zip(holdings, results):
            snapshots.append(snapshot)
            if trend is not None:
                trends[holding.fund_code] = summarize_nav_history(
                    trend, recent_sample=sample, window_days=window
                ) or {}
        return snapshots, trends

    def get_nav_history(
        self,
        fund_code: str,
        fund_name: str = "",
        *,
        trading_days: int = 90,
    ) -> FundNavHistory:
        if fund_code == "000000":
            return FundNavHistory(
                fund_code=fund_code,
                fund_name=fund_name or "未知基金",
                source="unavailable",
                note="请先补全基金代码后再查看净值走势。",
            )

        try:
            return self._nav_history_from_akshare(
                fund_code, fund_name, trading_days=trading_days
            )
        except Exception as exc:
            return FundNavHistory(
                fund_code=fund_code,
                fund_name=fund_name,
                source="error",
                note=f"暂未获取到净值走势：{exc}",
            )

    def _snapshot_and_trend_for_holding(
        self,
        holding: Holding,
        *,
        trading_days: int,
    ) -> tuple[FundSnapshot, FundNavHistory | None]:
        if holding.fund_code == "000000":
            return (
                FundSnapshot(
                    fund_code=holding.fund_code,
                    fund_name=holding.fund_name,
                    source="yangjibao-ocr",
                    note="OCR 未识别到基金代码，已使用养基宝截图指标；补全代码后可拉取净值快照。",
                ),
                None,
            )

        try:
            return self._from_akshare_combined(holding, trading_days=trading_days)
        except Exception as exc:
            return (
                FundSnapshot(
                    fund_code=holding.fund_code,
                    fund_name=holding.fund_name,
                    source="manual",
                    note=f"暂未获取到实时净值数据：{exc}",
                ),
                None,
            )

    def _nav_history_from_akshare(
        self,
        fund_code: str,
        fund_name: str,
        *,
        trading_days: int,
    ) -> FundNavHistory:
        from app.services.fund_nav_cache import get_cached_fund_nav, save_cached_fund_nav

        cached = get_cached_fund_nav(fund_code, trading_days)
        if cached is not None and cached.points:
            if fund_name and not cached.fund_name:
                cached = cached.model_copy(update={"fund_name": fund_name})
            return cached

        from app.services.akshare_subprocess import fetch_fund_nav_history

        result = fetch_fund_nav_history(fund_code, trading_days=trading_days)
        if result is None or "data" not in result:
            raise ValueError("AkShare 获取净值数据失败或返回空数据")

        data = result["data"]
        if not data:
            raise ValueError("未能解析净值数据")

        points = _parse_nav_points(data)

        if not points:
            raise ValueError("未能解析净值数据")

        latest = points[-1]
        period_change = None
        if points[0].nav > 0:
            period_change = round((latest.nav / points[0].nav - 1) * 100, 2)

        history = FundNavHistory(
            fund_code=fund_code,
            fund_name=fund_name,
            source="akshare",
            points=points,
            latest_nav=latest.nav,
            latest_date=latest.date,
            period_change_percent=period_change,
        )
        save_cached_fund_nav(fund_code, trading_days, history)
        return history

    def _from_akshare_combined(
        self,
        holding: Holding,
        *,
        trading_days: int,
    ) -> tuple[FundSnapshot, FundNavHistory]:
        history = self.get_nav_history(
            holding.fund_code,
            holding.fund_name,
            trading_days=trading_days,
        )
        if history.source != "akshare" or not history.points:
            raise ValueError("AkShare 获取净值数据失败")

        latest_point = history.points[-1]

        # 获取基金诊断信息（这会单独调用AkShare）
        diagnostics = {}
        try:
            from app.services.fund_diagnostics_cache import load_fund_diagnostics

            diagnostics = load_fund_diagnostics(holding.fund_code)
        except Exception:
            pass  # 诊断信息失败不影响主逻辑

        snapshot = FundSnapshot(
            fund_code=holding.fund_code,
            fund_name=holding.fund_name,
            latest_nav=latest_point.nav,
            nav_date=latest_point.date,
            source="akshare",
            fund_type=diagnostics.get("fund_type"),
            management_fee=diagnostics.get("management_fee"),
            fund_scale_yi=diagnostics.get("fund_scale_yi"),
            return_1y_percent=diagnostics.get("return_1y_percent"),
            max_drawdown_1y_percent=diagnostics.get("max_drawdown_1y_percent"),
        )
        return snapshot, history

    def get_nav_history_page(
        self,
        fund_code: str,
        fund_name: str = "",
        *,
        limit: int = 30,
        before_date: str | None = None,
        pool_days: int = 800,
    ) -> dict:
        history = self.get_nav_history(fund_code, fund_name, trading_days=pool_days)
        if not history.points:
            return {
                "fund_code": fund_code,
                "fund_name": history.fund_name,
                "source": history.source,
                "points": [],
                "has_more": False,
                "next_before": None,
                "note": history.note,
            }

        points = sorted(history.points, key=lambda point: point.date, reverse=True)
        if before_date:
            cutoff = before_date[:10]
            points = [point for point in points if point.date < cutoff]

        page = points[:limit]
        has_more = len(points) > limit
        next_before = page[-1].date if has_more and page else None
        return {
            "fund_code": fund_code,
            "fund_name": history.fund_name,
            "source": history.source,
            "points": [point.model_dump(mode="json") for point in page],
            "has_more": has_more,
            "next_before": next_before,
            "note": history.note,
        }

    def get_index_daily_history(
        self,
        index_symbol: str = "000300",
        *,
        trading_days: int = 252,
    ) -> dict:
        from app.services.index_daily_client import (
            fetch_index_daily_history as fetch_index_daily,
        )
        from app.services.index_daily_client import index_display_name

        result = fetch_index_daily(index_symbol, trading_days=trading_days)
        if result is None:
            from app.services.akshare_subprocess import fetch_index_daily_history as fetch_ak

            result = fetch_ak(index_symbol, trading_days=trading_days)

        if result is None or "data" not in result:
            return {
                "symbol": index_symbol,
                "name": index_display_name(index_symbol),
                "source": "unavailable",
                "points": [],
                "note": "暂未获取到指数走势数据",
            }

        points = []
        for item in result["data"]:
            if not item.get("date") or item.get("close") is None:
                continue
            try:
                points.append(
                    {
                        "date": str(item["date"])[:10],
                        "close": round(float(item["close"]), 4),
                    }
                )
            except (TypeError, ValueError):
                continue

        period_change = None
        if len(points) >= 2 and points[0]["close"] > 0:
            period_change = round((points[-1]["close"] / points[0]["close"] - 1) * 100, 2)

        return {
            "symbol": index_symbol,
            "name": index_display_name(index_symbol),
            "source": str(result.get("source") or "akshare"),
            "points": points,
            "period_change_percent": period_change,
        }


def _parse_nav_points(data: list[dict]) -> list[FundNavPoint]:
    points: list[FundNavPoint] = []
    for item in data:
        if not item.get("date") or item.get("nav") is None:
            continue
        daily_return_percent = None
        daily_growth = item.get("daily_growth")
        if daily_growth is not None:
            try:
                daily_return_percent = round(float(daily_growth), 2)
            except (TypeError, ValueError):
                daily_return_percent = None
        try:
            points.append(
                FundNavPoint(
                    date=str(item["date"])[:10],
                    nav=round(float(item["nav"]), 4),
                    daily_return_percent=daily_return_percent,
                )
            )
        except (ValueError, TypeError):
            continue
    return points


def _load_fund_diagnostics(ak: object, fund_code: str) -> dict:
    diagnostics: dict = {}
    try:
        overview = ak.fund_open_fund_info_em(symbol=fund_code, indicator="基金概况")  # type: ignore[attr-defined]
        diagnostics.update(_parse_overview_frame(overview))
    except Exception:
        pass

    try:
        cumulative = ak.fund_open_fund_info_em(symbol=fund_code, indicator="累计收益率走势")  # type: ignore[attr-defined]
        diagnostics.update(_parse_return_frame(cumulative))
    except Exception:
        pass

    return diagnostics


def _parse_overview_frame(frame) -> dict:
    result: dict = {}
    if frame is None or frame.empty:
        return result

    columns = list(frame.columns)
    if len(columns) >= 2:
        keys = frame.iloc[:, 0].astype(str).tolist()
        values = frame.iloc[:, 1].astype(str).tolist()
        pairs = zip(keys, values)
    else:
        pairs = []

    for key, value in pairs:
        if "基金类型" in key or "类型" == key:
            result["fund_type"] = value
        if "管理费" in key or "管理费率" in key:
            result["management_fee"] = value
        if "规模" in key or "资产规模" in key:
            result["fund_scale_yi"] = _parse_scale_yi(value)
    return result


def _parse_return_frame(frame) -> dict:
    if frame is None or frame.empty or len(frame) < 2:
        return {}

    column = None
    for candidate in ("累计收益率", "收益率", "累计回报率"):
        if candidate in frame.columns:
            column = candidate
            break
    if column is None:
        numeric_cols = [name for name in frame.columns if name not in ("净值日期", "日期")]
        column = numeric_cols[-1] if numeric_cols else None
    if column is None:
        return {}

    growth_indices: list[float] = []
    for value in frame[column].tail(260):
        try:
            cumulative_percent = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(cumulative_percent):
            continue
        growth_index = 1.0 + cumulative_percent / 100.0
        if growth_index > 0:
            growth_indices.append(growth_index)
    if len(growth_indices) < 2:
        return {}

    start = growth_indices[0]
    end = growth_indices[-1]
    return_1y = round((end / start - 1.0) * 100.0, 2)
    peak = growth_indices[0]
    max_drawdown = 0.0
    for point in growth_indices:
        peak = max(peak, point)
        drawdown = (point / peak - 1.0) * 100.0
        max_drawdown = min(max_drawdown, drawdown)

    return {
        "return_1y_percent": return_1y,
        "max_drawdown_1y_percent": round(max_drawdown, 2),
    }


def _parse_scale_yi(text: str) -> float | None:
    cleaned = text.replace(",", "").strip()
    try:
        if "亿" in cleaned:
            return round(float(cleaned.replace("亿元", "").replace("亿", "")), 2)
        if "万" in cleaned:
            return round(float(cleaned.replace("万元", "").replace("万", "")) / 10000, 4)
        return round(float(cleaned), 2)
    except ValueError:
        return None
