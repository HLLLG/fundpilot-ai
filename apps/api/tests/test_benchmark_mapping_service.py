from __future__ import annotations

import json
import sqlite3

from app.services.benchmark_mapping_service import freeze_fund_benchmark_spec


def test_tracking_mapping_repairs_a_stale_fuzzy_code_for_the_same_index_name() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE fund_primary_sectors ("
        "userId INTEGER, fund_code TEXT, sector_name TEXT, "
        "intraday_index_name TEXT, source TEXT, confidence REAL, "
        "detail TEXT, updated_at TEXT)"
    )
    connection.execute(
        "CREATE TABLE fund_primary_sectors_global ("
        "fund_code TEXT, sector_name TEXT, intraday_index_name TEXT, "
        "source TEXT, confidence REAL, detail TEXT, resolved_at TEXT)"
    )
    detail = {
        "index_code": "483024",
        "index_name": "中证医药卫生指数",
        "benchmark_text": "中证医药卫生指数收益率×95%＋中国债券总指数收益率×5%",
        "benchmark_text_kind": "performance_benchmark",
        "benchmark_text_source_kind": "xq_akshare_aggregator",
        "benchmark_text_truncated": False,
    }
    connection.execute(
        "INSERT INTO fund_primary_sectors_global VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "000711",
            "医药",
            "中证医药卫生指数",
            "precompute_benchmark",
            0.82,
            json.dumps(detail, ensure_ascii=False),
            "2026-07-15T09:14:54+00:00",
        ),
    )

    spec, _mapping = freeze_fund_benchmark_spec(
        fund_code="000711",
        decision_at="2026-07-15T10:00:00+00:00",
        user_id=1,
        connection=connection,
    )

    assert spec["tier"] == "tracked_index_exact"
    assert spec["benchmark_code"] == "000933"
    assert spec["components"][0]["source_symbol"] == "000933"
