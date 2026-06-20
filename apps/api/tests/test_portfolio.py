from app.config import refresh_settings
from app.database import get_portfolio_summary, save_portfolio_summary
from app.models import FundProfile, Holding, PortfolioSummary
from app.services.fund_profile import FundProfileService
from app.services.portfolio_parser import parse_portfolio_summary_from_text

# 账户汇总版式（账户资产 + 当日收益），parse_portfolio_summary_from_text 的输入。
ACCOUNT_SUMMARY_TEXT = """
账户汇总
28,572.36
-363.10
当日收益
"""


def test_parse_portfolio_summary_from_account_overview():
    summary = parse_portfolio_summary_from_text(ACCOUNT_SUMMARY_TEXT)

    assert summary is not None
    assert summary.total_assets == 28572.36
    assert summary.daily_profit == -363.10


def test_sync_profiles_from_overview_holdings(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    refresh_settings()

    service = FundProfileService()
    service.save_profile(
        FundProfile(
            fund_code="025856",
            fund_name="华夏中证电网设备主题ETF联接A",
            holding_amount=15075.46,
            holding_shares=10645.76,
            holding_cost=1.3784,
            sector_name="电网设备",
            intraday_index_name="中证电网设备",
        )
    )

    drafts = [
        Holding(
            fund_code="000000",
            fund_name="华夏中证电网设备...",
            holding_amount=15075.46,
            return_percent=2.74,
        ),
        Holding(
            fund_code="000000",
            fund_name="华夏人工智能ETF.",
            holding_amount=7427.01,
            return_percent=-1.12,
        ),
        Holding(
            fund_code="000000",
            fund_name="易方达国防军工混..",
            holding_amount=1846.93,
            return_percent=-7.65,
        ),
        Holding(
            fund_code="000000",
            fund_name="银河创新成长混合A",
            holding_amount=4222.96,
            return_percent=1.53,
        ),
    ]
    holdings = FundProfileService().resolve_holdings(drafts)
    result = service.sync_profiles_from_holdings(holdings)

    assert result.updated >= 1
    assert result.created >= 1

    matched = service.find_match("华夏中证电网设备...")
    assert matched is not None
    assert matched.holding_amount == 15075.46
    assert matched.holding_shares == 10645.76
    assert matched.holding_cost == 1.3784

    provisional = service.find_match("银河创新成长混合A")
    assert provisional is not None
    assert provisional.is_provisional is True
    assert provisional.holding_amount == 4222.96


def test_save_portfolio_summary_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    refresh_settings()

    summary = PortfolioSummary(total_assets=1000, daily_profit=-10, holding_count=2)
    save_portfolio_summary(summary)
    loaded = get_portfolio_summary()

    assert loaded is not None
    assert loaded.total_assets == 1000
    assert loaded.daily_profit == -10
