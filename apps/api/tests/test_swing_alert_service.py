from app.models import Holding, InvestorProfile


def test_swing_alerts_evaluate_and_dedupe(tmp_path, monkeypatch):
    from tests.conftest import auth_client_for_db

    client = auth_client_for_db(monkeypatch, tmp_path / "swing.db")
    monkeypatch.setattr(
        "app.services.swing_alert_engine.build_trading_session",
        lambda when=None: {
            "effective_trade_date": "2026-06-16",
            "session_kind": "trading_day_intraday",
        },
    )
    monkeypatch.setattr(
        "app.services.swing_alert_service.build_sector_heat_ranking",
        lambda **kwargs: [],
    )

    body = {
        "holdings": [
            {
                "fund_code": "519674",
                "fund_name": "银河创新成长",
                "holding_amount": 35000,
                "return_percent": 0,
                "holding_return_percent": 2.0,
                "sector_return_percent": 0.8,
                "sector_name": "半导体",
            }
        ],
        "profile": {
            "style": "激进",
            "horizon": "3-7天",
            "max_drawdown_percent": 12,
            "concentration_limit_percent": 40,
            "prefer_dca": False,
            "avoid_chasing": False,
            "decision_style": "aggressive",
            "investment_preset": "aggressive_swing",
            "round_trip_fee_percent": 1.5,
            "min_net_profit_percent": 1.0,
            "hold_days_target": 7,
            "swing_alerts_enabled": True,
            "swing_monitor_scope": "holdings",
        },
        "monitor_scope": "holdings",
    }

    first = client.post("/api/swing-alerts/evaluate", json=body)
    assert first.status_code == 200
    payload = first.json()
    assert payload["alerts_enabled"] is True
    assert payload["new_count"] >= 1

    second = client.post("/api/swing-alerts/evaluate", json=body)
    assert second.status_code == 200
    again = second.json()
    assert again["new_count"] == 0

    today = client.get("/api/swing-alerts/today", params={"trade_date": "2026-06-16"})
    assert today.status_code == 200
    assert len(today.json()["items"]) >= 1
