from __future__ import annotations

from app.models import InvestorProfile
from app.services.discovery_client import build_discovery_report_from_parsed
from app.services.discovery_export import discovery_report_to_markdown
from app.services.discovery_payload import OUTPUT_DISCOVERY_REQUIREMENTS


def _profile() -> InvestorProfile:
    return InvestorProfile(
        decision_style="conservative",
        avoid_chasing=True,
        concentration_limit_percent=30,
        expected_investment_amount=100000,
    )


def _candidate_pool() -> list[dict]:
    return [
        {
            "fund_code": "020357",
            "fund_name": "华夏半导体材料设备ETF联接C",
            "sector_label": "半导体材料",
            "fund_quality_score": 132.25,
            "sector_fit_score": 37.12,
            "quality_reasons": ["板块高置信匹配", "近3/6月表现占优"],
            "quality_penalties": ["缺少近1年回撤"],
            "return_3m_percent": 18.2,
            "return_6m_percent": 31.4,
            "return_1y_percent": 42.0,
            "nav_trend": {"distance_from_high_percent": -8.5, "trend_label": "回调企稳"},
        }
    ]


def _facts() -> dict:
    return {
        "portfolio_gap": {"available_budget_yuan": 50000, "holdings_slim": []},
        "sector_opportunities": [
            {
                "sector_label": "半导体材料",
                "track": "momentum",
                "score": 86.5,
                "confidence": "高",
                "entry_hint": "可分批关注",
                "evidence": ["1d/5d 动量延续", "今日主力净流入 3.2 亿"],
                "penalties": ["短线波动较高"],
                "today_main_force_net_yi": 3.2,
                "cumulative_5d_net_yi": 9.8,
                "pattern_label": "price_flow_aligned_up",
            }
        ],
    }


def test_report_parser_preserves_structured_decision_fields():
    parsed = {
        "title": "机会扫描",
        "summary": "半导体材料方向较强。",
        "market_view": "顺势方向占优。",
        "recommendations": [
            {
                "fund_code": "020357",
                "fund_name": "华夏半导体材料设备ETF联接C",
                "sector_name": "半导体材料",
                "action": "建议关注",
                "suggested_amount_yuan": 3000,
                "amount_note": "不超过集中度上限",
                "hold_horizon": "2-4周",
                "confidence": "中",
                "decision_path": "先选半导体材料顺势方向，再选质量分最高的联接基金。",
                "sector_evidence": ["机会分 86.5，track=momentum"],
                "fund_evidence": ["fund_quality_score=132.25，sector_fit_score=37.12"],
                "validation_notes": ["缺少近1年回撤，先观察"],
                "points": ["fund_quality_score=132.25"],
                "risks": ["短线波动较高"],
            }
        ],
        "caveats": [],
    }

    report = build_discovery_report_from_parsed(
        parsed,
        target_sectors=["半导体材料"],
        focus_sectors=[],
        scan_mode="full_market",
        candidate_pool=_candidate_pool(),
        discovery_facts=_facts(),
        profile=_profile(),
        held_codes=set(),
        budget_yuan=50000,
        sector_heat=[{"sector_label": "半导体材料", "change_1d_percent": 2.0}],
        analysis_mode="fast",
    )

    rec = report.recommendations[0]
    assert rec.decision_path == "先选半导体材料顺势方向，再选质量分最高的联接基金。"
    assert rec.sector_evidence == ["机会分 86.5，track=momentum"]
    assert rec.fund_evidence == ["fund_quality_score=132.25，sector_fit_score=37.12"]
    assert rec.validation_notes == ["缺少近1年回撤，先观察"]


def test_guard_backfills_decision_evidence_and_corrects_candidate_identity():
    parsed = {
        "title": "机会扫描",
        "summary": "半导体材料方向较强。",
        "recommendations": [
            {
                "fund_code": "020357",
                "fund_name": "模型写错的名称",
                "sector_name": "模型写错的板块",
                "action": "建议关注",
                "hold_horizon": "2-4周",
                "confidence": "中",
                "points": ["近3/6月表现占优"],
                "risks": ["波动较高"],
            }
        ],
        "caveats": [],
    }

    report = build_discovery_report_from_parsed(
        parsed,
        target_sectors=["半导体材料"],
        focus_sectors=[],
        scan_mode="full_market",
        candidate_pool=_candidate_pool(),
        discovery_facts=_facts(),
        profile=_profile(),
        held_codes=set(),
        budget_yuan=50000,
        sector_heat=[{"sector_label": "半导体材料", "change_1d_percent": 2.0}],
        analysis_mode="fast",
    )

    rec = report.recommendations[0]
    assert rec.fund_name == "华夏半导体材料设备ETF联接C"
    assert rec.sector_name == "半导体材料"
    assert "半导体材料" in rec.decision_path
    assert any("机会分 86.5" in item for item in rec.sector_evidence)
    assert any("fund_quality_score=132.25" in item for item in rec.fund_evidence)
    assert any("缺少近1年回撤" in item for item in rec.validation_notes)
    assert any("已按候选池校正基金名称/板块" in item for item in report.caveats)


