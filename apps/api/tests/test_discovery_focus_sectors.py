"""用户手选的关注方向必须真正进入扫描范围。

回归背景：`sector_opportunities` 曾经**整体覆盖** `select_target_sectors` 的结果，
而 `full_market` 是默认模式，所以用户选的板块只要当日没通过方向成熟度门槛（判为
invalid、或被相关性去重/名额挤掉），就会在候选召回之前彻底消失——报告里既没有这个
方向，也没有任何说明，用户看起来就是"选了板块但完全没生效"。
"""

from app.models import DiscoveryRequest, InvestorProfile
from app.services.discovery_pipeline import resolve_scan_scope
from app.services.discovery_target_sectors import (
    resolve_focus_sector_labels,
    select_target_sectors,
)


def _request(**kwargs) -> DiscoveryRequest:
    return DiscoveryRequest(holdings=[], profile=InvestorProfile(), **kwargs)


def _opportunity(label: str) -> dict:
    return {"sector_label": label}


def test_focus_sector_survives_the_direction_scoring_override() -> None:
    request = _request(focus_sectors=["半导体", "医药"])
    # 方向打分只留下两个热门方向，用户选的两个都没通过当日门槛。
    target_sectors, per_sector, pool_cap = resolve_scan_scope(
        request,
        select_target_sectors([], ["半导体", "医药"], [], request.profile),
        [_opportunity("银行"), _opportunity("煤炭")],
    )

    # 关注方向排在最前，保证 finalize_candidate_pool 的板块配额先分给它们。
    assert target_sectors == ["半导体", "医药", "银行", "煤炭"]
    assert per_sector == 3
    # 候选池按关注方向数量扩容，新增方向不会把排序靠后的自动方向挤出板块配额。
    assert pool_cap == 28 + 3 * 2


def test_focus_sector_is_not_duplicated_when_it_also_scores_through() -> None:
    request = _request(focus_sectors=["半导体"])
    target_sectors, _per_sector, pool_cap = resolve_scan_scope(
        request,
        ["半导体", "银行"],
        [_opportunity("银行"), _opportunity("半导体")],
    )

    assert target_sectors == ["半导体", "银行"]
    assert pool_cap == 28 + 3


def test_scan_scope_without_focus_sectors_keeps_the_scored_direction_order() -> None:
    request = _request()
    target_sectors, _per_sector, pool_cap = resolve_scan_scope(
        request,
        ["旧的热度顺序"],
        [_opportunity("银行"), _opportunity("煤炭")],
    )

    assert target_sectors == ["银行", "煤炭"]
    assert pool_cap == 28


def test_portfolio_gap_scan_keeps_its_own_target_sectors() -> None:
    """历史 portfolio_gap 报告的方向列表不受方向打分覆盖，行为保持不变。"""
    request = _request(focus_sectors=["半导体"], scan_mode="portfolio_gap")
    target_sectors, _per_sector, _pool_cap = resolve_scan_scope(
        request,
        ["半导体", "缺口板块"],
        [_opportunity("银行")],
    )

    assert target_sectors == ["半导体", "缺口板块"]


def test_focus_sector_labels_are_normalised_to_whitelist_labels() -> None:
    """关注方向要用归一后的标签比对，否则会在某一步静默失配。"""
    assert resolve_focus_sector_labels(["半导体", " 半导体 ", ""]) == ["半导体"]
    assert resolve_focus_sector_labels(None) == []


def test_full_market_targets_mix_flow_and_setup_not_just_heat() -> None:
    """全市场第一层不能只按 1/5 日热度圈满 6 个目标。"""
    hot = [
        {
            "sector_label": f"热门{index}",
            "heat_score": 20.0 - index,
            "change_1d_percent": 8.0,
            "change_5d_percent": 15.0,
        }
        for index in range(12)
    ]
    quiet = {
        "sector_label": "安静蓄势",
        "heat_score": 0.4,
        "change_1d_percent": 0.2,
        "change_5d_percent": 0.8,
        "advancing_ratio_percent": 55.0,
    }

    selected = select_target_sectors(
        [],
        [],
        [*hot, quiet],
        InvestorProfile(),
        flow_inflection_labels=["资金拐点"],
    )

    assert "资金拐点" in selected
    assert "安静蓄势" in selected
    assert len(selected) == 6


def test_full_market_focus_occupies_shared_six_slots() -> None:
    """关注方向占 6 席总名额，不再在自动 8 席之外另加。"""
    heat = [
        {
            "sector_label": f"热门{index}",
            "heat_score": 20.0 - index,
            "change_1d_percent": 8.0,
            "change_5d_percent": 15.0,
        }
        for index in range(12)
    ]

    selected = select_target_sectors(
        [],
        ["半导体", "医药", "银行"],
        heat,
        InvestorProfile(),
    )

    assert selected[:3] == ["半导体", "医药", "银行"]
    assert len(selected) == 6


def test_actionable_gold_adds_gold_equity_companion_for_vehicle_recall() -> None:
    request = _request()
    target_sectors, _per_sector, _pool_cap = resolve_scan_scope(
        request,
        ["黄金"],
        [
            {
                "sector_label": "黄金",
                "entry_state": "ready_to_start",
                "opportunity_available": True,
            }
        ],
    )

    assert target_sectors == ["黄金", "黄金股"]


def test_gap_quota_counts_normalised_focus_labels_as_focus() -> None:
    """缺口配额不得把「归一后改了名的关注方向」误计为缺口板块。

    回归背景：`_select_portfolio_gap_sectors` 曾用**用户原始输入**（"半导体主题"）比对
    已归一的 `ordered`（"半导体"），于是关注方向被误计入 3 个缺口名额，热度表里真正的
    缺口板块被提前截断。
    """
    heat = [{"sector_label": label} for label in ("银行", "煤炭", "有色金属", "医疗")]
    # "半导体主题" 会被 registry 归一为 "半导体"，与原始输入字符串不相等。
    target = select_target_sectors(
        [],
        ["半导体主题"],
        heat,
        InvestorProfile(),
        scan_mode="portfolio_gap",
    )

    # 关注方向占首位，其后必须补满 3 个真正的缺口板块（修复前只会补 2 个）。
    assert target == ["半导体", "银行", "煤炭", "有色金属"]
