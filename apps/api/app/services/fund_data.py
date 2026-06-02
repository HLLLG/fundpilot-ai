from __future__ import annotations

from app.config import get_settings
from app.models import FundNavHistory, FundNavPoint, FundSnapshot, Holding
from app.services.nav_trend_summary import summarize_nav_history


class FundDataService:
    def get_snapshots(self, holdings: list[Holding]) -> list[FundSnapshot]:
        snapshots, _ = self.get_snapshots_with_nav_trends(holdings)
        return snapshots

    def get_snapshots_with_nav_trends(
        self,
        holdings: list[Holding],
        *,
        trading_days: int | None = None,
    ) -> tuple[list[FundSnapshot], dict[str, dict]]:
        settings = get_settings()
        days = trading_days if trading_days is not None else settings.nav_trend_days
        sample = settings.nav_trend_recent_sample

        snapshots: list[FundSnapshot] = []
        trends: dict[str, dict] = {}
        for holding in holdings:
            snapshot, trend = self._snapshot_and_trend_for_holding(holding, trading_days=days)
            snapshots.append(snapshot)
            if trend is not None:
                trends[holding.fund_code] = summarize_nav_history(
                    trend, recent_sample=sample
                ) or {}
        return snapshots, trends

    def get_nav_trends_for_holdings(
        self,
        holdings: list[Holding],
        *,
        trading_days: int | None = None,
    ) -> dict[str, dict]:
        _, trends = self.get_snapshots_with_nav_trends(
            holdings, trading_days=trading_days
        )
        return trends

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

    def _snapshot_for_holding(self, holding: Holding) -> FundSnapshot:
        if holding.fund_code == "000000":
            return FundSnapshot(
                fund_code=holding.fund_code,
                fund_name=holding.fund_name,
                source="yangjibao-ocr",
                note="OCR 未识别到基金代码，已使用养基宝截图指标；补全代码后可拉取净值快照。",
            )

        try:
            return self._from_akshare(holding)
        except Exception as exc:
            return FundSnapshot(
                fund_code=holding.fund_code,
                fund_name=holding.fund_name,
                source="manual",
                note=f"暂未获取到实时净值数据：{exc}",
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
        import akshare as ak  # type: ignore[import-not-found]

        frame = ak.fund_open_fund_info_em(symbol=fund_code, indicator="单位净值走势")
        if frame is None or frame.empty:
            raise ValueError("AkShare 返回空数据")

        points = points_from_nav_frame(frame, trading_days=trading_days)
        if not points:
            raise ValueError("未能解析净值数据")

        latest = points[-1]
        period_change = None
        if points[0].nav > 0:
            period_change = round((latest.nav / points[0].nav - 1) * 100, 2)

        return FundNavHistory(
            fund_code=fund_code,
            fund_name=fund_name,
            source="akshare",
            points=points,
            latest_nav=latest.nav,
            latest_date=latest.date,
            period_change_percent=period_change,
        )

    def _from_akshare_combined(
        self,
        holding: Holding,
        *,
        trading_days: int,
    ) -> tuple[FundSnapshot, FundNavHistory]:
        import akshare as ak  # type: ignore[import-not-found]

        frame = ak.fund_open_fund_info_em(
            symbol=holding.fund_code, indicator="单位净值走势"
        )
        if frame is None or frame.empty:
            raise ValueError("AkShare 返回空数据")

        points = points_from_nav_frame(frame, trading_days=trading_days)
        if not points:
            raise ValueError("未能解析净值数据")

        latest_row = frame.iloc[-1]
        nav_value = latest_row.get("单位净值")
        nav_date = latest_row.get("净值日期")
        latest_point = points[-1]
        period_change = None
        if points[0].nav > 0:
            period_change = round((latest_point.nav / points[0].nav - 1) * 100, 2)

        diagnostics = _load_fund_diagnostics(ak, holding.fund_code)
        snapshot = FundSnapshot(
            fund_code=holding.fund_code,
            fund_name=holding.fund_name,
            latest_nav=float(nav_value) if nav_value is not None else latest_point.nav,
            nav_date=str(nav_date) if nav_date is not None else latest_point.date,
            source="akshare",
            fund_type=diagnostics.get("fund_type"),
            management_fee=diagnostics.get("management_fee"),
            fund_scale_yi=diagnostics.get("fund_scale_yi"),
            return_1y_percent=diagnostics.get("return_1y_percent"),
            max_drawdown_1y_percent=diagnostics.get("max_drawdown_1y_percent"),
        )
        history = FundNavHistory(
            fund_code=holding.fund_code,
            fund_name=holding.fund_name,
            source="akshare",
            points=points,
            latest_nav=latest_point.nav,
            latest_date=latest_point.date,
            period_change_percent=period_change,
        )
        return snapshot, history


def points_from_nav_frame(frame, *, trading_days: int) -> list[FundNavPoint]:
    if frame is None or frame.empty:
        return []

    limit = max(10, min(trading_days, 365))
    tail = frame.tail(limit)
    points: list[FundNavPoint] = []
    for _, row in tail.iterrows():
        nav = _parse_nav_value(row)
        if nav is None:
            continue
        date_value = _parse_nav_date(row)
        if not date_value:
            continue
        points.append(
            FundNavPoint(
                date=date_value,
                nav=nav,
                daily_return_percent=_parse_daily_return(row),
            )
        )
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

    series = []
    for value in frame[column].tail(260):
        try:
            series.append(float(value))
        except (TypeError, ValueError):
            continue
    if len(series) < 2:
        return {}

    start = series[0]
    end = series[-1]
    if start == 0:
        return {}

    return_1y = round((end / start - 1) * 100, 2)
    peak = series[0]
    max_drawdown = 0.0
    for point in series:
        peak = max(peak, point)
        if peak > 0:
            drawdown = (point / peak - 1) * 100
            max_drawdown = min(max_drawdown, drawdown)

    return {
        "return_1y_percent": return_1y,
        "max_drawdown_1y_percent": round(max_drawdown, 2),
    }


def _parse_nav_value(row) -> float | None:
    for key in ("单位净值", "净值", "nav"):
        if hasattr(row, "index") and key in row.index:  # type: ignore[attr-defined]
            try:
                return float(row[key])  # type: ignore[index]
            except (TypeError, ValueError):
                continue
    return None


def _parse_nav_date(row) -> str | None:
    for key in ("净值日期", "日期", "date"):
        if hasattr(row, "index") and key in row.index:  # type: ignore[attr-defined]
            value = row[key]  # type: ignore[index]
            if value is None:
                continue
            if hasattr(value, "isoformat"):
                return value.isoformat()[:10]
            text = str(value).strip()
            return text[:10] if text else None
    return None


def _parse_daily_return(row) -> float | None:
    for key in ("日增长率", "日涨跌幅", "涨跌幅", "daily_return"):
        if hasattr(row, "index") and key in row.index:  # type: ignore[attr-defined]
            raw = row[key]  # type: ignore[index]
            if raw is None:
                continue
            text = str(raw).replace("%", "").strip()
            try:
                return round(float(text), 4)
            except ValueError:
                continue
    return None


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
