from __future__ import annotations

from app.services import eastmoney_spot_client, sector_canonical, sector_intraday_provider
from app.services.sector_quote_identity import provider_identity_matches
from app.services.sector_registry import (
    resolve_discovery_quote,
    resolve_market_quote,
    resolve_theme_sector_label,
)
from app.services.sector_registry_data import THEME_BOARD_INDEX


def test_high_risk_sector_registry_uses_exact_market_namespaces() -> None:
    assert THEME_BOARD_INDEX["恒生科技"] == ("124.HSTECH", "HSTECH", "index")
    assert THEME_BOARD_INDEX["保险"] == ("0.399809", "399809", "index")


def test_all_registered_theme_quotes_are_the_canonical_market_source() -> None:
    for label, expected in THEME_BOARD_INDEX.items():
        registry = resolve_market_quote(label)
        canonical = sector_canonical.get_canonical_sector(label)
        intraday = sector_canonical.get_intraday_canonical_sector(label)

        assert registry is not None, label
        assert canonical is not None, label
        assert intraday is not None, label
        expected_identity = (expected[0], expected[1], expected[2])
        assert (
            registry.eastmoney_secid,
            registry.source_code,
            registry.source_type,
        ) == expected_identity, label
        assert (
            canonical.eastmoney_secid,
            canonical.source_code,
            canonical.source_type,
        ) == expected_identity, label
        assert (
            intraday.eastmoney_secid,
            intraday.source_code,
            intraday.source_type,
        ) == expected_identity, label


def test_intraday_request_hint_is_resolved_by_the_registry() -> None:
    assert sector_intraday_provider.resolve_intraday_source(
        "concept",
        "互联网",
    ) == ("index", "互联网")


def test_semiconductor_materials_index_name_does_not_collapse_to_semiconductor() -> None:
    """合同跟踪名含「半导体」，但不能落到更宽的中证半导体 H30184。

    2026-08-21：931743 收盘 -0.42%，H30184 收盘 +0.34%。详情分时若走错标的，
    会和官方净值（联接跟踪 931743）完全对不上。
    """

    long_name = "中证半导体材料设备主题指数"
    materials = sector_canonical.get_canonical_sector(long_name)
    semiconductor = sector_canonical.get_canonical_sector("半导体")

    assert materials is not None
    assert materials.source_code == "931743"
    assert materials.label == "半导体材料"
    assert semiconductor is not None
    assert semiconductor.source_code == "H30184"
    assert sector_intraday_provider.resolve_intraday_source(
        "index",
        long_name,
    ) == ("index", "半导体材料")
    assert resolve_theme_sector_label(long_name) == "半导体材料"
    assert resolve_theme_sector_label("中证半导体指数") == "半导体"


def test_market_identity_and_fund_flow_identity_are_explicitly_separate() -> None:
    market = resolve_market_quote("半导体")
    flow = resolve_discovery_quote("半导体")

    assert market is not None
    assert flow is not None
    assert (market.eastmoney_secid, market.source_code) == ("2.H30184", "H30184")
    assert (flow.eastmoney_secid, flow.source_code) == ("90.BK1036", "BK1036")
    assert resolve_theme_sector_label("中证半导体指数") == "半导体"


def test_unregistered_label_does_not_substring_match_an_unrelated_sector() -> None:
    assert sector_canonical.get_canonical_sector("非银行金融") is None


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

    monkeypatch.setattr(
        eastmoney_spot_client,
        "eastmoney_httpx_client",
        lambda **kwargs: FakeClient(**kwargs),
    )

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

    monkeypatch.setattr(
        eastmoney_spot_client,
        "eastmoney_httpx_client",
        lambda **kwargs: FakeClient(**kwargs),
    )

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
