from __future__ import annotations

from app.models import FundSnapshot, Holding


class FundDataService:
    def get_snapshots(self, holdings: list[Holding]) -> list[FundSnapshot]:
        return [self._snapshot_for_holding(holding) for holding in holdings]

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

    def _from_akshare(self, holding: Holding) -> FundSnapshot:
        import akshare as ak  # type: ignore[import-not-found]

        frame = ak.fund_open_fund_info_em(symbol=holding.fund_code, indicator="单位净值走势")
        if frame.empty:
            raise ValueError("AkShare 返回空数据")
        latest = frame.iloc[-1]
        nav_value = latest.get("单位净值")
        nav_date = latest.get("净值日期")

        diagnostics = _load_fund_diagnostics(ak, holding.fund_code)
        return FundSnapshot(
            fund_code=holding.fund_code,
            fund_name=holding.fund_name,
            latest_nav=float(nav_value) if nav_value is not None else None,
            nav_date=str(nav_date) if nav_date is not None else None,
            source="akshare",
            fund_type=diagnostics.get("fund_type"),
            management_fee=diagnostics.get("management_fee"),
            fund_scale_yi=diagnostics.get("fund_scale_yi"),
            return_1y_percent=diagnostics.get("return_1y_percent"),
            max_drawdown_1y_percent=diagnostics.get("max_drawdown_1y_percent"),
        )


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
