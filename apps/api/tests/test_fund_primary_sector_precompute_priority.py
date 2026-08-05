from __future__ import annotations

import json
from contextlib import nullcontext
from types import SimpleNamespace

from app.services import fund_primary_sector_precompute as service


def _configure_queue(monkeypatch, tmp_path) -> None:
    settings = SimpleNamespace(
        db_path=tmp_path / "app.db",
        fund_primary_sector_global_enabled=True,
        fund_primary_sector_precompute_enabled=True,
    )
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service,
        "cross_process_lock",
        lambda *_args, **_kwargs: nullcontext(),
    )


def test_candidate_priority_queue_keeps_only_identity_near_misses(
    monkeypatch,
    tmp_path,
) -> None:
    _configure_queue(monkeypatch, tmp_path)

    queued = service.enqueue_candidate_sector_precompute(
        [
            {
                "fund_code": "1",
                "quality_gate": {"status": "eligible"},
                "vehicle_quality_status": "eligible",
                "sector_fit_score": 16,
            },
            {
                "fund_code": "2",
                "quality_gate": {"status": "watch_only"},
                "vehicle_quality_status": "eligible",
                "sector_fit_score": 16,
            },
            {
                "fund_code": "3",
                "quality_gate": {"status": "eligible"},
                "vehicle_quality_status": "eligible",
                "sector_fit_score": 34,
            },
        ]
    )

    assert queued == 1
    payload = json.loads(service._priority_queue_path().read_text(encoding="utf-8"))
    assert payload["fund_codes"] == ["000001"]


def test_priority_batch_only_fills_missing_rows_and_dequeues_processed_codes(
    monkeypatch,
    tmp_path,
) -> None:
    _configure_queue(monkeypatch, tmp_path)
    service.enqueue_priority_precompute_codes(["1", "2"])
    calls: list[dict] = []

    def run_batch(**kwargs):
        calls.append(kwargs)
        return service.PrecomputeBatchResult(processed=1, ok=1)

    monkeypatch.setattr(service, "run_precompute_batch", run_batch)
    monkeypatch.setattr(
        service,
        "get_fund_sector_current_primary_by_codes",
        lambda _codes: {
            "000001": {
                "source": "precompute_benchmark",
                "identity_status": "verified",
                "is_primary": 1,
                "expires_at": "2099-01-01T00:00:00+00:00",
            }
        },
    )
    monkeypatch.setattr(
        service,
        "list_fund_sector_resolution_statuses",
        lambda: {},
    )

    result = service.run_priority_precompute_batch(limit=2)

    assert result.to_dict() == {
        "processed": 2,
        "ok": 1,
        "skipped": 1,
        "miss": 0,
        "error": 0,
        "queued": 0,
        "research_only": 0,
        "pending": 0,
        "unmapped": 0,
        "unavailable": 0,
        "errors": [],
    }
    assert calls == [
        {
            "limit": 1,
            "mode": "holdings",
            "force": False,
            "fund_codes": ["000002"],
        }
    ]
    payload = json.loads(service._priority_queue_path().read_text(encoding="utf-8"))
    assert payload["fund_codes"] == []


def test_bulk_profile_backlog_detects_due_profiles_but_ignores_holdings_queue(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        service,
        "_fund_name_table",
        lambda: [("000001", "人工智能主题混合A")],
    )
    statuses = {
        "000001": {
            "resolution_status": "queued",
            "next_retry_at": "2000-01-01T00:00:00+00:00",
        }
    }
    monkeypatch.setattr(
        service,
        "list_fund_sector_resolution_statuses",
        lambda: dict(statuses),
    )

    assert service.bulk_profile_resolution_backlog_pending() is False

    statuses["000001"] = {
        "resolution_status": "unavailable",
        "next_retry_at": "2000-01-01T00:00:00+00:00",
    }
    assert service.bulk_profile_resolution_backlog_pending() is True

    statuses["000001"]["next_retry_at"] = "2099-01-01T00:00:00+00:00"
    assert service.bulk_profile_resolution_backlog_pending() is False

    statuses.clear()
    assert service.bulk_profile_resolution_backlog_pending() is True


