"""终选之后才挂同类分位；同一粗分桶只分类一次。"""

from __future__ import annotations

from datetime import datetime, timezone

from app.services.discovery_candidate_pool import (
    attach_descriptive_peer_research,
    build_candidate_pool,
)
from app.services.fund_peer_ranking import build_fund_peer_group

DECISION_AT = datetime(2026, 8, 7, 6, 30, tzinfo=timezone.utc)
_SNAPSHOT_AVAILABLE_AT = "2026-08-07T02:00:00+00:00"
_PIT_STAMPED_METRICS = (
    "return_3m_percent",
    "return_6m_percent",
    "return_1y_percent",
    "max_drawdown_1y_percent",
    "fund_scale_yi",
)


def _catalogue_row(index: int, *, code: str | None = None) -> dict:
    row = {
        "fund_code": code or f"{600000 + index:06d}",
        "fund_name": f"测试股票基金{index}",
        "fund_type": "gp",
        "established_date": "2018-01-15",
        "nav_date": "2026-08-07",
        "latest_nav": 1.5 + index * 0.01,
        "return_3m_percent": 4.0 + index * 0.3,
        "return_6m_percent": 9.0 + index * 0.5,
        "return_1y_percent": 18.0 + index * 0.7,
        "max_drawdown_1y_percent": -12.0 - index * 0.2,
        "fund_scale_yi": 20.0 + index,
        "membership_available_at": _SNAPSHOT_AVAILABLE_AT,
        "snapshot_available_at": _SNAPSHOT_AVAILABLE_AT,
        "source": "fund_universe_snapshot",
    }
    for field in _PIT_STAMPED_METRICS:
        row[f"{field}_available_at"] = _SNAPSHOT_AVAILABLE_AT
        row[f"{field}_source"] = "fund_universe_snapshot"
    return row


def test_build_candidate_pool_does_not_attach_peer_research(monkeypatch) -> None:
    universe = [_catalogue_row(i) for i in range(8)]
    for row in universe[:3]:
        row["fund_name"] = f"半导体成长{row['fund_code']}"
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.list_fund_primary_sectors",
        lambda: [],
    )
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.list_fund_primary_sectors_by_sector_names",
        lambda *_args, **_kwargs: [
            {
                "fund_code": row["fund_code"],
                "fund_name": row["fund_name"],
                "sector_name": "半导体",
                "source": "holdings_infer",
            }
            for row in universe[:3]
        ],
    )

    selected = build_candidate_pool(
        ["半导体"],
        per_sector=2,
        pool_cap=2,
        fetch_rank=lambda limit: universe,
        fetch_new_funds=lambda limit: [],
        decision_at=DECISION_AT,
    )

    assert selected
    assert all("peer_rank" not in row for row in selected)


def test_attach_after_finalize_reuses_bucket_classification(monkeypatch) -> None:
    universe = [_catalogue_row(i) for i in range(40)]
    group_calls = {"n": 0}
    original_group = build_fund_peer_group

    def counting_group(*args, **kwargs):
        group_calls["n"] += 1
        return original_group(*args, **kwargs)

    monkeypatch.setattr(
        "app.services.fund_peer_ranking.build_fund_peer_group",
        counting_group,
    )
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.build_fund_peer_group",
        counting_group,
    )

    candidates = [
        {
            "fund_code": universe[0]["fund_code"],
            "fund_name": universe[0]["fund_name"],
            "fund_type": "gp",
        },
        {
            "fund_code": universe[1]["fund_code"],
            "fund_name": universe[1]["fund_name"],
            "fund_type": "gp",
        },
        {
            "fund_code": universe[2]["fund_code"],
            "fund_name": universe[2]["fund_name"],
            "fund_type": "gp",
        },
    ]
    attached = attach_descriptive_peer_research(
        candidates,
        universe=universe,
        decision_at=DECISION_AT,
    )

    assert all(isinstance(row.get("peer_rank"), dict) for row in attached)
    # 40 只目录分类一次 + 每只终选再算自己的 target group，远小于 3×40。
    assert group_calls["n"] <= 40 + 6
