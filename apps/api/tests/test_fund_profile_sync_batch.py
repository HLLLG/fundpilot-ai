from __future__ import annotations

from app.models import FundProfile, Holding
from app.services.fund_profile import FundProfileService, provisional_code_for_name


def _holding(code: str, name: str, amount: float) -> Holding:
    return Holding(
        fund_code=code,
        fund_name=name,
        holding_amount=amount,
        holding_return_percent=2.0,
        return_percent=2.0,
    )


def test_sync_profiles_empty_batch_does_not_load_profiles(monkeypatch):
    monkeypatch.setattr(
        "app.services.fund_profile.list_fund_profiles",
        lambda: (_ for _ in ()).throw(
            AssertionError("empty sync must not load profiles")
        ),
    )

    result = FundProfileService().sync_profiles_from_holdings([])

    assert result.updated == 0
    assert result.created == 0


def test_sync_profiles_uses_one_mutable_profile_snapshot(monkeypatch):
    existing = FundProfile(
        fund_code="111111",
        fund_name="Alpha Core Fund",
        aliases=["Alpha Legacy Alias"],
        holding_amount=10.0,
    )
    provisional = FundProfile(
        fund_code="900001",
        fund_name="Beta Provisional Fund",
        aliases=["Beta Legacy Alias"],
        holding_amount=20.0,
        first_seen_date="2026-06-01",
        is_provisional=True,
    )
    wrong_code = FundProfile(
        fund_code="222222",
        fund_name="Gamma Corrected Fund",
        aliases=["Gamma Legacy Alias"],
        holding_amount=30.0,
    )
    state = {
        profile.fund_code: profile
        for profile in (existing, provisional, wrong_code)
    }
    calls = {"list": 0, "point": 0, "saved": [], "deleted": []}

    def _list_profiles():
        calls["list"] += 1
        return list(state.values())

    def _point_query(_code: str):
        calls["point"] += 1
        raise AssertionError("batch sync must not issue point profile queries")

    def _save(profile: FundProfile) -> FundProfile:
        state[profile.fund_code] = profile
        calls["saved"].append(profile)
        return profile

    def _delete(code: str) -> bool:
        calls["deleted"].append(code)
        return state.pop(code, None) is not None

    monkeypatch.setattr("app.services.fund_profile.list_fund_profiles", _list_profiles)
    monkeypatch.setattr("app.services.fund_profile.get_fund_profile_by_code", _point_query)
    monkeypatch.setattr("app.services.fund_profile.save_fund_profile", _save)
    monkeypatch.setattr("app.services.fund_profile.delete_fund_profile", _delete)
    monkeypatch.setattr(
        "app.services.fund_profile.FundProfileService.find_match",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("batch save must match names from the mutable map")
        ),
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.upsert_primary_sector_from_profile",
        lambda *_args, **_kwargs: None,
    )

    unresolved_name = "Delta Unresolved Fund"
    result = FundProfileService().sync_profiles_from_holdings(
        [
            _holding("111111", "Alpha Core Fund", 100.0),
            _holding("000000", "Alpha Legacy Alias", 110.0),
            _holding("444444", "Epsilon New Fund", 200.0),
            _holding("444444", "Epsilon New Fund", 250.0),
            _holding("555555", "Beta Provisional Fund", 300.0),
            _holding("666666", "Gamma Corrected Fund", 400.0),
            _holding("000000", unresolved_name, 500.0),
            _holding("000000", unresolved_name, 550.0),
        ]
    )

    provisional_delta_code = provisional_code_for_name(unresolved_name)
    assert calls["list"] == 1
    assert calls["point"] == 0
    assert calls["deleted"] == ["900001", "222222"]
    assert len(calls["saved"]) == 8
    assert result.updated == 4
    assert result.created == 4
    assert set(state) == {
        "111111",
        "444444",
        "555555",
        "666666",
        provisional_delta_code,
    }
    assert state["111111"].holding_amount == 110.0
    assert state["444444"].holding_amount == 250.0
    assert state["555555"].holding_amount == 300.0
    assert state["555555"].is_provisional is False
    assert state["555555"].first_seen_date == "2026-06-01"
    assert "Beta Legacy Alias" in state["555555"].aliases
    assert state["666666"].holding_amount == 400.0
    assert state[provisional_delta_code].holding_amount == 550.0
    assert state[provisional_delta_code].is_provisional is True


def test_sync_profiles_reuses_primary_sector_batch_context(monkeypatch):
    from app.services.fund_primary_sector_service import PrimarySectorBatchContext

    profile = FundProfile(
        fund_code="123456",
        fund_name="Sector Fund",
        aliases=[],
        holding_amount=100.0,
        sector_name="半导体",
    )
    state = {profile.fund_code: profile}
    calls = {"list": 0, "context": 0, "point_sector": 0, "sector_save": 0}

    def _list_profiles():
        calls["list"] += 1
        return list(state.values())

    def _load_context(codes, *, profiles):
        calls["context"] += 1
        assert set(codes) == {"123456"}
        return PrimarySectorBatchContext(
            profiles_by_code={item.fund_code: item for item in profiles},
        )

    def _point_sector(_code: str):
        calls["point_sector"] += 1
        raise AssertionError("primary-sector point query should not run")

    def _save_sector(**kwargs):
        calls["sector_save"] += 1
        return kwargs

    def _save_profile(current: FundProfile) -> FundProfile:
        state[current.fund_code] = current
        return current

    monkeypatch.setattr("app.services.fund_profile.list_fund_profiles", _list_profiles)
    monkeypatch.setattr(
        "app.services.fund_profile.get_fund_profile_by_code",
        lambda _code: (_ for _ in ()).throw(
            AssertionError("profile point query should not run")
        ),
    )
    monkeypatch.setattr(
        "app.services.fund_profile.save_fund_profile",
        _save_profile,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.PrimarySectorBatchContext.load",
        _load_context,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        _point_sector,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.save_fund_primary_sector",
        _save_sector,
    )

    result = FundProfileService().sync_profiles_from_holdings(
        [
            _holding("123456", "Sector Fund", 120.0).model_copy(
                update={"sector_name": "半导体"}
            ),
            _holding("123456", "Sector Fund", 130.0).model_copy(
                update={"sector_name": "半导体"}
            ),
        ]
    )

    assert result.updated == 2
    assert calls == {
        "list": 1,
        "context": 1,
        "point_sector": 0,
        "sector_save": 2,
    }
