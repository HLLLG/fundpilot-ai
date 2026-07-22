from __future__ import annotations

from app.services import eastmoney_spot_client, sector_canonical, sector_intraday_provider
from app.services.sector_quote_identity import provider_identity_matches
from app.services.sector_registry_data import THEME_BOARD_INDEX


def test_high_risk_sector_registry_uses_exact_market_namespaces() -> None:
    assert THEME_BOARD_INDEX["恒生科技"] == ("124.HSTECH", "HSTECH", "index")
    assert THEME_BOARD_INDEX["保险"] == ("0.399809", "399809", "index")


def test_provider_identity_policy_rejects_unrelated_valid_index() -> None:
    assert provider_identity_matches(
        "恒生科技",
        expected_source_code="HSTECH",
        actual_security_name="恒生科技指数",
        actual_security_code="HSTECH",
    )
    assert not provider_identity_matches(
        "恒生科技",
        expected_source_code="CESHKB",
        actual_security_name="中华香港生物科技",
        actual_security_code="CESHKB",
    )
    assert provider_identity_matches(
        "保险",
        expected_source_code="399809",
        actual_security_name="保险主题",
        actual_security_code="399809",
    )
    assert not provider_identity_matches(
        "恒生科技",
        expected_source_code="HSTECH",
        actual_security_name="恒生",
        actual_security_code="HSTECH",
    )


def test_single_quote_identity_preserves_numeric_zero_market_id(monkeypatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "data": {
                    "f12": "399809",
                    "f13": 0,
                    "f14": "保险主题",
                    "f3": -1.25,
                }
            }

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def get(self, *_args, **_kwargs) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(eastmoney_spot_client.httpx, "Client", FakeClient)

    assert eastmoney_spot_client.fetch_eastmoney_quote_by_secid(
        "0.399809",
        max_retries=1,
    ) == ("保险主题", -1.25)


def test_single_quote_falls_back_to_identity_joined_batch_payload(monkeypatch) -> None:
    class FakeResponse:
        def __init__(self, payload: dict) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self.payload

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def get(self, url: str, *_args, **_kwargs) -> FakeResponse:
            if "/ulist.np/" in url:
                return FakeResponse(
                    {
                        "data": {
                            "diff": [
                                {
                                    "f12": "HSTECH",
                                    "f13": 124,
                                    "f14": "恒生科技指数",
                                    "f3": -3.04,
                                }
                            ]
                        }
                    }
                )
            return FakeResponse({"data": {}})

    monkeypatch.setattr(eastmoney_spot_client.httpx, "Client", FakeClient)

    assert eastmoney_spot_client.fetch_eastmoney_quote_by_secid(
        "124.HSTECH",
        max_retries=1,
    ) == ("恒生科技指数", -3.04)


def test_canonical_quote_fails_closed_before_kline_on_identity_mismatch(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        sector_canonical,
        "fetch_eastmoney_quote_by_secid",
        lambda *_args, **_kwargs: ("中华香港生物科技", 0.84),
    )

    def unexpected_kline(*_args, **_kwargs):
        raise AssertionError("identity mismatch must block kline lookup")

    monkeypatch.setattr(
        sector_canonical,
        "fetch_eastmoney_kline_close_percent",
        unexpected_kline,
    )

    assert sector_canonical.fetch_canonical_sector_quote("恒生科技", {}) is None


def test_canonical_quote_accepts_verified_hang_seng_tech_identity(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        sector_canonical,
        "fetch_eastmoney_quote_by_secid",
        lambda *_args, **_kwargs: ("恒生科技指数", -3.21),
    )
    monkeypatch.setattr(
        sector_canonical,
        "fetch_eastmoney_kline_close_percent",
        lambda *_args, **_kwargs: -3.23,
    )
    monkeypatch.setattr(
        sector_canonical,
        "build_trading_session",
        lambda *_args, **_kwargs: {"effective_trade_date": "2026-07-22"},
    )

    result = sector_canonical.fetch_canonical_sector_quote("恒生科技", {})

    assert result is not None
    assert result.change_percent == -3.23
    assert result.source_code == "HSTECH"


def test_intraday_fails_closed_when_provider_identity_cannot_be_verified(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        sector_intraday_provider,
        "fetch_eastmoney_quote_by_secid",
        lambda *_args, **_kwargs: (None, None),
    )

    def unexpected_intraday(*_args, **_kwargs):
        raise AssertionError("unverified identity must block intraday lookup")

    monkeypatch.setattr(
        sector_intraday_provider,
        "_fetch_intraday_minute_chain",
        unexpected_intraday,
    )

    assert sector_intraday_provider._fetch_index_intraday("恒生科技") == []
