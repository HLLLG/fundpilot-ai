import pandas as pd

from app.services.fund_data import FundDataService


def test_get_nav_history_parses_akshare_frame(monkeypatch):
    # 生成90条交易日数据来匹配trading_days=90的默认值
    dates = pd.date_range("2025-12-01", periods=90, freq="B")
    frame = pd.DataFrame(
        {
            "净值日期": dates,
            "单位净值": [1.0 + index * 0.005 for index in range(90)],
            "日增长率": [0.3] * 90,
        }
    )

    def fake_fund_open_fund_info_em(symbol: str, indicator: str):
        assert symbol == "008586"
        assert indicator == "单位净值走势"
        return frame

    import sys

    fake_ak = type(sys)("akshare")
    fake_ak.fund_open_fund_info_em = fake_fund_open_fund_info_em
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)

    history = FundDataService().get_nav_history("008586", "测试基金", trading_days=90)
    assert history.source == "akshare"
    assert len(history.points) == 90  # 应该返回90个点
    assert history.latest_nav == history.points[-1].nav
    assert history.period_change_percent is not None
    assert history.period_change_percent > 0


def test_get_nav_history_rejects_placeholder_code():
    history = FundDataService().get_nav_history("000000", "未知")
    assert history.source == "unavailable"
    assert history.points == []
    assert history.note
