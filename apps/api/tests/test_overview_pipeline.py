from app.models import Holding, PortfolioSummary
from app.services.holding_estimates import sum_daily_profit
from app.services.overview_pipeline import process_overview_holdings


def test_process_overview_uses_sector_sum_not_ocr_account_daily(monkeypatch):
    monkeypatch.setattr(
        "app.services.overview_pipeline.refresh_holdings_sector_quotes",
        lambda holdings, force_refresh=True: {
            "ok": True,
            "message": "已刷新",
            "holdings": [
                holding.model_copy(update={"sector_return_percent": sector}).model_dump()
                for holding, sector in zip(
                    holdings,
                    [0.61, 4.25, 3.05, 2.29],
                    strict=True,
                )
            ],
            "items": [],
            "summary": {"matched": 4, "unresolved": 0, "needs_mapping": 0},
        },
    )
    monkeypatch.setattr(
        "app.services.overview_pipeline.enrich_holdings_from_profiles",
        lambda holdings: holdings,
    )
    monkeypatch.setattr(
        "app.services.overview_pipeline.bootstrap_holding_baselines",
        lambda holdings, **kwargs: holdings,
    )
    monkeypatch.setattr(
        "app.services.overview_pipeline.sync_holding_amounts_from_shares",
        lambda holdings, **kwargs: holdings,
    )
    monkeypatch.setattr(
        "app.services.overview_pipeline.overlay_official_nav_returns",
        lambda holdings: holdings,
    )

    holdings = [
        Holding(
            fund_code="025856",
            fund_name="华夏中证电网设备主题ETF联接A",
            holding_amount=15144.66,
            return_percent=3.21,
            sector_name="中证电网设备",
        ),
        Holding(
            fund_code="008586",
            fund_name="华夏人工智能ETF联接C",
            holding_amount=8270.43,
            return_percent=-0.49,
            sector_name="中证人工智能",
        ),
        Holding(
            fund_code="519674",
            fund_name="银河创新成长混合A",
            holding_amount=4042.24,
            return_percent=-2.82,
            sector_name="半导体",
        ),
        Holding(
            fund_code="015945",
            fund_name="易方达国防军工混合C",
            holding_amount=1188.96,
            return_percent=-7.43,
            sector_name="商业航天",
        ),
    ]
    summary = PortfolioSummary(total_assets=28289.55, daily_profit=369.84, holding_count=4)

    result, _sector, updated = process_overview_holdings(holdings, portfolio_summary=summary)

    sector_sum = round(sum_daily_profit(result), 2)
    assert sector_sum != 369.84
    assert updated is not None
    assert updated.daily_profit == sector_sum
    assert all(holding.daily_return_percent == holding.sector_return_percent for holding in result)


def test_process_overview_enriches_daily_from_sector_when_no_account_daily(monkeypatch):
    monkeypatch.setattr(
        "app.services.overview_pipeline.refresh_holdings_sector_quotes",
        lambda holdings, force_refresh=True: {
            "ok": True,
            "message": "已刷新 1 只",
            "holdings": [
                holding.model_copy(update={"sector_return_percent": 2.5}).model_dump()
                for holding in holdings
            ],
            "items": [],
            "summary": {"matched": 1, "unresolved": 0, "needs_mapping": 0},
        },
    )
    monkeypatch.setattr(
        "app.services.overview_pipeline.enrich_holdings_from_profiles",
        lambda holdings: holdings,
    )
    monkeypatch.setattr(
        "app.services.overview_pipeline.bootstrap_holding_baselines",
        lambda holdings, **kwargs: holdings,
    )
    monkeypatch.setattr(
        "app.services.overview_pipeline.sync_holding_amounts_from_shares",
        lambda holdings, **kwargs: holdings,
    )
    monkeypatch.setattr(
        "app.services.overview_pipeline.overlay_official_nav_returns",
        lambda holdings: holdings,
    )
    monkeypatch.setattr(
        "app.database.get_fund_profile_by_code",
        lambda code: None,
    )

    holdings = [
        Holding(
            fund_code="008586",
            fund_name="华夏人工智能ETF联接C",
            holding_amount=8000,
            return_percent=3.0,
            sector_name="中证人工智能",
        )
    ]
    result, sector, _summary = process_overview_holdings(holdings)
    assert result[0].sector_return_percent == 2.5
    assert result[0].daily_profit == 200.0
    assert sector["ok"] is True
