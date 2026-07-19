from __future__ import annotations

import logging

from app.services import akshare_subprocess
from app.services.fund_universe_sampler import (
    stratified_sample_universe,
    universe_coverage,
)


def _row(index: int, fund_type: str, *, rank_enriched: bool = False) -> dict:
    return {
        "fund_code": f"{index:06d}",
        "fund_name": f"fund-{fund_type}-{index}",
        "fund_type": fund_type,
        "rank_enriched": rank_enriched,
        "return_1y_percent": float(index) if rank_enriched else None,
    }


def test_full_catalogue_is_the_success_boundary_when_rank_enrichment_fails(
    monkeypatch,
    caplog,
) -> None:
    captured: dict[str, object] = {}
    expected = [_row(1, "gp")]

    def fake_run(script: str, *, label: str, timeout: int | float, **_kwargs):
        captured.update({"script": script, "label": label, "timeout": timeout})
        return {
            "data": expected,
            "metadata": {
                "rank_enriched_rows": 0,
                "rank_error": "ReadTimeout: optional source timed out",
            },
        }

    monkeypatch.setattr(akshare_subprocess, "run_akshare_json_script", fake_run)
    with caplog.at_level(logging.WARNING):
        result = akshare_subprocess.fetch_open_fund_universe(
            limit=25_000,
            timeout_seconds=42,
        )

    assert result == expected
    assert captured["label"] == "fund_open_universe:25000"
    assert captured["timeout"] == 42
    assert "fundcode_search.js" in str(captured["script"])
    assert '"ft": "all"' in str(captured["script"])
    assert '"pn": "25000"' in str(captured["script"])
    assert "without optional rank enrichment" in caplog.text


def test_catalogue_failure_returns_no_universe(monkeypatch) -> None:
    monkeypatch.setattr(
        akshare_subprocess,
        "run_akshare_json_script",
        lambda *_args, **_kwargs: None,
    )

    assert akshare_subprocess.fetch_open_fund_universe(limit=25_000) is None


def test_hash_fallback_is_deterministic_and_input_order_independent() -> None:
    fund_types = ("gp", "hh", "zq", "zs", "qdii", "fof")
    rows = [
        _row(type_index * 1_000 + index, fund_type)
        for type_index, fund_type in enumerate(fund_types, start=1)
        for index in range(60)
    ]

    first = stratified_sample_universe(rows, 60)
    repeated = stratified_sample_universe(list(reversed(rows)), 60)

    assert [row["fund_code"] for row in first] == [
        row["fund_code"] for row in repeated
    ]
    sampled_by_type = {
        fund_type: sum(row["fund_type"] == fund_type for row in first)
        for fund_type in fund_types
    }
    assert sampled_by_type == {fund_type: 10 for fund_type in fund_types}
    assert [row["fund_code"] for row in first[:10]] != [
        row["fund_code"] for row in rows[:10]
    ]


def test_complete_rank_snapshot_prefers_current_rows_for_sampling() -> None:
    fund_types = ("gp", "hh", "zq", "zs", "qdii", "fof")
    rows: list[dict] = []
    for type_index, fund_type in enumerate(fund_types, start=1):
        rows.extend(
            _row(type_index * 1_000 + index, fund_type, rank_enriched=True)
            for index in range(40)
        )
        rows.extend(
            _row(type_index * 1_000 + 100 + index, fund_type)
            for index in range(20)
        )

    sampled = stratified_sample_universe(rows, 60)
    coverage = universe_coverage(rows, sampled)

    assert len(sampled) == 60
    assert all(row["rank_enriched"] is True for row in sampled)
    assert coverage["rank_enriched_share_classes"] == 240
    assert coverage["rank_enrichment_rate"] == 0.6667
