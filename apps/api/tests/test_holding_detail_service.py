from datetime import date, timedelta

from app.models import FundProfile, Holding
from app.services.holding_detail_service import (
    _holding_days_from_snapshots,
    _resolve_holding_days,
    _yesterday_profit_from_snapshots,
    build_holding_detail,
)


def _holding(code="600000"):
    return Holding(fund_code=code, fund_name="测试基金", holding_amount=1000.0)


def test_resolve_holding_days_from_first_seen_increments():
    anchor = (date.today() - timedelta(days=12)).isoformat()
    profile = FundProfile(fund_code="600000", fund_name="测试基金", first_seen_date=anchor)
    days, source = _resolve_holding_days(profile, _holding())
    assert days == 12
    assert source == "first_seen"


def test_resolve_holding_days_user_date_beats_first_seen():
    profile = FundProfile(
        fund_code="600000",
        fund_name="测试基金",
        first_purchase_date=(date.today() - timedelta(days=100)).isoformat(),
        first_seen_date=(date.today() - timedelta(days=5)).isoformat(),
    )
    days, source = _resolve_holding_days(profile, _holding())
    assert days == 100
    assert source == "user"


def test_resolve_holding_days_no_anchor_returns_none(monkeypatch):
    monkeypatch.setattr(
        "app.services.holding_detail_service.list_portfolio_daily_snapshots",
        lambda limit=365: [],
    )
    profile = FundProfile(fund_code="600000", fund_name="测试基金")
    days, source = _resolve_holding_days(profile, _holding())
    assert days is None
    assert source is None


def test_yesterday_profit_from_snapshots(monkeypatch):
    monkeypatch.setattr(
        "app.services.holding_detail_service.list_portfolio_daily_snapshots",
        lambda limit=14: [
            {
                "snapshot_date": "2026-06-03",
                "holdings": [{"fund_code": "008586", "daily_profit": 100.0}],
            },
            {
                "snapshot_date": "2026-06-02",
                "holdings": [{"fund_code": "008586", "daily_profit": -86.23}],
            },
        ],
    )
    holding = Holding(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=8000,
        return_percent=3.0,
    )
    assert _yesterday_profit_from_snapshots(holding) == -86.23


def test_holding_days_from_snapshots(monkeypatch):
    monkeypatch.setattr(
        "app.services.holding_detail_service.list_portfolio_daily_snapshots",
        lambda limit=365: [
            {
                "snapshot_date": "2026-06-03",
                "holdings": [{"fund_code": "008586", "fund_name": "华夏人工智能ETF联接C"}],
            },
            {
                "snapshot_date": "2026-05-10",
                "holdings": [{"fund_code": "008586", "fund_name": "华夏人工智能ETF联接C"}],
            },
        ],
    )
    holding = Holding(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=8000,
        return_percent=3.0,
    )
    days = _holding_days_from_snapshots(holding)
    assert days is not None
    assert days >= 0


def test_resolve_holding_days_prefers_user_purchase_date(monkeypatch):
    monkeypatch.setattr(
        "app.services.holding_detail_service.list_portfolio_daily_snapshots",
        lambda limit=365: [
            {
                "snapshot_date": "2026-06-03",
                "holdings": [{"fund_code": "025856", "fund_name": "测试基金"}],
            }
        ],
    )
    profile = FundProfile(
        fund_code="025856",
        fund_name="测试基金",
        holding_days=200,
        holding_days_as_of=date.today().isoformat(),
        first_purchase_date=(date.today() - timedelta(days=30)).isoformat(),
    )
    holding = Holding(
        fund_code="025856",
        fund_name="测试基金",
        holding_amount=1000,
        return_percent=1.0,
    )
    days, source = _resolve_holding_days(profile, holding)
    assert days == 30
    assert source == "user"


def test_resolve_holding_days_ages_ocr_detail(monkeypatch):
    monkeypatch.setattr(
        "app.services.holding_detail_service.list_portfolio_daily_snapshots",
        lambda limit=365: [],
    )
    profile = FundProfile(
        fund_code="025856",
        fund_name="华夏中证电网设备主题ETF联接A",
        holding_days=95,
        holding_days_as_of=(date.today() - timedelta(days=10)).isoformat(),
    )
    holding = Holding(
        fund_code="025856",
        fund_name="华夏中证电网设备主题ETF联接A",
        holding_amount=15000,
        return_percent=2.0,
    )
    days, source = _resolve_holding_days(profile, holding)
    assert days == 105
    assert source == "ocr_detail"


def test_build_holding_detail_uses_profile_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    from app.config import refresh_settings
    from app.services.fund_profile import FundProfileService, parse_profile_from_text

    refresh_settings()
    detail_text = """
华夏中证电网设备主题ETF联接A
025856
持有金额
15,075.46
10,645.76
52.76%
持有收益
+401.80
+2.74%
1.3784
当日收益
昨日收益
持有天数
-85.93
-86.23
95
"""
    profile = parse_profile_from_text(detail_text)
    assert profile is not None
    FundProfileService().save_profile(profile)

    holding = Holding(
        fund_code="000000",
        fund_name="华夏中证电网设备...",
        holding_amount=15075.46,
        return_percent=2.74,
        sector_name="中证电网设备",
        sector_return_percent=-0.59,
    )
    result = build_holding_detail([holding], 0)
    assert result.holding.fund_code == "025856"
    assert result.holding_shares == 10645.76
    assert result.holding_cost == 1.3784
    assert result.yesterday_profit == -86.23
    assert result.holding_days == 95
    assert result.provenance["yesterday_profit"] == "ocr_detail"
