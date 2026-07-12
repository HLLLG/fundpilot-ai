from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services import market_flow_client as service


ANCHOR = "2026-07-10"


def _rows(*, trade_date: str = ANCHOR, north: float = 0, south: float = 12.5) -> list[dict]:
    return [
        {
            "交易日": trade_date,
            "资金方向": "北向",
            "板块": "沪股通",
            "成交净买额": north,
        },
        {
            "交易日": trade_date,
            "资金方向": "南向",
            "板块": "港股通(沪)",
            "成交净买额": south,
        },
    ]


def test_stock_connect_parser_drops_northbound_value_but_keeps_audit_and_southbound():
    result = service._parse_stock_connect_summary(_rows(), ANCHOR)

    assert result is not None
    assert "northbound_net_yi" not in result
    assert "northbound_available" not in result
    assert result["northbound_status"] == "not_disclosed"
    assert result["northbound_reason"] == service._NORTHBOUND_UNAVAILABLE_REASON
    assert result["southbound_net_yi"] == 12.5
    assert result["southbound_available"] is True
    assert "北向资金中性" not in result["interpretation"]
    assert "不参与战术判断" in result["interpretation"]


def test_context_resanitizes_legacy_cache_shape(monkeypatch):
    monkeypatch.setattr(
        service,
        "fetch_stock_connect_flow_summary",
        lambda *_a, **_k: {
            "trade_date": ANCHOR,
            "northbound_net_yi": 88,
            "southbound_net_yi": -8.87,
            "interpretation": "北向净流入约 88 亿，偏利好成长板块。",
        },
    )

    result = service.build_stock_connect_flow_context(ANCHOR)

    assert result["available"] is True
    assert "northbound_net_yi" not in result
    assert result["northbound_status"] == "not_disclosed"
    assert result["southbound_net_yi"] == -8.87
    assert result["southbound_available"] is True
    assert "北向净流入约 88 亿" not in result["interpretation"]
    assert "南向净流出约 9 亿" in result["interpretation"]


def test_fetch_rejects_source_trade_date_mismatch(monkeypatch):
    monkeypatch.setattr(service, "get_spot_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(service, "get_spot_snapshot_any_age", lambda *_a, **_k: None)
    monkeypatch.setattr(
        service,
        "_fetch_stock_connect_flow_summary_uncached",
        lambda *_a, **_k: {
            "trade_date": "2026-07-09",
            "northbound_net_yi": 0,
            "southbound_net_yi": 10,
        },
    )

    assert service.fetch_stock_connect_flow_summary(ANCHOR) is None


def test_parser_does_not_fill_missing_source_date_with_anchor():
    rows = _rows()
    for row in rows:
        row.pop("交易日")

    assert service._parse_stock_connect_summary(rows, ANCHOR) is None


def test_stale_fallback_has_age_boundary(monkeypatch):
    now = datetime(2026, 7, 10, 8, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(service, "_utc_now", lambda: now)
    monkeypatch.setattr(service, "get_spot_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(service, "_fetch_stock_connect_flow_summary_uncached", lambda *_a, **_k: None)
    monkeypatch.setattr(
        service,
        "get_spot_snapshot_any_age",
        lambda *_a, **_k: {
            "trade_date": ANCHOR,
            "northbound_net_yi": 0,
            "southbound_net_yi": 6.2,
            "fetched_at": (now - timedelta(hours=7)).isoformat(),
        },
    )

    assert service.fetch_stock_connect_flow_summary(ANCHOR) is None


def test_recent_stale_fallback_keeps_only_aligned_southbound(monkeypatch):
    now = datetime(2026, 7, 10, 8, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(service, "_utc_now", lambda: now)
    monkeypatch.setattr(service, "get_spot_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(service, "_fetch_stock_connect_flow_summary_uncached", lambda *_a, **_k: None)
    monkeypatch.setattr(
        service,
        "get_spot_snapshot_any_age",
        lambda *_a, **_k: {
            "trade_date": ANCHOR,
            "northbound_net_yi": 0,
            "southbound_net_yi": 6.2,
            "fetched_at": (now - timedelta(hours=2)).isoformat(),
            "interpretation": "北向资金中性，短线参考权重一般。",
        },
    )

    result = service.fetch_stock_connect_flow_summary(ANCHOR)

    assert result is not None
    assert result["stale"] is True
    assert "northbound_net_yi" not in result
    assert result["northbound_status"] == "not_disclosed"
    assert result["southbound_net_yi"] == 6.2
    assert "北向资金中性" not in result["interpretation"]


def test_unavailable_context_exposes_status_and_southbound_reason(monkeypatch):
    monkeypatch.setattr(service, "fetch_stock_connect_flow_summary", lambda *_a, **_k: None)

    result = service.build_stock_connect_flow_context(ANCHOR)

    assert result["available"] is False
    assert "northbound_net_yi" not in result
    assert result["northbound_status"] == "not_disclosed"
    assert result["northbound_reason"] == service._NORTHBOUND_UNAVAILABLE_REASON
    assert result["southbound_available"] is False
    assert result["southbound_reason"] == "source_unavailable_or_trade_date_mismatch"


def test_legacy_market_flow_shim_keeps_old_shape_without_decision_value(monkeypatch):
    monkeypatch.setattr(
        service,
        "fetch_stock_connect_flow_summary",
        lambda *_a, **_k: {
            "trade_date": ANCHOR,
            "southbound_net_yi": 3.5,
            "southbound_available": True,
        },
    )

    result = service.build_market_flow_context(ANCHOR)

    assert result["northbound_net_yi"] is None
    assert result["northbound_available"] is False
    assert result["northbound_status"] == "not_disclosed"
    assert result["southbound_net_yi"] == 3.5
