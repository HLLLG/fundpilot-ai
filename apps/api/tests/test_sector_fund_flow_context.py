from __future__ import annotations

from app.services import sector_fund_flow_context as flow_module


def test_theme_snapshot_supplies_flow_by_label_without_board_code(monkeypatch):
    monkeypatch.setattr(
        flow_module,
        "resolve_board_flow_code_for_sector",
        lambda _label: (None, None),
    )
    monkeypatch.setattr(
        flow_module,
        "_matching_theme_board_snapshot",
        lambda _trade_date: {
            "trade_date": "2026-07-15",
            "items": [
                {
                    "sector_label": "恒生科技",
                    "flow_source_code": None,
                    "flow_data_date": "2026-07-15",
                    "main_force_net_yi": 10.59,
                    "cumulative_5d_net_yi": 22.82,
                    "flow_tiers": {"super_large_net_yi": 7.24},
                }
            ],
        },
    )

    result = flow_module.build_sector_fund_flow_context(
        "恒生科技",
        sector_return_percent=4.96,
        trade_date="2026-07-15",
    )

    assert result is not None
    assert result["available"] is True
    assert result["today_available"] is True
    assert result["five_day_available"] is True
    assert result["five_day_source"] == "eastmoney_rank"
    assert result["today_main_force_net_yi"] == 10.59
    assert result["cumulative_5d_net_yi"] == 22.82