def test_profile_resolution_verifies_only_passive_exact_benchmark(monkeypatch) -> None:
    profile = {
        "benchmark_text": "中证人工智能主题指数收益率×95%+银行活期存款利率×5%",
        "benchmark_text_kind": "performance_benchmark",
        "benchmark_text_source_kind": "xq_akshare_aggregator",
        "fund_category": "股票型-标准指数",
    }

    record, status, reason, _detail = service._profile_sector_resolution(
        fund_code="000001",
        fallback_name="人工智能主题A类",
        profile=profile,
    )
    assert record is not None
    assert record.sector_name == "人工智能"
    assert status == "verified"
    assert reason == "exact_benchmark_verified"

    active_record, active_status, active_reason, _detail = (
        service._profile_sector_resolution(
            fund_code="000002",
            fallback_name="人工智能主题混合A",
            profile={**profile, "fund_category": "混合型"},
        )
    )
    assert active_record is None
    assert active_status == "queued"
    assert active_reason == "active_fund_holdings_verification_queued"

    bond_record, bond_status, bond_reason, _detail = (
        service._profile_sector_resolution(
            fund_code="000003",
            fallback_name="纯债债券A",
            profile={**profile, "fund_category": "债券型"},
        )
    )
    assert bond_record is None
    assert bond_status == "unmapped"
    assert bond_reason == "non_sector_fund_category"

    qdii_record, qdii_status, qdii_reason, _detail = (
        service._profile_sector_resolution(
            fund_code="000004",
            fallback_name="全球科技精选A",
            profile={**profile, "fund_category": "QDII-股票"},
        )
    )
    assert qdii_record is None
    assert qdii_status == "research_only"
    assert qdii_reason == "overseas_holdings_classifier_unavailable"

    monkeypatch.setattr(service, "_resolve_from_holdings_infer", lambda *_a, **_k: None)
    holdings = service._evaluate_holdings_resolution(
        "000002",
        {
            "status": "qualified",
            "stocks": [object()],
            "sector_clue": {
                "sector_name": "人工智能",
                "scores": {"人工智能": 15.0, "软件": 10.0},
                "coverage": {"classified_mass_percent": 25.0},
                "qualification": {
                    "research_clue_available": True,
                    "sector_inference_eligible": False,
                    "research_only": True,
                    "reason_codes": ["industry_theme_dominance_insufficient"],
                },
            },
        },
    )
    assert holdings.resolution_status == "research_only"
    assert holdings.reason_code == "holdings_evidence_research_only"


