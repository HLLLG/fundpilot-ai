"""519674 等：关联板块涨跌走概念半导体，分时图仍走中证半导体指数。"""

from app.models import FundProfile, Holding
from app.services.fund_profile import FundProfileService
from app.services.sector_quote_label import sector_quote_lookup_label


def test_519674_lookup_uses_concept_board_not_inferred_index(tmp_path, monkeypatch):
    from app.config import get_settings
    from app.database import save_fund_profile

    db_path = tmp_path / "test.db"
    monkeypatch.setenv("FUND_AI_DB_PATH", str(db_path))
    get_settings.cache_clear()

    profile = FundProfile(
        fund_code="519674",
        fund_name="银河创新成长混合A",
        sector_name="半导体",
        intraday_index_name="中证半导体",
    )
    save_fund_profile(profile)

    holding = Holding(
        fund_code="519674",
        fund_name="银河创新成长混合A",
        holding_amount=4042.24,
        return_percent=1.94,
        sector_name="半导体",
    )
    resolved = FundProfileService().resolve_holding(holding)
    assert resolved.intraday_index_name == "中证半导体"
    assert sector_quote_lookup_label(resolved, profile=profile) == "半导体"


def test_sanitize_strips_inferred_index_from_semiconductor_profile(tmp_path, monkeypatch):
    from app.config import get_settings
    from app.database import get_fund_profile_by_code, save_fund_profile
    from app.services.fund_profile import _sanitize_profile_sector_fields

    db_path = tmp_path / "test.db"
    monkeypatch.setenv("FUND_AI_DB_PATH", str(db_path))
    get_settings.cache_clear()

    save_fund_profile(
        FundProfile(
            fund_code="519674",
            fund_name="银河创新成长混合A",
            sector_name="半导体",
            intraday_index_name="中证半导体",
        )
    )
    profile = get_fund_profile_by_code("519674")
    assert profile is not None
    cleaned = _sanitize_profile_sector_fields(profile)
    assert cleaned.intraday_index_name is None
    assert cleaned.sector_name == "半导体"
