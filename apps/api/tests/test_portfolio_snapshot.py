from datetime import datetime, timezone

from app.config import refresh_settings
from app.database import delete_portfolio_snapshots_on_or_before, list_portfolio_daily_snapshots
from app.models import Holding, PortfolioSummary
from app.services.portfolio_snapshot import (
    build_dashboard_payload,
    build_risk_correlation_payload,
    save_daily_snapshot,
    snapshot_date_key,
)


def test_snapshot_roundtrip_and_dashboard(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    refresh_settings()

    monkeypatch.setattr(
        "app.services.portfolio_snapshot.build_profit_trend",
        lambda **_kwargs: {"kind": "intraday", "points": []},
    )

    holdings = [
        Holding(
            fund_code="008586",
            fund_name="华夏人工智能",
            holding_amount=7250.12,
            return_percent=-3.47,
            daily_profit=-176.88,
            daily_return_percent=-2.38,
        )
    ]
    summary = PortfolioSummary(
        total_assets=28090.36,
        daily_profit=-482.0,
        daily_return_percent=-1.72,
        updated_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    snapshot = save_daily_snapshot(holdings, summary)

    rows = list_portfolio_daily_snapshots()
    matching = [row for row in rows if row["snapshot_date"] == snapshot.snapshot_date]
    assert len(matching) == 1
    assert matching[0]["total_assets"] == 28090.36

    monkeypatch.setattr(
        "app.services.portfolio_snapshot.list_portfolio_daily_snapshots",
        lambda **kwargs: matching,
    )

    payload = build_dashboard_payload(summary=summary, profiles=[])
    assert len(payload["history"]) == 1
    assert payload["history"][0]["total_assets"] == 28090.36
    assert len(payload["allocation"]) == 1
    assert payload["allocation"][0]["weight_percent"] > 0


def test_delete_snapshots_on_or_before(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    refresh_settings()

    from app.database import save_portfolio_daily_snapshot
    from app.models import PortfolioDailySnapshot

    for day, profit in [("2026-06-08", 10), ("2026-06-09", 20), ("2026-06-10", -5)]:
        save_portfolio_daily_snapshot(
            PortfolioDailySnapshot(
                snapshot_date=day,
                total_assets=1000,
                daily_profit=profit,
                holdings=[],
            )
        )

    purge = delete_portfolio_snapshots_on_or_before("2026-06-09")
    assert purge["daily_snapshots_deleted"] == 2

    remaining = list_portfolio_daily_snapshots(limit=10)
    assert len(remaining) == 1
    assert remaining[0]["snapshot_date"] == "2026-06-10"


def test_snapshot_date_key_uses_utc_date():
    moment = datetime(2026, 6, 1, 15, 0, tzinfo=timezone.utc)
    assert snapshot_date_key(moment) == "2026-06-01"


def test_build_risk_correlation_payload_with_injected_nav():
    from app.models import FundNavPoint

    days = [f"2026-01-{i + 1:02d}" for i in range(25)]

    def _make_points(multiplier: float) -> list[FundNavPoint]:
        nav = 1.0
        points = [FundNavPoint(date=days[0], nav=nav)]
        for i in range(1, len(days)):
            nav *= 1.0 + multiplier * ((i % 5) - 2) / 100.0
            points.append(FundNavPoint(date=days[i], nav=round(nav, 6)))
        return points

    nav_by_code = {
        "111111": _make_points(1.0),
        "222222": _make_points(2.0),  # 与 111111 完全正相关
    }

    def fake_fetch(code, name, trading_days):
        return nav_by_code[code]

    holdings = [
        Holding(fund_code="111111", fund_name="基金甲", holding_amount=5000),
        Holding(fund_code="222222", fund_name="基金乙", holding_amount=3000),
    ]
    payload = build_risk_correlation_payload(holdings, fetch_nav=fake_fetch)

    assert payload["available"] is True
    assert payload["codes"] == ["111111", "222222"]
    assert payload["matrix"][0][1] == 1.0
    assert payload["max_pair"]["corr"] == 1.0


# ---------------------------------------------------------------------------
# 模块4 竖切3：build_factor_scores_for_facts（紧凑+挂 IC 置信，离线注入）
# ---------------------------------------------------------------------------

from app.services.portfolio_snapshot import build_factor_scores_for_facts  # noqa: E402


def _facts_rank_rows(n: int) -> list[dict]:
    return [
        {
            "fund_code": f"{100000 + i:06d}",
            "fund_name": f"排行基金{i}",
            "return_3m_percent": float(i % 13) - 6,
            "return_6m_percent": float(i % 17) - 8,
            "return_1y_percent": float(i % 23) - 10,
            "max_drawdown_1y_percent": -float(i % 19 + 1),
            "fund_scale_yi": float(i % 50 + 1),
        }
        for i in range(n)
    ]


def test_factor_scores_for_facts_compact_and_reliability():
    holdings = [Holding(fund_code="100005", fund_name="持仓在榜", holding_amount=1000.0)]
    ic_factors = {"momentum": {"mean_ic": 0.04, "significant": True}}
    out = build_factor_scores_for_facts(
        holdings,
        fetch_rank=lambda: _facts_rank_rows(40),
        fetch_nav=lambda code, name, trading_days: [],
        ic_factors=ic_factors,
    )
    assert out["available"] is True
    assert out["universe_size"] == 40
    # 紧凑持仓结构
    h = out["holdings"][0]
    assert h["fund_code"] == "100005"
    assert "composite_grade" in h and "factor_percentiles" in h
    assert set(h["factor_percentiles"].keys()) == {
        "momentum", "risk_adjusted", "drawdown", "size"
    }
    # 因子可信度（IC 背书）
    assert out["factor_reliability"]["momentum"]["level"] == "高"
    assert out["factor_reliability"]["size"]["level"] == "不足"


def test_factor_scores_for_facts_best_effort_on_failure():
    def _boom():
        raise RuntimeError("network down")

    holdings = [Holding(fund_code="100005", fund_name="x", holding_amount=1000.0)]
    out = build_factor_scores_for_facts(
        holdings,
        fetch_rank=_boom,
        fetch_nav=lambda code, name, trading_days: [],
        ic_factors={},
    )
    assert out["available"] is False
    assert "message" in out


# ---------------------------------------------------------------------------
# 模块4 竖切4：build_risk_metrics_for_facts（挂样本充足度置信，best-effort）
# ---------------------------------------------------------------------------

from app.services import portfolio_snapshot as _ps  # noqa: E402


def test_risk_metrics_for_facts_attaches_confidence(monkeypatch):
    monkeypatch.setattr(
        _ps,
        "build_risk_metrics_payload",
        lambda rows, holdings: {"available": True, "sample_days": 150, "sharpe_ratio": 1.2},
    )
    out = _ps.build_risk_metrics_for_facts([{"x": 1}], [])
    assert out["available"] is True
    assert out["confidence"]["level"] == "高"


def test_risk_metrics_for_facts_best_effort_on_failure(monkeypatch):
    def _boom(rows, holdings):
        raise RuntimeError("index down")

    monkeypatch.setattr(_ps, "build_risk_metrics_payload", _boom)
    out = _ps.build_risk_metrics_for_facts([{"x": 1}], [])
    assert out["available"] is False
    assert "message" in out


def test_build_risk_correlation_payload_single_holding_unavailable():
    from app.models import FundNavPoint

    def fake_fetch(code, name, trading_days):
        return [FundNavPoint(date=f"2026-01-{i + 1:02d}", nav=1.0 + i * 0.01) for i in range(25)]

    holdings = [Holding(fund_code="111111", fund_name="基金甲", holding_amount=5000)]
    payload = build_risk_correlation_payload(holdings, fetch_nav=fake_fetch)
    assert payload["available"] is False