def test_guard_backfilled_decision_path_uses_final_action_after_chase_control():
    parsed = {
        "title": "机会扫描",
        "summary": "半导体材料方向很热。",
        "recommendations": [
            {
                "fund_code": "020357",
                "fund_name": "华夏半导体材料设备ETF联接C",
                "sector_name": "半导体材料",
                "action": "分批买入",
                "hold_horizon": "2-4周",
                "confidence": "中",
                "points": ["近3/6月表现占优"],
                "risks": ["波动较高"],
            }
        ],
        "caveats": [],
    }

    report = build_discovery_report_from_parsed(
        parsed,
        target_sectors=["半导体材料"],
        focus_sectors=[],
        scan_mode="full_market",
        candidate_pool=_candidate_pool(),
        discovery_facts=_facts(),
        profile=_profile(),
        held_codes=set(),
        budget_yuan=50000,
        sector_heat=[{"sector_label": "半导体材料", "change_1d_percent": 5.0}],
        analysis_mode="fast",
    )

    rec = report.recommendations[0]
    assert rec.action == "等待回调"
    assert "动作定为等待回调" in rec.decision_path


def test_guard_normalizes_action_confidence_and_downgrades_weak_evidence():
    weak_pool = [
        {
            "fund_code": "021627",
            "fund_name": "华富半导体产业混合发起式C",
            "sector_label": "半导体",
            "fund_quality_score": 48.0,
            "sector_fit_score": 12.0,
            "quality_reasons": ["近3/6月表现占优"],
            "quality_penalties": ["板块匹配置信偏低", "缺少近1年回撤"],
            "return_3m_percent": 115.45,
            "return_6m_percent": 118.34,
            "nav_trend": {"distance_from_high_percent": -12.0, "trend_label": "回调"},
        }
    ]
    facts = {
        "portfolio_gap": {"available_budget_yuan": 50000, "holdings_slim": []},
        "sector_opportunities": [
            {
                "sector_label": "半导体",
                "track": "momentum",
                "score": 52.0,
                "confidence": "低",
                "penalties": ["主方向置信低", "资金流日期不匹配"],
                "today_main_force_net_yi": -12.5,
                "cumulative_5d_net_yi": -80.0,
                "pattern_label": "flow_date_mismatch",
            }
        ],
    }
    parsed = {
        "title": "机会扫描",
        "summary": "半导体方向波动。",
        "recommendations": [
            {
                "fund_code": "021627",
                "fund_name": "华富半导体产业混合发起式C",
                "sector_name": "半导体",
                "action": "少量买入",
                "confidence": "很高",
                "hold_horizon": "2-4周",
                "points": ["近3/6月涨幅较高"],
                "risks": ["波动较高"],
            }
        ],
        "caveats": [],
    }

    report = build_discovery_report_from_parsed(
        parsed,
        target_sectors=["半导体"],
        focus_sectors=[],
        scan_mode="full_market",
        candidate_pool=weak_pool,
        discovery_facts=facts,
        profile=_profile(),
        held_codes=set(),
        budget_yuan=50000,
        sector_heat=[{"sector_label": "半导体", "change_1d_percent": 1.0}],
        analysis_mode="fast",
    )

    rec = report.recommendations[0]
    assert rec.action == "建议关注"
    assert rec.confidence == "中"
    assert any("主方向置信低" in note for note in rec.validation_notes)
    assert any("板块匹配置信偏低" in note for note in rec.validation_notes)
    assert any("已将动作" in point for point in rec.points)
    assert any("证据不足" in line for line in report.caveats)


def test_guard_caps_total_suggested_amount_to_budget():
    pool = [
        {
            "fund_code": "020357",
            "fund_name": "华夏半导体材料设备ETF联接C",
            "sector_label": "半导体材料",
            "fund_quality_score": 132.25,
            "sector_fit_score": 37.12,
        },
        {
            "fund_code": "006081",
            "fund_name": "海富通电子传媒股票A",
            "sector_label": "电子",
            "fund_quality_score": 90.0,
            "sector_fit_score": 35.0,
        },
    ]
    parsed = {
        "title": "机会扫描",
        "summary": "方向较强。",
        "recommendations": [
            {
                "fund_code": "020357",
                "fund_name": "华夏半导体材料设备ETF联接C",
                "sector_name": "半导体材料",
                "action": "建议关注",
                "suggested_amount_yuan": 40000,
                "hold_horizon": "2-4周",
                "points": ["质量分高"],
                "risks": ["波动"],
            },
            {
                "fund_code": "006081",
                "fund_name": "海富通电子传媒股票A",
                "sector_name": "电子",
                "action": "建议关注",
                "suggested_amount_yuan": 40000,
                "hold_horizon": "2-4周",
                "points": ["质量分高"],
                "risks": ["波动"],
            },
        ],
        "caveats": [],
    }
    profile = InvestorProfile(
        decision_style="conservative",
        avoid_chasing=True,
        concentration_limit_percent=100,
        expected_investment_amount=100000,
    )

    report = build_discovery_report_from_parsed(
        parsed,
        target_sectors=["半导体材料", "电子"],
        focus_sectors=[],
        scan_mode="full_market",
        candidate_pool=pool,
        discovery_facts={"portfolio_gap": {"available_budget_yuan": 50000}},
        profile=profile,
        held_codes=set(),
        budget_yuan=50000,
        sector_heat=[],
        analysis_mode="fast",
    )

    amounts = [rec.suggested_amount_yuan or 0 for rec in report.recommendations]
    assert sum(amounts) <= 50000
    assert amounts == [40000, 10000]
    assert any("总预算" in line for line in report.caveats)


