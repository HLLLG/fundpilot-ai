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
        return FundSnapshot(
            fund_code=holding.fund_code,
            fund_name=holding.fund_name,
            latest_nav=float(nav_value) if nav_value is not None else None,
            nav_date=str(nav_date) if nav_date is not None else None,
            source="akshare",
        )
