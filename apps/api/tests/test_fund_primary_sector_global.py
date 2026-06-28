"""全市场 fund_primary_sectors_global 与预计算。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.fund_primary_sector_global import is_global_sector_fresh, load_fresh_global_sector
from app.services.fund_primary_sector_precompute import iter_precompute_candidates, precompute_fund_sector
from app.services.fund_primary_sector_types import PrimarySectorRecord
from app.services.fund_primary_sector_service import resolve_primary_sector


@pytest.fixture(autouse=True)
def _clear_benchmark_miss_cache():
    from app.services import fund_primary_sector_service

    fund_primary_sector_service._benchmark_miss_cache.clear()
    yield
    fund_primary_sector_service._benchmark_miss_cache.clear()


def test_is_global_sector_fresh_respects_benchmark_ttl(monkeypatch):
    monkeypatch.setattr(
        "app.services.fund_primary_sector_global.get_settings",
        lambda: type(
            "S",
            (),
            {
                "fund_primary_sector_global_enabled": True,
                "fund_primary_sector_global_benchmark_ttl_days": 30,
                "fund_primary_sector_global_holdings_ttl_days": 90,
            },
        )(),
    )
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert is_global_sector_fresh({"source": "precompute_benchmark", "resolved_at": recent}) is True
    stale = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    assert is_global_sector_fresh({"source": "benchmark_index", "resolved_at": stale}) is False


def test_resolve_primary_sector_uses_global_without_network(monkeypatch):
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_profile_by_code",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_global.get_fund_primary_sector_global",
        lambda _code: {
            "fund_code": "021533",
            "sector_name": "半导体材料",
            "intraday_index_name": "中证半导体材料设备主题指数",
            "source": "precompute_benchmark",
            "confidence": 0.82,
            "detail": {},
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    monkeypatch.setattr(
        "app.services.fund_benchmark_sector.fetch_fund_benchmark_text",
        lambda _code: (_ for _ in ()).throw(AssertionError("network should not run")),
    )

    record = resolve_primary_sector("021533", fetch_benchmark=False)
    assert record is not None
    assert record.sector_name == "半导体材料"
    assert record.source == "precompute_benchmark"


def test_precompute_fund_sector_writes_global(monkeypatch):
    saved: list[dict] = []

    monkeypatch.setattr(
        "app.services.fund_primary_sector_precompute.get_fund_primary_sector_global",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_precompute._resolve_from_benchmark_index",
        lambda _code, **kwargs: PrimarySectorRecord(
            fund_code="021533",
            sector_name="半导体材料",
            intraday_index_name="中证半导体材料设备主题指数",
            source="benchmark_index",
            confidence=0.82,
            detail={},
        ),
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_precompute.promote_record_to_global",
        lambda record: saved.append(
            {
                "fund_code": record.fund_code,
                "sector_name": record.sector_name,
                "source": record.source,
            }
        ),
    )

    status = precompute_fund_sector("021533", mode="benchmark")
    assert status == "ok"
    assert saved == [
        {"fund_code": "021533", "sector_name": "半导体材料", "source": "precompute_benchmark"}
    ]


def test_iter_precompute_candidates_prioritizes_missing(monkeypatch):
    monkeypatch.setattr(
        "app.services.fund_primary_sector_precompute._fund_name_table",
        lambda: [("111111", "A"), ("222222", "B"), ("333333", "C")],
    )

    def _global(code: str):
        if code == "222222":
            return {
                "fund_code": code,
                "source": "precompute_benchmark",
                "resolved_at": datetime.now(timezone.utc).isoformat(),
            }
        return None

    monkeypatch.setattr(
        "app.services.fund_primary_sector_precompute.get_fund_primary_sector_global",
        _global,
    )

    candidates = iter_precompute_candidates(limit=3, force=False)
    assert candidates[0] == "111111"
    assert "333333" in candidates


def test_load_fresh_global_sector_disabled(monkeypatch):
    monkeypatch.setattr(
        "app.services.fund_primary_sector_global.global_sector_enabled",
        lambda: False,
    )
    assert load_fresh_global_sector("021533") is None
