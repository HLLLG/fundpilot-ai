"""全市场 fund_primary_sectors_global 与预计算。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config import get_settings
from app.database import save_fund_primary_sector_global
from app.services.fund_primary_sector_global import is_global_sector_fresh, load_fresh_global_sector
from app.services.fund_primary_sector_precompute import (
    iter_precompute_candidates,
    precompute_fund_sector,
    run_precompute_batch,
)
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
        "app.services.fund_primary_sector_precompute.get_fund_primary_sectors_global_by_codes",
        lambda _codes: {},
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


def test_precompute_fund_sector_falls_back_to_llm_when_rules_miss(monkeypatch):
    saved: list[dict] = []

    monkeypatch.setattr(
        "app.services.fund_primary_sector_precompute.get_fund_primary_sectors_global_by_codes",
        lambda _codes: {},
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_precompute._resolve_from_benchmark_index",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_precompute._resolve_from_holdings_infer",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_precompute._lookup_fund_name",
        lambda _code: "某某另类主题混合(QDII)C",
    )
    monkeypatch.setattr(
        "app.services.fund_sector_llm_infer.infer_sector_via_llm",
        lambda _code, _name, **_kwargs: ("另类主题", 0.6),
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_precompute.promote_record_to_global",
        lambda record: saved.append(
            {
                "fund_code": record.fund_code,
                "sector_name": record.sector_name,
                "source": record.source,
                "confidence": record.confidence,
            }
        ),
    )

    status = precompute_fund_sector("654321", mode="auto")
    assert status == "ok"
    assert saved == [
        {
            "fund_code": "654321",
            "sector_name": "另类主题",
            "source": "precompute_llm",
            "confidence": 0.6,
        }
    ]


def test_precompute_fund_sector_skips_llm_when_disabled(monkeypatch):
    monkeypatch.setattr(
        "app.services.fund_primary_sector_precompute.get_fund_primary_sectors_global_by_codes",
        lambda _codes: {},
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_precompute._resolve_from_benchmark_index",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_precompute._resolve_from_holdings_infer",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        get_settings(), "fund_primary_sector_llm_infer_enabled", False
    )
    monkeypatch.setattr(
        "app.services.fund_sector_llm_infer.infer_sector_via_llm",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    status = precompute_fund_sector("654321", mode="auto")
    assert status == "miss"


def test_iter_precompute_candidates_prioritizes_missing(monkeypatch):
    monkeypatch.setattr(
        "app.services.fund_primary_sector_precompute._fund_name_table",
        lambda: [("111111", "A"), ("222222", "B"), ("333333", "C")],
    )

    calls: list[set[str]] = []
    global_rows = {
        "222222": {
            "fund_code": "222222",
            "source": "precompute_benchmark",
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        },
        "333333": {
            "fund_code": "333333",
            "source": "precompute_benchmark",
            "resolved_at": (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(),
        },
    }

    def _global(codes: set[str]):
        calls.append(set(codes))
        return global_rows

    monkeypatch.setattr(
        "app.services.fund_primary_sector_precompute.get_fund_primary_sectors_global_by_codes",
        _global,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_precompute.load_precompute_status",
        lambda: {},
    )

    candidates = iter_precompute_candidates(limit=3, force=False)
    assert candidates == ["111111", "333333", "222222"]
    assert calls == [{"111111", "222222", "333333"}]

    forced = iter_precompute_candidates(
        limit=3,
        force=True,
        global_rows_by_code=global_rows,
    )
    assert forced == ["111111", "222222", "333333"]
    assert calls == [{"111111", "222222", "333333"}]


def test_run_precompute_batch_reuses_promoted_row_for_duplicate_code(monkeypatch):
    batch_calls: list[set[str]] = []
    resolver_calls: list[dict] = []
    promoted: list[PrimarySectorRecord] = []
    saved_status: list[dict] = []
    count_calls = {"value": 0}

    def _batch_get(codes: set[str]):
        batch_calls.append(set(codes))
        return {}

    def _resolve(code: str, **kwargs):
        resolver_calls.append(kwargs)
        return PrimarySectorRecord(
            fund_code=code,
            sector_name="半导体",
            intraday_index_name=None,
            source="benchmark_index",
            confidence=0.82,
        )

    def _promote(record: PrimarySectorRecord):
        promoted.append(record)

    def _count():
        count_calls["value"] += 1
        return 1

    monkeypatch.setattr(
        "app.services.fund_primary_sector_precompute._fund_name_table",
        lambda: [("111111", "A")],
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_precompute.get_fund_primary_sectors_global_by_codes",
        _batch_get,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_precompute.global_sector_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_precompute.is_global_sector_fresh",
        lambda row: bool(row and row.get("resolved_at")),
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_precompute._resolve_from_benchmark_index",
        _resolve,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_precompute.promote_record_to_global",
        _promote,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_precompute.load_precompute_status",
        lambda: {},
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_precompute.save_precompute_status",
        lambda payload: saved_status.append(payload),
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_precompute.count_fund_primary_sectors_global",
        _count,
    )

    result = run_precompute_batch(
        limit=2,
        mode="benchmark",
        fund_codes=["111111", "111111"],
        sleep_seconds=0,
    )

    assert result.to_dict() == {
        "ok": 1,
        "skipped": 1,
        "miss": 0,
        "error": 0,
        "processed": 2,
        "errors": [],
    }
    assert batch_calls == [{"111111"}]
    assert len(resolver_calls) == 1
    assert resolver_calls[0]["preloaded_global_row"] is None
    assert [record.source for record in promoted] == ["precompute_benchmark"]
    assert count_calls == {"value": 1}
    assert saved_status[0]["global_count"] == 1


def test_precompute_stale_row_forces_benchmark_refresh(monkeypatch):
    stale_row = {
        "fund_code": "111111",
        "sector_name": "旧板块",
        "source": "precompute_benchmark",
        "resolved_at": (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(),
    }
    rows = {"111111": stale_row}
    resolver_rows: list[dict | None] = []

    def _resolve(code: str, **kwargs):
        resolver_rows.append(kwargs["preloaded_global_row"])
        return PrimarySectorRecord(
            fund_code=code,
            sector_name="新板块",
            intraday_index_name=None,
            source="benchmark_index",
        )

    monkeypatch.setattr(
        "app.services.fund_primary_sector_precompute.global_sector_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_precompute.is_global_sector_fresh",
        lambda row: row is not stale_row,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_precompute._resolve_from_benchmark_index",
        _resolve,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_precompute.promote_record_to_global",
        lambda _record: None,
    )

    assert (
        precompute_fund_sector(
            "111111",
            mode="benchmark",
            global_rows_by_code=rows,
        )
        == "ok"
    )
    assert resolver_rows == [None]
    assert rows["111111"]["sector_name"] == "新板块"
    assert rows["111111"]["source"] == "precompute_benchmark"


def test_benchmark_resolver_skips_point_lookup_for_preloaded_missing_row(monkeypatch):
    from app.services.fund_primary_sector_service import _resolve_from_benchmark_index

    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.load_fresh_global_sector",
        lambda _code: (_ for _ in ()).throw(AssertionError("point lookup must not run")),
    )

    assert (
        _resolve_from_benchmark_index(
            "111111",
            fetch=False,
            persist_user=False,
            promote_global=False,
            preloaded_global_row=None,
        )
        is None
    )


def test_save_global_sector_returns_written_row_without_point_read(monkeypatch):
    monkeypatch.setattr(
        "app.database.get_fund_primary_sector_global",
        lambda _code: (_ for _ in ()).throw(AssertionError("write must not read back")),
    )

    saved = save_fund_primary_sector_global(
        fund_code="111111",
        sector_name="半导体",
        source="precompute_benchmark",
        confidence=0.82,
    )

    assert saved["fund_code"] == "111111"
    assert saved["sector_name"] == "半导体"
    assert saved["source"] == "precompute_benchmark"
    assert saved["resolved_at"] == saved["updated_at"]


def test_load_fresh_global_sector_disabled(monkeypatch):
    monkeypatch.setattr(
        "app.services.fund_primary_sector_global.global_sector_enabled",
        lambda: False,
    )
    assert load_fresh_global_sector("021533") is None
