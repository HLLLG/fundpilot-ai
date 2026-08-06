from __future__ import annotations

from datetime import datetime, timezone

from app.services.fund_peer_ranking import build_fund_peer_group
from app.services.fund_type_classification import has_positive_qdii_marker


def test_non_qdii_label_is_not_treated_as_overseas() -> None:
    assert has_positive_qdii_marker("商品型-非QDII") is False
    assert has_positive_qdii_marker("商品型-NON-QDII") is False

    group = build_fund_peer_group(
        {
            "fund_code": "000930",
            "fund_name": "博时黄金I",
            "fund_type": "商品型-非QDII",
            "fund_category": "商品型-非QDII",
        },
        decision_at=datetime(2026, 8, 6, tzinfo=timezone.utc),
    )

    assert group["region"] == "domestic"
    assert group["fund_type_key"] != "qdii"
    assert group["group_key"].startswith("domestic.commodity")


def test_positive_qdii_marker_remains_overseas() -> None:
    assert has_positive_qdii_marker("QDII-股票型") is True

    group = build_fund_peer_group(
        {
            "fund_code": "000001",
            "fund_name": "全球科技股票(QDII)A",
            "fund_type": "QDII-股票型",
        },
        decision_at=datetime(2026, 8, 6, tzinfo=timezone.utc),
    )

    assert group["region"] == "overseas"
    assert group["fund_type_key"] == "qdii"