def test_bulk_profile_batch_retries_missing_rows_and_checkpoints_every_code(
    monkeypatch,
    tmp_path,
) -> None:
    settings = SimpleNamespace(
        db_path=tmp_path / "app.db",
        fund_primary_sector_precompute_batch_size=80,
        fund_primary_sector_precompute_profile_chunk_size=80,
        fund_primary_sector_global_benchmark_ttl_days=30,
        fund_primary_sector_precompute_unavailable_retry_hours=6,
        fund_primary_sector_precompute_pending_retry_days=14,
        fund_primary_sector_precompute_research_retry_days=30,
        fund_primary_sector_precompute_unmapped_retry_days=30,
    )
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service,
        "_fund_name_table",
        lambda: [
            ("000001", "人工智能ETF联接A"),
            ("000002", "人工智能主题混合A"),
            ("000003", "纯债债券A"),
        ],
    )
    stored: dict[str, dict] = {}
    monkeypatch.setattr(
        service,
        "list_fund_sector_resolution_statuses",
        lambda: dict(stored),
    )

    def save_rows(rows):
        for row in rows:
            stored[str(row["fund_code"])] = dict(row)
        return len(rows)

    monkeypatch.setattr(service, "save_fund_sector_resolution_statuses", save_rows)
    profiles_by_code = {
        "000001": {
            "fund_code": "000001",
            "fund_name": "人工智能ETF联接A",
            "fund_category": "股票型-标准指数",
            "benchmark_text": "中证人工智能主题指数收益率×95%+存款利率×5%",
            "benchmark_text_kind": "performance_benchmark",
            "benchmark_text_source_kind": "xq_akshare_aggregator",
        },
        "000002": {
            "fund_code": "000002",
            "fund_name": "人工智能主题混合A",
            "fund_category": "混合型",
            "benchmark_text": "中证人工智能主题指数收益率×80%+债券指数×20%",
            "benchmark_text_kind": "performance_benchmark",
            "benchmark_text_source_kind": "xq_akshare_aggregator",
        },
        "000003": {
            "fund_code": "000003",
            "fund_name": "纯债债券A",
            "fund_category": "债券型",
            "benchmark_text": None,
            "benchmark_text_kind": None,
            "benchmark_text_source_kind": None,
        },
    }
    profile_calls: list[tuple[list[str], int]] = []

    def fetch_profiles(codes, timeout_seconds):
        normalized = [str(code).zfill(6) for code in codes]
        profile_calls.append((normalized, timeout_seconds))
        selected = normalized if len(profile_calls) > 1 else [
            code for code in normalized if code != "000002"
        ]
        return [profiles_by_code[code] for code in selected]

    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_fund_basic_profiles_xq",
        fetch_profiles,
    )
    promoted: list[str] = []
    monkeypatch.setattr(
        service,
        "_promote_and_remember",
        lambda record, **_kwargs: promoted.append(record.fund_code),
    )
    monkeypatch.setattr(service, "get_fund_primary_sectors_global_by_codes", lambda _c: {})
    monkeypatch.setattr(service, "count_fund_primary_sectors_global", lambda: 0)
    monkeypatch.setattr(service, "count_fresh_verified_fund_sector_current", lambda: 1)
    monkeypatch.setattr(service, "load_precompute_status", lambda: {})
    monkeypatch.setattr(service, "save_precompute_status", lambda _payload: None)

    result = service.run_bulk_profile_precompute_batch(limit=3, sleep_seconds=0)

    assert result.processed == 3
    assert result.ok == 1
    assert result.queued == 1
    assert result.pending == 0
    assert result.unmapped == 1
    assert promoted == ["000001"]
    assert profile_calls == [
        (["000001", "000002", "000003"], 45),
        (["000002"], 20),
    ]
    assert {code: row["resolution_status"] for code, row in stored.items()} == {
        "000001": "verified",
        "000002": "queued",
        "000003": "unmapped",
    }
    assert stored["000002"]["detail"]["profile_retry_recovered"] is True
    assert service._holdings_resolution_candidates(
        limit=3,
        force=False,
        fund_codes=None,
        statuses=stored,
    ) == ["000002"]
    assert service._bulk_resolution_candidates(
        limit=3,
        force=False,
        fund_codes=None,
        statuses=stored,
    ) == []
    assert service.resolution_coverage()["initial_backfill_complete"] is True


