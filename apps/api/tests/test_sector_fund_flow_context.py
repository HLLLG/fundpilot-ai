from app.services.sector_fund_flow_context import (
    _classify_flow_pattern,
    build_sector_fund_flow_context,
    build_sector_fund_flow_map,
)


def test_classify_distribution_pattern():
    pattern = _classify_flow_pattern(
        sector_return_percent=2.5,
        today_flow=-10.0,
        cumulative_5d=20.0,
        flow_tiers={
            "super_large_net_yi": -8.0,
            "small_net_yi": 5.0,
        },
    )
    assert pattern["pattern_label"] == "distribution"
    assert "出货" in pattern["pattern_hint"]


def test_classify_accumulation_pattern():
    pattern = _classify_flow_pattern(
        sector_return_percent=-2.0,
        today_flow=5.0,
        cumulative_5d=-3.0,
        flow_tiers=None,
    )
    assert pattern["pattern_label"] == "accumulation"


def test_build_sector_fund_flow_context(monkeypatch):
    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.resolve_board_flow_code_for_sector",
        lambda _label: ("BK1036", "半导体"),
    )
    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.get_cached_board_flow_series",
        lambda _code: [
            {"date": f"2026-05-{day:02d}", "main_force_net_yi": 1.0, "flow_tiers": {}}
            for day in range(20, 32)
        ]
        + [
            {"date": "2026-06-18", "main_force_net_yi": 1.0, "flow_tiers": {}},
            {"date": "2026-06-19", "main_force_net_yi": -2.0, "flow_tiers": {}},
            {"date": "2026-06-20", "main_force_net_yi": 3.0, "flow_tiers": {}},
            {"date": "2026-06-21", "main_force_net_yi": -1.0, "flow_tiers": {}},
            {
                "date": "2026-06-22",
                "main_force_net_yi": -5.0,
                "flow_tiers": {"super_large_net_yi": -4.0, "small_net_yi": 2.0},
            },
        ],
    )

    context = build_sector_fund_flow_context("半导体", sector_return_percent=1.5)
    assert context is not None
    assert context["available"] is True
    assert context["board_code"] == "BK1036"
    assert context["today_main_force_net_yi"] == -5.0
    assert context["cumulative_5d_net_yi"] == -4.0
    assert context["cumulative_20d_net_yi"] == 8.0
    assert context["recent_5d_main_force_yi"] == [1.0, -2.0, 3.0, -1.0, -5.0]
    assert context["pattern_label"] == "distribution"


def test_build_sector_fund_flow_map_dedupes(monkeypatch):
    from app.models import Holding

    holdings = [
        Holding(
            fund_code="519674",
            fund_name="基A",
            holding_amount=1000,
            sector_name="半导体",
            sector_return_percent=1.0,
        ),
        Holding(
            fund_code="015945",
            fund_name="基B",
            holding_amount=2000,
            sector_name="半导体",
            sector_return_percent=1.0,
        ),
    ]

    calls = {"count": 0}

    def _fake_build(sector_name, *, sector_return_percent=None):
        calls["count"] += 1
        return {"available": True, "sector_label": sector_name}

    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.build_sector_fund_flow_context",
        _fake_build,
    )
    result = build_sector_fund_flow_map(holdings)
    assert calls["count"] == 1
    assert "半导体" in result
