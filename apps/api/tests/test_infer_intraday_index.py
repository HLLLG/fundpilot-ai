from app.models import Holding
from app.services.fund_profile import (
    FundProfileService,
    infer_intraday_index_from_fund_name,
    infer_intraday_index_from_sector,
)
from app.services.sector_quote_label import sector_quote_lookup_label


def test_infer_intraday_index_from_etf_feeder_name():
    assert infer_intraday_index_from_fund_name("华夏中证电网设备主题ETF联接A") == "中证电网设备"


def test_infer_intraday_index_from_ai_etf_feeder_without_csi_prefix():
    assert infer_intraday_index_from_fund_name("华夏人工智能ETF联接C") == "中证人工智能"


def test_infer_intraday_index_from_semiconductor_board():
    assert infer_intraday_index_from_sector("半导体") == "中证半导体"


def test_infer_intraday_index_from_commercial_aerospace_has_no_csi_index():
    assert infer_intraday_index_from_sector("商业航天") is None


def test_resolve_holding_restores_csi_grid_index_for_025856(tmp_path, monkeypatch):
    from app.database import save_fund_profile
    from app.models import FundProfile

    db_path = tmp_path / "test.db"
    monkeypatch.setenv("FUND_AI_DB_PATH", str(db_path))
    from app.config import get_settings

    get_settings.cache_clear()

    save_fund_profile(
        FundProfile(
            fund_code="025856",
            fund_name="华夏中证电网设备主题ETF联接A",
            sector_name="电网设备",
            intraday_index_name=None,
        )
    )

    holding = Holding(
        fund_code="025856",
        fund_name="华夏中证电网设备主题ETF联接A",
        holding_amount=9663.64,
        return_percent=4.15,
        sector_name="电网设备",
        intraday_index_name=None,
    )
    resolved = FundProfileService().resolve_holding(holding)
    assert resolved.intraday_index_name == "中证电网设备"
    assert sector_quote_lookup_label(resolved) == "中证电网设备"


def test_resolve_holding_restores_csi_ai_index_for_008586(tmp_path, monkeypatch):
    from app.database import save_fund_profile
    from app.models import FundProfile

    db_path = tmp_path / "test.db"
    monkeypatch.setenv("FUND_AI_DB_PATH", str(db_path))
    from app.config import get_settings

    get_settings.cache_clear()

    save_fund_profile(
        FundProfile(
            fund_code="008586",
            fund_name="华夏人工智能ETF联接C",
            sector_name="中证人工智能",
            intraday_index_name=None,
        )
    )

    holding = Holding(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=8527.77,
        return_percent=1.8,
        sector_name="中证人工智能",
        intraday_index_name=None,
    )
    resolved = FundProfileService().resolve_holding(holding)
    assert resolved.intraday_index_name == "中证人工智能"
    assert resolved.sector_name == "人工智能"
    assert sector_quote_lookup_label(resolved) == "中证人工智能"
