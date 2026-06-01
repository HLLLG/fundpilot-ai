from pathlib import Path

from app.config import refresh_settings
from app.database import get_portfolio_summary, save_portfolio_summary
from app.models import Holding, PortfolioSummary
from app.services.fund_profile import FundProfileService, parse_profile_from_text
from app.services.ocr_parser import parse_holdings_from_text
from app.services.portfolio_parser import parse_portfolio_summary_from_text

FIXTURES = Path(__file__).parent / "fixtures"
OVERVIEW_TEXT = (FIXTURES / "yangjibao_holdings_no_daily_ocr.txt").read_text(encoding="utf-8")

DETAIL_TEXT = """
华夏中证电网设备主题ETF联接A
025856
持有金额
持有份额
持仓占比
15,075.46
10,645.76
52.76%
持有收益
持有收益率
持仓成本
+401.80
+2.74%
1.3784
"""


def test_parse_portfolio_summary_from_yangjibao_overview():
    summary = parse_portfolio_summary_from_text(OVERVIEW_TEXT)

    assert summary is not None
    assert summary.total_assets == 28572.36
    assert summary.daily_profit == -363.10


def test_sync_profiles_from_overview_holdings(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    refresh_settings()

    service = FundProfileService()
    service.save_profile(parse_profile_from_text(DETAIL_TEXT))

    holdings = FundProfileService().resolve_holdings(parse_holdings_from_text(OVERVIEW_TEXT))
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
