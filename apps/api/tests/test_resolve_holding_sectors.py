from app.models import Holding
from app.config import refresh_settings
from app.services.fund_profile import FundProfileService, parse_profile_from_text
from tests.test_yangjibao_four_funds import FUND_519674


def test_resolve_holding_replaces_invalid_sector_from_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    refresh_settings()
    service = FundProfileService()
    profile = parse_profile_from_text(FUND_519674)
    assert profile is not None
    service.save_profile(profile)

    resolved = service.resolve_holding(
        Holding(
            fund_code="519674",
            fund_name=profile.fund_name,
            holding_amount=4042.24,
            return_percent=1.94,
            sector_name="+",
            sector_return_percent=4.01,
        )
    )
    assert resolved.sector_name == "半导体"
    assert resolved.intraday_index_name == "中证半导体"
