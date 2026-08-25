"""季报重仓加权估当日：养基宝计算器口径，不跟关联板块。"""

from app.models import Holding
from app.services.fund_holdings_return_estimate import (
    HoldingsReturnEstimate,
    apply_holdings_daily_estimates,
    compute_holdings_weighted_return,
    estimate_holdings_weighted_returns,
    holding_row_secid,
    holdings_missing_weighted_daily,
    overlay_holdings_daily_estimates,
    should_use_holdings_weighted_daily,
)
from app.services.holding_estimates import (
    apply_sector_daily_estimates,
    compute_estimated_holding_return_percent,
    holding_daily_return_is_estimated,
    resolve_intraday_return_percent,
)


def _row(
    code: str,
    weight: float,
    *,
    security_id: str | None = None,
) -> dict:
    payload = {"security_code": code, "weight_percent": weight}
    if security_id is not None:
        payload["security_id"] = security_id
    return payload


def test_weighted_return_matches_yangjibao_calculator() -> None:
    """012200 前十大披露约 72%：加权 = Σ(r_i × w_i)/100，含港股 01347。"""

    rows = [
        _row("688409", 9.18, security_id="CN:688409"),
        _row("688037", 8.82, security_id="CN:688037"),
        _row("300604", 8.07, security_id="CN:300604"),
        _row("300567", 7.84, security_id="CN:300567"),
        _row("300666", 7.17, security_id="CN:300666"),
        _row("01347", 6.96, security_id="HK:01347"),
        _row("688019", 6.94, security_id="CN:688019"),
        _row("688072", 6.27, security_id="CN:688072"),
        _row("688172", 5.79, security_id="CN:688172"),
        _row("688627", 5.22, security_id="CN:688627"),
    ]
    quotes = {
        "1.688409": 1.0,
        "1.688037": 1.0,
        "0.300604": 1.0,
        "0.300567": 1.0,
        "0.300666": 1.0,
        "116.01347": 1.0,
        "1.688019": 1.0,
        "1.688072": 1.0,
        "1.688172": 1.0,
        "1.688627": 1.0,
    }
    estimate = compute_holdings_weighted_return(rows, quotes)
    assert estimate is not None
    assert estimate.disclosed_weight_percent == 72.26
    assert estimate.quoted_weight_percent == 72.26
    assert estimate.change_percent == 0.7226
    assert estimate.source == "holdings_estimate"


def test_mixed_cn_hk_quotes_use_disclosed_weights() -> None:
    rows = [
        _row("688409", 9.18, security_id="CN:688409"),
        _row("01347", 6.96, security_id="HK:01347"),
    ]
    estimate = compute_holdings_weighted_return(
        rows,
        {"1.688409": 1.0, "116.01347": 2.0},
    )
    assert estimate is not None
    assert estimate.change_percent == 0.231


def test_does_not_renormalize_sleeve_to_full_nav() -> None:
    rows = [_row("600000", 72.24, security_id="CN:600000")]
    estimate = compute_holdings_weighted_return(rows, {"1.600000": 1.0})
    assert estimate is not None
    assert estimate.change_percent == 0.7224


def test_quoted_coverage_below_threshold_returns_none() -> None:
    rows = [
        _row("600000", 10.0, security_id="CN:600000"),
        _row("600001", 62.24, security_id="CN:600001"),
    ]
    estimate = compute_holdings_weighted_return(rows, {"1.600000": 1.0})
    assert estimate is None


def test_secid_maps_cn_and_hk() -> None:
    assert holding_row_secid(_row("688409", 9.18, security_id="CN:688409")) == "1.688409"
    assert holding_row_secid(_row("300502", 8.0, security_id="CN:300502")) == "0.300502"
    assert holding_row_secid(_row("01347", 6.96, security_id="HK:01347")) == "116.01347"
    assert holding_row_secid(_row("NVDA", 5.0)) is None


def test_holdings_missing_weighted_daily_detects_unthemed_gap() -> None:
    missing = Holding(
        fund_code="017787",
        fund_name="万家宏观择时多策略混合C",
        holding_amount=1000,
    )
    estimated = missing.model_copy(
        update={
            "daily_return_percent": -0.76,
            "daily_return_percent_source": "holdings_estimate",
        }
    )
    official = missing.model_copy(
        update={
            "daily_return_percent": 0.5,
            "daily_return_percent_source": "official_nav",
        }
    )
    assert holdings_missing_weighted_daily([missing]) is True
    assert holdings_missing_weighted_daily([estimated]) is False
    assert holdings_missing_weighted_daily([official]) is False


def test_should_use_holdings_weighted_daily_skips_passive_and_qdii() -> None:
    active = Holding(
        fund_code="012200",
        fund_name="新华鑫科技3个月滚动持有灵活配置混合A",
        holding_amount=1000,
    )
    passive = Holding(
        fund_code="015788",
        fund_name="鹏扬中证数字经济主题ETF联接C",
        holding_amount=1000,
    )
    qdii = Holding(
        fund_code="006282",
        fund_name="易方达纳斯达克100ETF联接QDII",
        holding_amount=1000,
    )
    assert should_use_holdings_weighted_daily(active) is True
    assert should_use_holdings_weighted_daily(passive) is False
    assert should_use_holdings_weighted_daily(qdii) is False


