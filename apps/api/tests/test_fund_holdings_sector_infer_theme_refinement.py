"""组合级主题细分规则（CPO / CXO / PCB）的行为契约。

这些规则决定重仓光模块/医药外包/PCB 的基金能否拿到对应主板块身份，
进而决定荐基候选池对这些方向是否永远召回为空。
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.services import fund_holdings_sector_infer as infer_module
from app.services.fund_holdings_sector_infer import (
    HoldingStockRow,
    _refine_current_portfolio_themes,
    assess_sector_from_portfolio_stocks,
)

_NOW = datetime(2026, 8, 17, 3, 0, tzinfo=timezone.utc).isoformat()


def _industry_evidence(industry: str) -> dict:
    return {
        "value": industry,
        "available_at": _NOW,
        "source": "eastmoney_push2_stock_get_f127",
        "ref_id": f"ref-{industry}",
        "pit_qualified": True,
    }


def _board_evidence(codes: list[str]) -> dict:
    return {
        "codes": codes,
        "available_at": _NOW,
        "ref_id": "board-ref",
        "pit_qualified": True,
    }


def test_cpo_rule_refines_optical_module_holdings(monkeypatch):
    """f127「通信设备」持仓中 BK1128 成分占多数时，成分股细分为 CPO。"""

    rows = [
        {"security_code": "300308", "weight_percent": 9.0},  # 中际旭创
        {"security_code": "300502", "weight_percent": 7.0},  # 新易盛
        {"security_code": "000063", "weight_percent": 3.0},  # 中兴通讯，非 CPO 成分
    ]
    broad = {
        code: _industry_evidence("通信设备")
        for code in ("300308", "300502", "000063")
    }
    monkeypatch.setattr(
        infer_module,
        "fetch_current_board_constituent_evidence",
        lambda codes, *, force_refresh=False: {
            "BK1128": _board_evidence(["300308", "300502"])
        },
    )

    enriched = _refine_current_portfolio_themes(rows, broad, force_refresh=False)

    assert enriched["300308"]["theme"] == "CPO"
    assert enriched["300502"]["theme"] == "CPO"
    assert enriched["300308"]["theme_pit_qualified"] is True
    assert "BK1128" in enriched["300308"]["theme_source"]
    # 命中率 16/19 已过 60% 门槛，但改写只作用于成分股本身。
    assert "theme" not in enriched["000063"]


def test_cxo_rule_keeps_hospital_stocks_in_medical(monkeypatch):
    """CXO 龙头细分为 CXO；同为「医疗服务」的医院股不在 BK1600，不被改写。"""

    rows = [
        {"security_code": "603259", "weight_percent": 8.0},  # 药明康德
        {"security_code": "300347", "weight_percent": 6.0},  # 泰格医药
        {"security_code": "300015", "weight_percent": 4.0},  # 爱尔眼科
    ]
    broad = {
        code: _industry_evidence("医疗服务")
        for code in ("603259", "300347", "300015")
    }
    monkeypatch.setattr(
        infer_module,
        "fetch_current_board_constituent_evidence",
        lambda codes, *, force_refresh=False: {
            "BK1600": _board_evidence(["603259", "300347"])
        },
    )

    enriched = _refine_current_portfolio_themes(rows, broad, force_refresh=False)

    assert enriched["603259"]["theme"] == "CXO"
    assert enriched["300347"]["theme"] == "CXO"
    assert "theme" not in enriched["300015"]


def test_cpo_rule_requires_majority_weight(monkeypatch):
    """主设备商权重占优时（成分权重 3/18 < 60%），规则整体不触发。"""

    rows = [
        {"security_code": "000063", "weight_percent": 10.0},  # 中兴通讯
        {"security_code": "600498", "weight_percent": 5.0},  # 烽火通信
        {"security_code": "300308", "weight_percent": 3.0},  # 中际旭创
    ]
    broad = {
        code: _industry_evidence("通信设备")
        for code in ("000063", "600498", "300308")
    }
    monkeypatch.setattr(
        infer_module,
        "fetch_current_board_constituent_evidence",
        lambda codes, *, force_refresh=False: {
            "BK1128": _board_evidence(["300308"])
        },
    )

    enriched = _refine_current_portfolio_themes(rows, broad, force_refresh=False)

    assert all(
        "theme" not in value
        for value in enriched.values()
        if isinstance(value, dict)
    )


def test_compute_rental_rule_refines_it_and_telecom_services(monkeypatch):
    """IT服务/通信服务持仓中 BK1134 成分占多数时细分为算力租赁；半导体股不受影响。"""

    rows = [
        {"security_code": "688316", "weight_percent": 7.0},  # 青云科技
        {"security_code": "300846", "weight_percent": 5.0},  # 首都在线
        {"security_code": "688256", "weight_percent": 9.0},  # 寒武纪（半导体）
    ]
    broad = {
        "688316": _industry_evidence("IT服务Ⅱ"),
        "300846": _industry_evidence("通信服务"),
        "688256": _industry_evidence("半导体"),
    }
    monkeypatch.setattr(
        infer_module,
        "fetch_current_board_constituent_evidence",
        lambda codes, *, force_refresh=False: {
            "BK1134": _board_evidence(["688316", "300846", "688256"])
        },
    )

    enriched = _refine_current_portfolio_themes(rows, broad, force_refresh=False)

    assert enriched["688316"]["theme"] == "算力租赁"
    assert enriched["300846"]["theme"] == "算力租赁"
    # 半导体不是规则 parent：即便寒武纪在算力租赁概念板里，也不改写其行业身份。
    assert "theme" not in enriched["688256"]


def test_refined_cxo_theme_wins_primary_sector_vote():
    """细分主题优先于宽行业映射参与投票，可产出合格的 CXO 主板块。"""

    coverage = {"portfolio_weight_coverage_percent": 60.0}
    stocks = [
        HoldingStockRow(
            name="药明康德",
            weight=30.0,
            industry="医疗服务",
            stock_code="603259",
            coverage=coverage,
            industry_pit_qualified=True,
            theme="CXO",
            theme_pit_qualified=True,
            theme_available_at=_NOW,
        ),
        HoldingStockRow(
            name="泰格医药",
            weight=20.0,
            industry="医疗服务",
            stock_code="300347",
            coverage=coverage,
            industry_pit_qualified=True,
            theme="CXO",
            theme_pit_qualified=True,
            theme_available_at=_NOW,
        ),
        HoldingStockRow(
            name="爱尔眼科",
            weight=10.0,
            industry="医疗服务",
            stock_code="300015",
            coverage=coverage,
            industry_pit_qualified=True,
        ),
    ]

    assessment = assess_sector_from_portfolio_stocks(stocks)

    assert assessment["sector_name"] == "CXO"
    assert assessment["scores"] == {"CXO": 50.0, "医疗": 10.0}
    assert assessment["qualification"]["sector_inference_eligible"] is True


def test_pcb_rule_uses_seed_codes_when_board_omits_leaders(monkeypatch):
    """身份不读 BK0877：沪电/深南在核心名单且合计够重，即可细分为 PCB。"""

    rows = [
        {"security_code": "002463", "weight_percent": 9.0},  # 沪电股份
        {"security_code": "002916", "weight_percent": 7.0},  # 深南电路
        {"security_code": "300408", "weight_percent": 3.0},  # 三环集团，MLCC
    ]
    broad = {
        code: _industry_evidence("元件")
        for code in ("002463", "002916", "300408")
    }

    enriched = _refine_current_portfolio_themes(rows, broad, force_refresh=False)

    assert enriched["002463"]["theme"] == "PCB"
    assert enriched["002916"]["theme"] == "PCB"
    assert enriched["002463"]["theme_source"] == "seed_membership:PCB"
    assert "theme" not in enriched["300408"]


def test_pcb_rule_keeps_mlcc_components_in_electronics(monkeypatch):
    """同为「元件」的 MLCC 不在 PCB 名单/概念板，不被改写。"""

    rows = [
        {"security_code": "300408", "weight_percent": 8.0},  # 三环集团
        {"security_code": "000636", "weight_percent": 6.0},  # 风华高科
        {"security_code": "002463", "weight_percent": 3.0},  # 沪电，未过 60%
    ]
    broad = {
        code: _industry_evidence("元件")
        for code in ("300408", "000636", "002463")
    }
    enriched = _refine_current_portfolio_themes(rows, broad, force_refresh=False)

    assert all(
        "theme" not in value
        for value in enriched.values()
        if isinstance(value, dict)
    )


def test_refined_pcb_theme_wins_primary_sector_vote():
    """细分主题优先于「元件→电子」，可产出合格的 PCB 主板块。"""

    coverage = {"portfolio_weight_coverage_percent": 60.0}
    stocks = [
        HoldingStockRow(
            name="沪电股份",
            weight=28.0,
            industry="元件",
            stock_code="002463",
            coverage=coverage,
            industry_pit_qualified=True,
            theme="PCB",
            theme_pit_qualified=True,
            theme_available_at=_NOW,
        ),
        HoldingStockRow(
            name="深南电路",
            weight=18.0,
            industry="元件",
            stock_code="002916",
            coverage=coverage,
            industry_pit_qualified=True,
            theme="PCB",
            theme_pit_qualified=True,
            theme_available_at=_NOW,
        ),
        HoldingStockRow(
            name="三环集团",
            weight=8.0,
            industry="元件",
            stock_code="300408",
            coverage=coverage,
            industry_pit_qualified=True,
        ),
    ]

    assessment = assess_sector_from_portfolio_stocks(stocks)

    assert assessment["sector_name"] == "PCB"
    assert assessment["scores"] == {"PCB": 46.0, "电子": 8.0}
    assert assessment["qualification"]["sector_inference_eligible"] is True


def test_pcb_rule_requires_a_core_leader(monkeypatch):
    """只有生益/东山、没有沪电深南胜宏鹏鼎时，不升成 PCB。"""

    rows = [
        {"security_code": "002384", "weight_percent": 9.0},  # 东山精密
        {"security_code": "600183", "weight_percent": 8.0},  # 生益科技
    ]
    broad = {
        code: _industry_evidence("元件") for code in ("002384", "600183")
    }

    enriched = _refine_current_portfolio_themes(rows, broad, force_refresh=False)

    assert all(
        "theme" not in value
        for value in enriched.values()
        if isinstance(value, dict)
    )


def test_pcb_rule_counts_ccl_upstream_with_a_leader(monkeypatch):
    """覆铜板本身不够，但配上沪电后一起算进 PCB。"""

    rows = [
        {"security_code": "002463", "weight_percent": 8.0},
        {"security_code": "603186", "weight_percent": 6.0},
        {"security_code": "688519", "weight_percent": 5.0},
        {"security_code": "300408", "weight_percent": 3.0},
    ]
    broad = {
        code: _industry_evidence("元件")
        for code in ("002463", "603186", "688519", "300408")
    }

    enriched = _refine_current_portfolio_themes(rows, broad, force_refresh=False)

    assert enriched["002463"]["theme"] == "PCB"
    assert enriched["603186"]["theme"] == "PCB"
    assert enriched["688519"]["theme"] == "PCB"
    assert "theme" not in enriched["300408"]


def test_pcb_rule_requires_portfolio_weight(monkeypatch):
    """有龙头但合计净值不够 15%，不升成 PCB。"""

    rows = [
        {"security_code": "002463", "weight_percent": 1.36},
        {"security_code": "002916", "weight_percent": 1.32},
        {"security_code": "300408", "weight_percent": 0.63},
    ]
    broad = {
        code: _industry_evidence("元件")
        for code in ("002463", "002916", "300408")
    }

    enriched = _refine_current_portfolio_themes(rows, broad, force_refresh=False)

    assert all(
        "theme" not in value
        for value in enriched.values()
        if isinstance(value, dict)
    )
