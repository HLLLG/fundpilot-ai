from app.models import FundProfile, Holding
from app.services import ocr_pipeline


class _TrackingProfileService:
    def __init__(self, profiles: list[FundProfile]) -> None:
        self._profiles = profiles
        self.list_calls = 0

    def list_profiles(self) -> list[FundProfile]:
        self.list_calls += 1
        return self._profiles


def _holding(
    code: str,
    *,
    official: bool = True,
    amount: float = 100.0,
) -> Holding:
    return Holding(
        fund_code=code,
        fund_name=f"Fund {code}",
        holding_amount=amount,
        daily_profit=1.0 if official else None,
        daily_return_percent_source="official_nav" if official else "sector_estimate",
    )


def test_pin_confirmed_settlements_loads_profiles_once_and_saves_each_code_once(
    monkeypatch,
) -> None:
    profiles = [
        FundProfile(
            fund_code="000001",
            fund_name="Fund One",
            aliases=["Fund 1", "Legacy One"],
        ),
        FundProfile(fund_code="000002", fund_name="Fund Two"),
    ]
    service = _TrackingProfileService(profiles)
    saved: list[FundProfile] = []

    def save(profile: FundProfile) -> FundProfile:
        saved.append(profile)
        return profile

    monkeypatch.setattr(ocr_pipeline, "save_fund_profile", save)

    result = ocr_pipeline._pin_confirmed_holding_settlements(
        [
            _holding("000001", amount=101.0),
            _holding("000001", amount=102.0),
            _holding("000002", amount=201.0),
            _holding("000003", official=False, amount=301.0),
        ],
        trade_date="2026-07-13",
        profile_service=service,
    )

    assert service.list_calls == 1
    assert [profile.fund_code for profile in saved] == ["000001", "000002"]
    assert all(profile.profit_settled_trade_date == "2026-07-13" for profile in saved)
    assert saved[0].aliases == ["Fund 1", "Legacy One"]
    assert [holding.settled_holding_amount for holding in result] == [
        101.0,
        102.0,
        201.0,
        301.0,
    ]
    assert [holding.amount_includes_today for holding in result] == [
        True,
        True,
        True,
        None,
    ]


def test_pin_confirmed_settlements_never_updates_same_name_wrong_code(
    monkeypatch,
) -> None:
    wrong_code_profile = FundProfile(
        fund_code="999999",
        fund_name="Fund 000123",
        aliases=["Fund 000123"],
    )
    service = _TrackingProfileService([wrong_code_profile])
    saved: list[FundProfile] = []
    monkeypatch.setattr(
        ocr_pipeline,
        "save_fund_profile",
        lambda profile: saved.append(profile) or profile,
    )

    result = ocr_pipeline._pin_confirmed_holding_settlements(
        [_holding("000123")],
        trade_date="2026-07-13",
        profile_service=service,
    )

    assert service.list_calls == 1
    assert saved == []
    assert result[0].amount_includes_today is True
    assert wrong_code_profile.profit_settled_trade_date is None


def test_pin_confirmed_settlements_skips_profile_query_without_official_rows(
    monkeypatch,
) -> None:
    service = _TrackingProfileService([])
    monkeypatch.setattr(
        ocr_pipeline,
        "save_fund_profile",
        lambda _profile: (_ for _ in ()).throw(AssertionError("unexpected save")),
    )

    result = ocr_pipeline._pin_confirmed_holding_settlements(
        [_holding("000001", official=False)],
        trade_date="2026-07-13",
        profile_service=service,
    )

    assert service.list_calls == 0
    assert result[0].settled_holding_amount == 100.0
    assert result[0].amount_includes_today is None