def test_bulk_profile_batch_keeps_provider_failures_unavailable_after_retry(
    monkeypatch,
    tmp_path,
) -> None:
    settings = SimpleNamespace(
        db_path=tmp_path / "app.db",
        fund_primary_sector_precompute_batch_size=80,
        fund_primary_sector_precompute_profile_chunk_size=80,
        fund_primary_sector_global_benchmark_ttl_days=30,
        fund_primary_sector_precompute_unavailable_retry_hours=6,
        fund_primary_sector_precompute_pending_retry_days=14,
        fund_primary_sector_precompute_research_retry_days=30,
        fund_primary_sector_precompute_unmapped_retry_days=30,
    )
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service,
        "_fund_name_table",
        lambda: [("000009", "待补档案基金A")],
    )
    stored: dict[str, dict] = {}
    monkeypatch.setattr(
        service,
        "list_fund_sector_resolution_statuses",
        lambda: dict(stored),
    )

    def save_rows(rows):
        for row in rows:
            stored[str(row["fund_code"])] = dict(row)
        return len(rows)

    monkeypatch.setattr(service, "save_fund_sector_resolution_statuses", save_rows)
    profile_calls: list[tuple[list[str], int]] = []

    def fetch_profiles(codes, timeout_seconds):
        profile_calls.append(([str(code).zfill(6) for code in codes], timeout_seconds))
        if len(profile_calls) == 1:
            return None
        raise TimeoutError("provider timeout")

    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_fund_basic_profiles_xq",
        fetch_profiles,
    )
    promoted: list[str] = []
    monkeypatch.setattr(
        service,
        "_promote_and_remember",
        lambda record, **_kwargs: promoted.append(record.fund_code),
    )
    monkeypatch.setattr(service, "get_fund_primary_sectors_global_by_codes", lambda _c: {})
    monkeypatch.setattr(service, "count_fund_primary_sectors_global", lambda: 0)
    monkeypatch.setattr(service, "count_fresh_verified_fund_sector_current", lambda: 0)
    monkeypatch.setattr(service, "load_precompute_status", lambda: {})
    monkeypatch.setattr(service, "save_precompute_status", lambda _payload: None)

    result = service.run_bulk_profile_precompute_batch(limit=1, sleep_seconds=0)

    assert result.processed == 1
    assert result.unavailable == 1
    assert result.ok == 0
    assert promoted == []
    assert profile_calls == [
        (["000009"], 45),
        (["000009"], 20),
    ]
    assert result.errors == ["profile_retry_batch:TimeoutError"]
    assert stored["000009"]["resolution_status"] == "unavailable"
    assert stored["000009"]["reason_code"] == "profile_provider_batch_unavailable"
    assert stored["000009"]["detail"] == {
        "provider_failed": True,
        "profile_retry_attempted": True,
        "profile_retry_provider_failed": True,
    }


def test_stored_profile_reclassification_promotes_new_exact_catalog_match(
    monkeypatch,
) -> None:
    stored = {
        "000001": {
            "fund_code": "000001",
            "fund_name": "银行ETF联接A",
            "stage": "bulk_benchmark_profile",
            "resolution_status": "pending",
            "reason_code": "tracking_index_sector_catalog_pending",
            "attempt_count": 1,
            "detail": json.dumps(
                {
                    "profile_source": "xq.fund_individual_basic_info_xq",
                    "fund_category": "股票型-标准指数",
                    "benchmark_text": "中证银行指数收益率×95%+银行活期存款利率×5%",
                    "benchmark_text_kind": "performance_benchmark",
                    "benchmark_text_source_kind": "xq_akshare_aggregator",
                },
                ensure_ascii=False,
            ),
        }
    }
    monkeypatch.setattr(
        service,
        "list_fund_sector_resolution_statuses",
        lambda: dict(stored),
    )
    saved: list[dict] = []
    monkeypatch.setattr(
        service,
        "save_fund_sector_resolution_statuses",
        lambda rows: saved.extend(rows) or len(rows),
    )
    monkeypatch.setattr(
        service,
        "get_fund_primary_sectors_global_by_codes",
        lambda _codes: {},
    )
    promoted: list[tuple[str, str]] = []
    monkeypatch.setattr(
        service,
        "_promote_and_remember",
        lambda record, **_kwargs: promoted.append(
            (record.fund_code, record.sector_name)
        ),
    )

    result = service.reclassify_stored_profile_resolutions(
        reason_codes={"tracking_index_sector_catalog_pending"}
    )

    assert result.processed == 1
    assert result.ok == 1
    assert promoted == [("000001", "银行")]
    assert saved[0]["resolution_status"] == "verified"
    assert saved[0]["reason_code"] == "exact_benchmark_verified"
    assert saved[0]["attempt_count"] == 2
