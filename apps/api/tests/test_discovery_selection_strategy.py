from datetime import date, timedelta

from app.services.discovery_selection_strategy import (
    balanced_score,
    dip_rebound_score,
    pick_sector_candidates,
    rank_candidates_balanced,
    rank_candidates_dip_rebound,
)


def test_balanced_score_prefers_moderate_1y_with_recent_strength():
    hot_chaser = {"return_1y_percent": 120.0, "return_6m_percent": 30.0, "return_3m_percent": 10.0}
    balanced_pick = {"return_1y_percent": 35.0, "return_6m_percent": 28.0, "return_3m_percent": 18.0}
    assert balanced_score(balanced_pick) > balanced_score(hot_chaser)


def test_rank_candidates_balanced_orders_by_score():
    rows = [
        {"fund_code": "000001", "return_1y_percent": 100.0, "return_6m_percent": 20.0, "return_3m_percent": 5.0},
        {"fund_code": "000002", "return_1y_percent": 30.0, "return_6m_percent": 25.0, "return_3m_percent": 15.0},
    ]
    ranked = rank_candidates_balanced(rows)
    assert ranked[0]["fund_code"] == "000002"


def test_dip_rebound_score_prefers_recent_pullback():
    pullback = {
        "return_1y_percent": 25.0,
        "nav_trend": {"recent_5d_change_percent": -6.0, "distance_from_high_percent": -12.0},
    }
    hot = {
        "return_1y_percent": 90.0,
        "nav_trend": {"recent_5d_change_percent": 4.0, "distance_from_high_percent": -1.0},
    }
    assert dip_rebound_score(pullback) > dip_rebound_score(hot)


def test_rank_candidates_dip_rebound_orders_by_score():
    rows = [
        {
            "fund_code": "000001",
            "return_1y_percent": 80.0,
            "nav_trend": {"recent_5d_change_percent": 2.0},
        },
        {
            "fund_code": "000002",
            "return_1y_percent": 30.0,
            "nav_trend": {"recent_5d_change_percent": -5.0, "distance_from_high_percent": -8.0},
        },
    ]
    ranked = rank_candidates_dip_rebound(rows)
    assert ranked[0]["fund_code"] == "000002"


def test_pick_sector_candidates_includes_new_issue_when_requested():
    recent = (date.today() - timedelta(days=30)).isoformat()
    new_rows = [
        {
            "fund_code": "027699",
            "fund_name": "测试半导体精选混合A",
            "established_date": recent,
        }
    ]
    ranked = [
        {
            "fund_code": "519674",
            "fund_name": "银河创新成长",
            "sector_label": "半导体",
            "selection_reason": "排行筛选",
            "return_1y_percent": 40.0,
            "return_6m_percent": 20.0,
            "return_3m_percent": 10.0,
        }
    ]
    seen: set[str] = set()
    picked = pick_sector_candidates(
        sector_label="半导体",
        fixed_entries=[],
        ranked_entries=ranked,
        new_issue_rows=new_rows,
        keywords=("半导体", "芯片"),
        excluded=set(),
        seen_codes=seen,
        fund_type_preference="any",
        selection_strategy="with_new_issue",
        name_matches_sector=lambda name, keywords: any(k in name for k in keywords),
        matches_fund_type=lambda name, pref: True,
    )
    codes = {item["fund_code"] for item in picked}
    assert "027699" in codes
    assert any(item.get("selection_reason") == "新发观察" for item in picked)