def test_guard_syncs_llm_decision_path_after_action_change():
    parsed = {
        "title": "机会扫描",
        "summary": "半导体材料方向很热。",
        "recommendations": [
            {
                "fund_code": "020357",
                "fund_name": "华夏半导体材料设备ETF联接C",
                "sector_name": "半导体材料",
                "action": "分批买入",
                "hold_horizon": "2-4周",
                "confidence": "高",
                "decision_path": "先判断板块方向，再比较基金质量，动作定为分批买入。",
                "points": ["近3/6月表现占优"],
                "risks": ["波动较高"],
            }
        ],
        "caveats": [],
    }

    report = build_discovery_report_from_parsed(
        parsed,
        target_sectors=["半导体材料"],
        focus_sectors=[],
        scan_mode="full_market",
        candidate_pool=_candidate_pool(),
        discovery_facts=_facts(),
        profile=_profile(),
        held_codes=set(),
        budget_yuan=50000,
        sector_heat=[{"sector_label": "半导体材料", "change_1d_percent": 5.0}],
        analysis_mode="fast",
    )

    rec = report.recommendations[0]
    assert rec.action == "等待回调"
    assert "最终动作调整为等待回调" in rec.decision_path
    assert "动作定为分批买入" not in rec.decision_path


def test_guard_removes_free_text_conflicting_action_from_decision_path():
    parsed = {
        "title": "机会扫描",
        "summary": "电子方向较热。",
        "recommendations": [
            {
                "fund_code": "006081",
                "fund_name": "海富通电子传媒股票A",
                "sector_name": "电子",
                "action": "分批买入",
                "hold_horizon": "2-4周",
                "confidence": "高",
                "decision_path": "先判断电子板块方向，再比较基金质量，最后决定建议关注等待回调后分批买入。",
                "points": ["近3/6月表现占优"],
                "risks": ["波动较高"],
            }
        ],
        "caveats": [],
    }
    pool = [
        {
            "fund_code": "006081",
            "fund_name": "海富通电子传媒股票A",
            "sector_label": "电子",
            "fund_quality_score": 90.0,
            "sector_fit_score": 35.0,
        }
    ]

    report = build_discovery_report_from_parsed(
        parsed,
        target_sectors=["电子"],
        focus_sectors=[],
        scan_mode="full_market",
        candidate_pool=pool,
        discovery_facts={"portfolio_gap": {"available_budget_yuan": 50000}},
        profile=_profile(),
        held_codes=set(),
        budget_yuan=50000,
        sector_heat=[{"sector_label": "电子", "change_1d_percent": 5.0}],
        analysis_mode="fast",
    )

    rec = report.recommendations[0]
    assert rec.action == "等待回调"
    assert "最终动作调整为等待回调" in rec.decision_path
    assert "分批买入" not in rec.decision_path


def test_prompt_requires_structured_decision_output():
    assert "decision_path" in OUTPUT_DISCOVERY_REQUIREMENTS
    assert "sector_evidence" in OUTPUT_DISCOVERY_REQUIREMENTS
    assert "fund_evidence" in OUTPUT_DISCOVERY_REQUIREMENTS
    assert "validation_notes" in OUTPUT_DISCOVERY_REQUIREMENTS
    assert "先判断板块方向" in OUTPUT_DISCOVERY_REQUIREMENTS


def test_discovery_markdown_exports_structured_decision_fields():
    markdown = discovery_report_to_markdown(
        {
            "title": "机会扫描",
            "created_at": "2026-06-30",
            "summary": "半导体材料方向较强。",
            "recommendations": [
                {
                    "fund_code": "020357",
                    "fund_name": "华夏半导体材料设备ETF联接C",
                    "sector_name": "半导体材料",
                    "action": "建议关注",
                    "hold_horizon": "2-4周",
                    "confidence": "中",
                    "decision_path": "先判断板块方向，再比较基金质量。",
                    "sector_evidence": ["机会分 86.5"],
                    "fund_evidence": ["fund_quality_score=132.25"],
                    "validation_notes": ["缺少近1年回撤"],
                    "points": ["近3/6月表现占优"],
                    "risks": ["短线波动较高"],
                }
            ],
            "caveats": [],
        }
    )

    assert "**决策路径：** 先判断板块方向，再比较基金质量。" in markdown
    assert "**板块依据：**" in markdown
    assert "- 机会分 86.5" in markdown
    assert "**基金依据：**" in markdown
    assert "- fund_quality_score=132.25" in markdown
    assert "**校验备注：**" in markdown
    assert "- 缺少近1年回撤" in markdown
