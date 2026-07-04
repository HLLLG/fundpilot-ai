"""M1.4：sector_opportunity_scoring confidence 上限修复单测。

覆盖：无 divergence_backtest 时行为不变（仍封顶「中」）、证据极强时升级为「高」、
证据不够强/不显著/edge 不足时仍是「中」、accumulation 与 distribution 两种模式各自
读取对应 rule_id、build_sector_divergence_map_for_opportunities 的 best-effort 降级。
"""

from __future__ import annotations

from app.services.sector_opportunity_scoring import (
    build_sector_divergence_map_for_opportunities,
    describe_sector_opportunity,
)


def _heat_row(label: str = "半导体", *, change_1d: float = 1.0, change_5d: float = 3.0) -> dict:
    return {
        "sector_label": label,
        "change_1d_percent": change_1d,
        "change_5d_percent": change_5d,
        "heat_score": 80.0,
    }


def _distribution_flow(label: str = "半导体") -> dict:
    """今日转出但5日累计仍净流入：不满足 disqualified 条件（today<=0 且 5d<=0 才会剔除），
    可以走到 _confidence() 分支，用于验证 distribution 规则的置信升级判定。"""
    return {
        "available": True,
        "date_aligned": True,
        "today_main_force_net_yi": -8.0,
        "cumulative_5d_net_yi": 5.0,
        "pattern_label": "distribution",
    }


def _disqualifying_distribution_flow() -> dict:
    """今日与5日累计都为净流出：会被 disqualified，用于验证「不足」优先级高于 divergence。"""
    return {
        "available": True,
        "date_aligned": True,
        "today_main_force_net_yi": -8.0,
        "cumulative_5d_net_yi": -20.0,
        "pattern_label": "distribution",
    }


def _accumulation_flow(label: str = "半导体") -> dict:
    return {
        "available": True,
        "date_aligned": True,
        "today_main_force_net_yi": 8.0,
        "cumulative_5d_net_yi": 20.0,
        "pattern_label": "accumulation",
    }


def _strong_bucket(edge: float = 15.0, significant: bool = True) -> dict:
    return {
        "trigger_count": 40,
        "hit_count": 34,
        "hit_rate_percent": 85.0,
        "baseline_rate_percent": 70.0,
        "edge_percent": edge,
        "significant": significant,
    }


def test_confidence_without_divergence_backtest_stays_capped_at_medium():
    """未传入 divergence_backtest（旧调用方式）时，行为与修复前完全一致：不会是「高」。"""
    result = describe_sector_opportunity(_heat_row(), _accumulation_flow(), focus=set())
    assert result is not None
    assert result["confidence"] == "中"


def test_confidence_upgrades_to_high_when_accumulation_evidence_is_strong():
    divergence = {"by_rule": {"flow_price_accumulation": _strong_bucket(edge=15.0)}}
    result = describe_sector_opportunity(
        _heat_row(change_1d=-1.0, change_5d=-2.0),
        _accumulation_flow(),
        focus=set(),
        divergence_backtest=divergence,
    )
    assert result is not None
    assert result["confidence"] == "高"


def test_confidence_upgrades_to_high_when_distribution_evidence_is_strong():
    """distribution 模式（涨但资金流出）触发时应读取 flow_price_distribution 规则。"""
    divergence = {"by_rule": {"flow_price_distribution": _strong_bucket(edge=12.0)}}
    heat = _heat_row(change_1d=0.5, change_5d=1.0)  # 避免触发「单日涨幅过热」等其它 penalty 干扰
    result = describe_sector_opportunity(
        heat,
        _distribution_flow(),
        focus=set(),
        divergence_backtest=divergence,
    )
    assert result is not None
    assert "资金背离或持续流出" in result["penalties"]
    assert result["confidence"] == "高"


def test_confidence_stays_medium_when_edge_below_high_threshold():
    """edge 未达 10pp 门槛：仍是「中」，不因为有 significant=True 就自动升级。"""
    divergence = {"by_rule": {"flow_price_accumulation": _strong_bucket(edge=6.0)}}
    result = describe_sector_opportunity(
        _heat_row(change_1d=-1.0, change_5d=-2.0),
        _accumulation_flow(),
        focus=set(),
        divergence_backtest=divergence,
    )
    assert result is not None
    assert result["confidence"] == "中"


def test_confidence_stays_medium_when_not_significant():
    """edge 达标但 significant=False（样本不足30次）：仍是「中」，不伪造显著性。"""
    divergence = {
        "by_rule": {
            "flow_price_accumulation": _strong_bucket(edge=20.0, significant=False)
        }
    }
    result = describe_sector_opportunity(
        _heat_row(change_1d=-1.0, change_5d=-2.0),
        _accumulation_flow(),
        focus=set(),
        divergence_backtest=divergence,
    )
    assert result is not None
    assert result["confidence"] == "中"


def test_confidence_reads_wrong_rule_id_does_not_upgrade():
    """当前是 accumulation 模式，但只提供了 distribution 规则的强证据：不应误用。"""
    divergence = {"by_rule": {"flow_price_distribution": _strong_bucket(edge=15.0)}}
    result = describe_sector_opportunity(
        _heat_row(change_1d=-1.0, change_5d=-2.0),
        _accumulation_flow(),
        focus=set(),
        divergence_backtest=divergence,
    )
    assert result is not None
    assert result["confidence"] == "中"


def test_confidence_disqualified_sector_reports_insufficient_regardless_of_divergence():
    """opportunity_available=False（资金持续流出且无回流迹象）时，confidence 走「不足」
    分支，divergence_backtest 证据再强也不应改变这一结论（disqualified 优先级更高）。"""
    divergence = {"by_rule": {"flow_price_distribution": _strong_bucket(edge=20.0)}}
    result = describe_sector_opportunity(
        _heat_row(change_1d=1.0, change_5d=1.0),
        _disqualifying_distribution_flow(),  # today_flow=-8, 5d=-20，两者都 <=0 → disqualified
        focus=set(),
        divergence_backtest=divergence,
    )
    assert result is not None
    assert result["opportunity_available"] is False
    assert result["confidence"] == "不足"


def test_build_sector_divergence_map_skips_boards_with_no_significant_rules(monkeypatch):
    """build_sector_flow_divergence_backtest 返回空 by_rule 时，该板块不进最终 map。"""
    monkeypatch.setattr(
        "app.services.sector_flow_divergence_backtest.build_sector_flow_divergence_backtest",
        lambda _label, **_kwargs: {"by_rule": {}},
    )
    result = build_sector_divergence_map_for_opportunities(["半导体"])
    assert result == {}


def test_build_sector_divergence_map_best_effort_on_worker_exception(monkeypatch):
    from app.services import sector_opportunity_scoring as service

    def _boom(_label, **_kwargs):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(
        "app.services.sector_flow_divergence_backtest.build_sector_flow_divergence_backtest",
        _boom,
    )
    result = service.build_sector_divergence_map_for_opportunities(["半导体", "白酒"])
    assert result == {}


def test_build_sector_divergence_map_includes_successful_boards_only(monkeypatch):
    def _fake(label, **_kwargs):
        if label == "半导体":
            return {"by_rule": {"flow_price_accumulation": _strong_bucket()}}
        return {"by_rule": {}}  # 白酒无显著规则

    monkeypatch.setattr(
        "app.services.sector_flow_divergence_backtest.build_sector_flow_divergence_backtest",
        _fake,
    )
    result = build_sector_divergence_map_for_opportunities(["半导体", "白酒"])
    assert "半导体" in result
    assert "白酒" not in result
