from __future__ import annotations

from concurrent.futures import Future

from app.services import sector_fund_flow_context as flow_module
from app.services import sector_opportunity_scoring as scoring_module


def _completed_future(value):
    future = Future()
    future.set_result(value)
    return future


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


def test_current_trade_date_uses_targeted_flow_when_theme_cache_is_cold(monkeypatch):
    monkeypatch.setattr(
        flow_module,
        "resolve_board_flow_code_for_sector",
        lambda _label: ("BK1325", "半导体材料"),
    )
    monkeypatch.setattr(flow_module, "_matching_theme_board_snapshot", lambda _date: None)
    monkeypatch.setattr(flow_module, "get_effective_trade_date", lambda: "2026-07-21")
    monkeypatch.setattr(flow_module, "_submit_history_load", lambda *_args: None)
    monkeypatch.setattr(
        flow_module,
        "_submit_current_flow_load",
        lambda *_args: _completed_future(
            {
                "date": "2026-07-21",
                "main_force_net_yi": 6.6,
                "flow_tiers": {"super_large_net_yi": 7.69},
            }
        ),
    )

    result = flow_module.build_sector_fund_flow_context(
        "半导体材料",
        sector_return_percent=11.42,
        trade_date="2026-07-21",
    )

    assert result is not None
    assert result["available"] is True
    assert result["date_aligned"] is True
    assert result["today_available"] is True
    assert result["today_main_force_net_yi"] == 6.6
    assert result["pattern_label"] == "price_flow_aligned_up"


def test_historical_trade_date_never_uses_current_targeted_flow(monkeypatch):
    calls = []
    monkeypatch.setattr(
        flow_module,
        "resolve_board_flow_code_for_sector",
        lambda _label: ("BK1325", "半导体材料"),
    )
    monkeypatch.setattr(flow_module, "_matching_theme_board_snapshot", lambda _date: None)
    monkeypatch.setattr(flow_module, "get_effective_trade_date", lambda: "2026-07-21")
    monkeypatch.setattr(flow_module, "_submit_history_load", lambda *_args: None)
    monkeypatch.setattr(
        flow_module,
        "_submit_current_flow_load",
        lambda *_args: calls.append(_args),
    )

    result = flow_module.build_sector_fund_flow_context(
        "半导体材料",
        sector_return_percent=3.0,
        trade_date="2026-07-18",
    )

    assert result is not None
    assert result["available"] is False
    assert calls == []


def test_current_targeted_flow_with_wrong_date_is_rejected(monkeypatch):
    monkeypatch.setattr(
        flow_module,
        "resolve_board_flow_code_for_sector",
        lambda _label: ("BK1325", "半导体材料"),
    )
    monkeypatch.setattr(flow_module, "_matching_theme_board_snapshot", lambda _date: None)
    monkeypatch.setattr(flow_module, "get_effective_trade_date", lambda: "2026-07-21")
    monkeypatch.setattr(flow_module, "_submit_history_load", lambda *_args: None)
    monkeypatch.setattr(
        flow_module,
        "_submit_current_flow_load",
        lambda *_args: _completed_future(
            {"date": "2026-07-20", "main_force_net_yi": 99.0}
        ),
    )

    result = flow_module.build_sector_fund_flow_context(
        "半导体材料",
        sector_return_percent=11.42,
        trade_date="2026-07-21",
    )

    assert result is not None
    assert result["available"] is False
    assert result["today_available"] is False


def test_opportunity_flow_map_reuses_one_captured_theme_snapshot(monkeypatch):
    snapshot = {"trade_date": "2026-07-21", "items": []}
    snapshot_calls = []
    context_calls = []

    monkeypatch.setattr(
        flow_module,
        "get_matching_theme_board_flow_snapshot",
        lambda trade_date: snapshot_calls.append(trade_date) or snapshot,
    )

    def fake_context(label, **kwargs):
        context_calls.append((label, kwargs.get("theme_snapshot")))
        return {"available": True, "sector_label": label}

    monkeypatch.setattr(flow_module, "build_sector_fund_flow_context", fake_context)

    result = scoring_module.build_sector_flow_map_for_opportunities(
        [
            {"sector_label": "半导体材料", "change_1d_percent": 11.42},
            {"sector_label": "人工智能", "change_1d_percent": 3.2},
        ],
        ["半导体材料", "人工智能"],
        trade_date="2026-07-21",
    )

    assert set(result) == {"半导体材料", "人工智能"}
    assert snapshot_calls == ["2026-07-21"]
    assert {label for label, _snapshot in context_calls} == {"半导体材料", "人工智能"}
    assert all(captured is snapshot for _label, captured in context_calls)
