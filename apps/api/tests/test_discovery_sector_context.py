from __future__ import annotations

from app.services import discovery_sector_context as context_module


def test_target_sector_context_reuses_opportunity_flow_facts(monkeypatch):
    preloaded = {
        "半导体材料": {
            "available": True,
            "sector_label": "半导体材料",
            "trade_date": "2026-07-21",
            "flow_date": "2026-07-21",
            "date_aligned": True,
            "today_main_force_net_yi": 6.6,
            "cumulative_5d_net_yi": -174.71,
            "today_available": True,
            "five_day_available": True,
        }
    }
    monkeypatch.setattr(
        context_module,
        "build_sector_fund_flow_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("preloaded opportunity flow should be reused")
        ),
    )
    monkeypatch.setattr(context_module, "summarize_sector_intraday_for_label", lambda _label: None)
    monkeypatch.setattr(context_module, "signal_backtest_for_sector", lambda *_args: None)

    result = context_module.build_target_sector_context(
        ["半导体材料"],
        [{"sector_label": "半导体材料", "change_1d_percent": 11.42}],
        {},
        trade_date="2026-07-21",
        sector_flow_by_label=preloaded,
    )

    assert result[0]["sector_fund_flow"]["today_main_force_net_yi"] == 6.6
    assert result[0]["sector_fund_flow"]["date_aligned"] is True