def test_batch_estimate_skips_index_fund_and_uses_snapshot(
    monkeypatch,
) -> None:
    snapshot = {
        "qualified": True,
        "holdings": [
            _row("600000", 40.0, security_id="CN:600000"),
            _row("000001", 32.24, security_id="CN:000001"),
        ],
    }

    def fake_snapshot(fund_code: str, *, allow_live: bool):
        return snapshot if fund_code == "012200" else None

    monkeypatch.setattr(
        "app.services.fund_holdings_return_estimate._load_qualified_snapshot",
        fake_snapshot,
    )
    monkeypatch.setattr(
        "app.services.fund_holdings_return_estimate._load_quote_changes",
        lambda secids, allow_fetch: {"1.600000": 1.0, "0.000001": -0.5},
    )
    result = estimate_holdings_weighted_returns(
        [
            Holding(
                fund_code="012200",
                fund_name="新华鑫科技3个月滚动持有灵活配置混合A",
                holding_amount=1000,
            ),
            Holding(
                fund_code="015788",
                fund_name="鹏扬中证数字经济主题ETF联接C",
                holding_amount=1000,
            ),
        ],
        allow_fetch=False,
    )
    assert "015788" not in result
    assert result["012200"].change_percent == 0.2388


def test_apply_does_not_override_official_nav() -> None:
    official = Holding(
        fund_code="012200",
        fund_name="新华鑫科技3个月滚动持有灵活配置混合A",
        holding_amount=10000,
        daily_return_percent=0.24,
        daily_profit=24.0,
        daily_return_percent_source="official_nav",
        sector_return_percent=-0.42,
    )
    updated = apply_holdings_daily_estimates(
        [official],
        {"012200": HoldingsReturnEstimate(0.17, 72.24, 72.24, 10)},
    )
    assert updated[0].daily_return_percent == 0.24
    assert updated[0].daily_return_percent_source == "official_nav"


def test_apply_overrides_existing_sector_estimate() -> None:
    holding = Holding(
        fund_code="012200",
        fund_name="新华鑫科技3个月滚动持有灵活配置混合A",
        holding_amount=10000,
        daily_return_percent=-0.42,
        daily_return_percent_source="sector_estimate",
        sector_return_percent=-0.42,
    )
    updated = apply_holdings_daily_estimates(
        [holding],
        {"012200": HoldingsReturnEstimate(0.17, 72.24, 72.24, 10)},
    )
    assert updated[0].daily_return_percent == 0.17
    assert updated[0].daily_return_percent_source == "holdings_estimate"
    assert updated[0].sector_return_percent == -0.42


def test_apply_writes_holdings_estimate_and_keeps_sector_column() -> None:
    holding = Holding(
        fund_code="012200",
        fund_name="新华鑫科技3个月滚动持有灵活配置混合A",
        holding_amount=10000,
        sector_return_percent=-0.42,
        sector_name="半导体材料",
    )
    updated = apply_holdings_daily_estimates(
        [holding],
        {"012200": HoldingsReturnEstimate(0.17, 72.24, 72.24, 10)},
    )
    assert updated[0].daily_return_percent == 0.17
    assert updated[0].daily_return_percent_source == "holdings_estimate"
    assert updated[0].daily_profit == 17.0
    assert updated[0].sector_return_percent == -0.42
    assert updated[0].sector_name == "半导体材料"


def test_enrich_preserves_holdings_estimate() -> None:
    holding = Holding(
        fund_code="012200",
        fund_name="新华鑫科技3个月滚动持有灵活配置混合A",
        holding_amount=10000,
        holding_return_percent=10.0,
        daily_return_percent=0.17,
        daily_profit=17.0,
        daily_return_percent_source="holdings_estimate",
        sector_return_percent=-0.42,
    )
    preserved = apply_sector_daily_estimates(holding)
    assert preserved.daily_return_percent == 0.17
    assert preserved.daily_return_percent_source == "holdings_estimate"
    assert resolve_intraday_return_percent(preserved) == 0.17
    assert holding_daily_return_is_estimated(preserved) is True
    assert (
        compute_estimated_holding_return_percent(
            preserved,
            session_kind="trading_day_intraday",
        )
        == 10.17
    )


def test_overlay_writes_holdings_estimate(monkeypatch) -> None:
    holding = Holding(
        fund_code="012200",
        fund_name="新华鑫科技3个月滚动持有灵活配置混合A",
        holding_amount=10000,
    )
    monkeypatch.setattr(
        "app.services.fund_holdings_return_estimate.estimate_holdings_weighted_returns",
        lambda *args, **kwargs: {
            "012200": HoldingsReturnEstimate(0.17, 72.24, 72.24, 10)
        },
    )
    updated = overlay_holdings_daily_estimates([holding])
    assert updated[0].daily_return_percent == 0.17
    assert updated[0].daily_return_percent_source == "holdings_estimate"
    assert updated[0].daily_profit == 17.0


def test_sector_quote_service_does_not_import_fundgz_fallback() -> None:
    import ast
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "app" / "services" / "sector_quote_service.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert "app.services.fund_estimate_provider" not in imported
    assert "fetch_fund_estimate_quotes" not in imported
