import pandas as pd

from app.services.fund_data import FundDataService


def test_get_nav_history_parses_akshare_frame(monkeypatch):
    # 生成90条交易日数据来匹配trading_days=90的默认值
    dates = pd.date_range("2025-12-01", periods=90, freq="B")
    payload = [
        {
            "date": date.strftime("%Y-%m-%d"),
            "nav": 1.0 + index * 0.005,
            "daily_growth": 0.3,
        }
        for index, date in enumerate(dates)
    ]

    def fake_fetch_fund_nav_history(fund_code: str, trading_days: int = 90):
        assert fund_code == "008586"
        assert trading_days == 90
        return {"data": payload}

    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_fund_nav_history",
        fake_fetch_fund_nav_history,
    )

    history = FundDataService().get_nav_history("008586", "测试基金", trading_days=90)
    assert history.source == "akshare"
    assert len(history.points) == 90  # 应该返回90个点
    assert history.latest_nav == history.points[-1].nav
    assert history.period_change_percent is not None
    assert history.period_change_percent > 0


def test_parse_nav_points_preserves_zero_daily_growth():
    from app.services.fund_data import _parse_nav_points

    points = _parse_nav_points(
        [
            {"date": "2026-05-30", "nav": 1.2, "daily_growth": 0.15},
            {"date": "2026-06-01", "nav": 1.2, "daily_growth": 0},
        ]
    )
    assert len(points) == 2
    assert points[1].daily_return_percent == 0


def test_get_nav_history_rejects_placeholder_code():
    history = FundDataService().get_nav_history("000000", "未知")
    assert history.source == "unavailable"
    assert history.points == []
    assert history.note
