"""日报接入基金持仓穿透后的链路契约。

回归背景：`build_fund_lookthrough_context` 长期是一份"造好但没插电"的资产——整个
`app/` 下零生产调用方，而下游插座早就建好了：`decision_data_evidence` 已经在读
`facts["fund_lookthrough"]` 生成时点证据（此前是死代码）、`analysis_payload` 已经在
trim 阶段 pop 这两个 key、`decision_quality_artifacts` 已经在等 claim audit。

穿透是日报唯一能看到"跨基金重复暴露"的地方：按基金市值算的集中度看不出三只名字与
板块标签都不同的基金其实重仓同一批股票。这里锁住四条契约：
1. facts 里带完整载荷，并真正激活 `fund_lookthrough:portfolio` 证据项；
2. 喂给 LLM 的是有界摘要，绝不含逐只披露快照解析审计与原始持仓行；
3. 超时/异常时键仍存在且 fail-closed 为 `status=unavailable`；
4. 模型对穿透的越界叙述会被 claim validator 清洗，并留下确定性审计。

文件范围随后扩到 `prepare_analysis_bundle` 的另外两处行内挂载（它们共用同一套并发/桩
夹具，分开建文件只会复制夹具）：行内基准投影，以及读该投影的载体质量——后者对挂载
**顺序**有硬要求，见 `test_vehicle_quality_is_attached_after_the_benchmark_join`。
"""

from datetime import datetime, timezone

import pytest

from app.models import (
    AnalysisRequest,
    FundSnapshot,
    Holding,
    InvestorProfile,
    RiskAssessment,
)
from app.services import analysis_facts as analysis_facts_module
from app.services import analysis_payload as analysis_payload_module
from app.services.analysis_payload import (
    prepare_analysis_bundle,
    trim_analysis_facts_for_llm,
)

DECISION_AT = datetime(2026, 8, 7, 6, 30, tzinfo=timezone.utc)
_SNAPSHOT_REF = "a1b2c3d4e5f6"


@pytest.fixture(autouse=True)
def _stub_analysis_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """把与穿透无关的并发增强项全部静态化，让用例只反映穿透链路本身。"""
    monkeypatch.setattr(
        analysis_payload_module,
        "_compute_analysis_context",
        lambda *_args, **_kwargs: ({"effective_trade_date": "2026-08-07"}, None, None, None),
    )
    for name, value in (
        ("build_signal_backtest_context", {"enabled": False, "has_data": False, "sectors": []}),
        ("resolve_signal_guard_policy", {"enforce_reversal_block": True, "enforce_pullback_block": True}),
        ("_build_sector_intraday_map", {}),
        ("build_stock_connect_flow_context", {"available": False}),
        ("build_market_breadth_signal", {"available": False}),
    ):
        monkeypatch.setattr(
            analysis_facts_module,
            name,
            lambda *_args, _value=value, **_kwargs: _value,
        )
    monkeypatch.setattr(
        analysis_facts_module,
        "build_holding_sector_opportunity_context",
        lambda *_args, **_kwargs: {
            "available": False,
            "held": {},
            "market_top": [],
            "sector_flow_by_label": {},
            "divergence_backtest": {},
        },
    )


def _holdings() -> list[Holding]:
    return [
        Holding(
            fund_code="519674",
            fund_name="银河创新成长",
            sector_name="半导体",
            holding_amount=10_000.0,
            holding_return_percent=6.0,
        ),
        Holding(
            fund_code="161725",
            fund_name="招商中证白酒",
            sector_name="白酒",
            holding_amount=8_000.0,
            holding_return_percent=-4.0,
        ),
    ]


def _request(holdings: list[Holding] | None = None) -> AnalysisRequest:
    return AnalysisRequest(
        holdings=holdings if holdings is not None else _holdings(),
        profile=InvestorProfile(
            decision_style="conservative",
            max_drawdown_percent=15,
            concentration_limit_percent=40,
            expected_investment_amount=100_000,
        ),
    )


def _existing_fund_row(code: str) -> dict:
    return {
        "fund_code": code,
        "status": "qualified",
        "snapshot": {
            "fund_code": code,
            "as_of_date": "2026-06-30",
            # 时点可用性取「披露发布」与「我们首次观察到」的较晚者，两者缺一
            # 就判为不可用——防止用披露日回溯冒充当时就能看到。
            "available_at": "2026-07-20T08:00:00+00:00",
            "first_observed_at": "2026-07-21T02:00:00+00:00",
            "current_freshness_label": "aging",
            "disclosed_overlap_lower_bound_eligible": True,
        },
        "lookthrough": {"identity_known_disclosed_mass_percent": 62.5},
    }


def _lookthrough_payload() -> dict:
    """一份形状真实的 portfolio_only 穿透结果：两只基金重仓同一只证券。"""
    return {
        "schema_version": "fund_lookthrough_research.v1",
        "status": "qualified",
        "scope": "portfolio_only",
        "research_qualified": True,
        "execution_qualified": False,
        "reason_codes": [],
        "research_hash": "f" * 64,
        "capabilities": {"portfolio_lookthrough": {"status": "qualified"}},
        "decision_use": {
            "research_eligible": True,
            "concentration_risk_guard_eligible": True,
        },
        "portfolio": {
            "scope": "fund_holdings_only",
            "portfolio_positions_complete": False,
            "identity_known_security_mass_lower_bound_percent": 58.0,
            "disclosed_security_mass_lower_bound_percent": 61.0,
            "unknown_account_mass_percent": 39.0,
            "security_exposure_lower_bounds": [
                {
                    "security_key": "600519",
                    "security_name": "贵州茅台",
                    "exposure_lower_bound_percent": 7.4,
                    "contributing_fund_count": 2,
                },
                {
                    "security_key": "000858",
                    "security_name": "五粮液",
                    "exposure_lower_bound_percent": 4.1,
                    "contributing_fund_count": 1,
                },
            ],
            "industry_exposure_lower_bounds": [
                {"industry": "食品饮料", "exposure_lower_bound_percent": 18.6},
            ],
            "listing_market_exposure_lower_bounds": [
                {"listing_market": "A股", "exposure_lower_bound_percent": 61.0},
            ],
        },
        "existing_funds": [_existing_fund_row("519674"), _existing_fund_row("161725")],
        "candidates": [],
        "resolution_audit": {
            "schema_version": "fund_holdings_resolution_audit.v1",
            "rows": [
                {
                    "fund_code": "519674",
                    "snapshot_ref": _SNAPSHOT_REF,
                    "qualified": True,
                    "as_of_date": "2026-06-30",
                    "available_at": "2026-07-20T08:00:00+00:00",
                    "freshness": "aging",
                },
            ],
        },
        "raw_holdings_included": False,
        "raw_snapshots_included": False,
    }


def _bundle(
    lookthrough_resolver,
    *,
    benchmark_research: dict | None = None,
    holdings: list[Holding] | None = None,
    snapshots: list[FundSnapshot] | None = None,
    peer_research: dict | None = None,
) -> object:
    request = _request(holdings)
    snapshots = snapshots if snapshots is not None else [
        FundSnapshot(fund_code=h.fund_code, fund_name=h.fund_name, source="test")
        for h in request.holdings
    ]
    return prepare_analysis_bundle(
        request,
        RiskAssessment(
            level="medium",
            suggested_action="watch",
            weighted_return_percent=1.0,
            alerts=[],
        ),
        snapshots,
        [],
        budget_enhancements=True,
        decision_at=DECISION_AT,
        tradeability_resolver=lambda *_a, **_k: {},
        benchmark_resolver=lambda *_a, **_k: {},
        benchmark_research_resolver=lambda *_a, **_k: benchmark_research or {},
        lookthrough_resolver=lookthrough_resolver,
        peer_research_resolver=lambda *_a, **_k: peer_research or {},
    )


def test_lookthrough_activates_its_evidence_item_before_being_compacted() -> None:
    captured: dict = {}

    def resolver(holdings, candidate_pool, **kwargs):
        captured["holding_count"] = len(holdings)
        captured["candidate_pool"] = candidate_pool
        captured["analysis_mode"] = kwargs.get("analysis_mode")
        return _lookthrough_payload()

    facts = _bundle(resolver).facts

    # 日报只穿透已持仓，没有候选基金 → scope 必须是 portfolio_only。
    assert captured["holding_count"] == 2
    assert captured["candidate_pool"] is None

    evidence_items = {
        str(item.get("fact_id")): item
        for item in facts["data_evidence"]["items"]
        if isinstance(item, dict)
    }
    portfolio_item = evidence_items["fund_lookthrough:portfolio"]
    assert portfolio_item["source"] == "fund_lookthrough_research"
    assert portfolio_item["confidence"] == "medium"
    assert portfolio_item["freshness"] == "aging"
    assert portfolio_item["as_of_date"] == "2026-06-30"
    # 逐只披露快照的时点证据来自完整载荷里的 resolution_audit.rows，必须在收敛之前取到。
    assert f"holdings_snapshot:519674:{_SNAPSHOT_REF}" in evidence_items


def test_persisted_lookthrough_is_the_bounded_summary_shared_with_prompt() -> None:
    """落库给前端的是有界摘要；prompt 再压一层，但合格时 top_* 暴露下界必须仍在。

    曾经落库完整载荷、只在 trim 阶段投影，结果两份形状字段名不同
    （`security_exposure_lower_bounds` vs `top_security_exposure_lower_bounds`），
    前端按落库形状取值就会读到空。落库形状因此保持有界摘要；喂模型时再丢掉
    disclaimer / candidates / 资格审计，只留可引用的暴露下界。
    """
    facts = _bundle(lambda *_a, **_k: _lookthrough_payload()).facts
    persisted = facts["fund_lookthrough"]

    assert persisted["status"] == "qualified"
    assert persisted["scope"] == "portfolio_only"
    # 重复暴露必须留下来——这正是日报接穿透的目的。
    top_securities = persisted["portfolio"]["top_security_exposure_lower_bounds"]
    assert top_securities[0]["security_key"] == "600519"
    assert top_securities[0]["security_name"] == "贵州茅台"
    assert top_securities[0]["exposure_lower_bound_percent"] == 7.4
    assert persisted["portfolio"]["unknown_account_mass_percent"] == 39.0
    assert persisted["execution_qualified"] is False
    assert persisted["raw_holdings_included"] is False
    # 逐只披露快照解析审计与原始快照不随每份日报持久化，也不进 prompt。
    assert "resolution_audit" not in persisted
    assert "existing_funds" not in persisted

    trimmed = trim_analysis_facts_for_llm(facts)
    lookthrough = trimmed["fund_lookthrough"]
    assert lookthrough["status"] == "qualified"
    assert lookthrough["research_eligible"] is True
    assert lookthrough["execution_qualified"] is False
    assert lookthrough["portfolio"]["unknown_account_mass_percent"] == 39.0
    assert lookthrough["portfolio"]["top_security_exposure_lower_bounds"][0]["security_key"] == "600519"
    assert lookthrough["portfolio"]["top_security_exposure_lower_bounds"][0]["exposure_lower_bound_percent"] == 7.4
    assert "candidates" not in lookthrough
    assert "disclaimer" not in lookthrough
    assert "research_hash" not in lookthrough
    # 声明审计是对模型自己叙述的事后检查，不能回喂给模型。
    assert "fund_lookthrough_claim_audit" not in trimmed


def test_lookthrough_resolver_error_stays_fail_closed_with_the_key_present() -> None:
    def exploding_resolver(*_args, **_kwargs):
        raise RuntimeError("disclosure store down")

    facts = _bundle(exploding_resolver).facts

    lookthrough = facts["fund_lookthrough"]
    assert lookthrough["status"] == "unavailable"
    # 超时与异常不共用一个原因码，运维才能分清"数据源慢"和"数据源坏"。
    assert "lookthrough_context_error" in lookthrough["reason_codes"]
    assert lookthrough["scope"] == "portfolio_only"

    evidence_items = {
        str(item.get("fact_id")): item
        for item in facts["data_evidence"]["items"]
        if isinstance(item, dict)
    }
    # 键仍在，但明确不可用——下游据此区分"拿不到披露"与"忘了算"。
    portfolio_item = evidence_items["fund_lookthrough:portfolio"]
    assert portfolio_item["source"] == "unavailable"
    assert portfolio_item["confidence"] == "none"
    assert portfolio_item["freshness"] == "unavailable"


def test_lookthrough_context_own_unavailable_result_is_passed_through() -> None:
    """生产里 `build_fund_lookthrough_context` 自己吞异常并返回 unavailable 载荷。

    这条路径与"注入的 resolver 抛异常"不同，必须同样收敛成有界形状且不可用。
    """
    production_shape = {
        "schema_version": "fund_lookthrough_research.v1",
        "status": "unavailable",
        "decision_at": DECISION_AT.isoformat(),
        "reason_codes": ["lookthrough_context_error", "TimeoutError"],
        "research_qualified": False,
        "execution_qualified": False,
        "raw_holdings_included": False,
    }

    facts = _bundle(lambda *_a, **_k: production_shape).facts

    lookthrough = facts["fund_lookthrough"]
    assert lookthrough["status"] == "unavailable"
    assert lookthrough["reason_codes"] == [
        "lookthrough_context_error",
        "TimeoutError",
    ]
    assert lookthrough["research_qualified"] is False
    # 缺 portfolio 时不得凭空造出暴露列表。
    assert lookthrough["portfolio"]["top_security_exposure_lower_bounds"] == []


def test_lookthrough_timeout_falls_back_without_blocking_the_report() -> None:
    """外层预算到点就放弃等待，日报照常出，原因码与异常路径区分开。"""
    import threading

    release = threading.Event()

    def slow_resolver(*_args, **_kwargs):
        release.wait(timeout=5.0)
        return _lookthrough_payload()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(analysis_payload_module, "LOOKTHROUGH_TIMEOUT_SECONDS", 0.25)
        try:
            facts = _bundle(slow_resolver).facts
        finally:
            release.set()

    lookthrough = facts["fund_lookthrough"]
    assert lookthrough["status"] == "unavailable"
    assert lookthrough["reason_codes"] == ["lookthrough_context_timeout"]


def test_benchmark_metrics_are_joined_onto_each_holding_row() -> None:
    """行内基准投影必须挂到落库 holdings；描述性明细不再喂给模型。

    载体质量与前端仍读行内 `benchmark_metrics`；动作由守卫决定，prompt 里再放一份
    只会占 token 或诱发「跑赢基准」叙述。
    """
    facts = _bundle(
        lambda *_a, **_k: _lookthrough_payload(),
        benchmark_research={
            "519674": {
                "schema_version": "fund_benchmark_research.v1",
                "status": "qualified",
                "qualified": True,
                "comparison_role": "formal_excess",
                "formal_excess_eligible": True,
                # 序列审计属于内部证据，不该出现在行内投影里。
                "fund_series": {"status": "available", "point_count": 300},
                "tracking_metrics": {
                    "applicable": True,
                    "available": True,
                    "tracking_error_annualized_percent": 0.9,
                },
            }
        },
    ).facts

    rows = {str(row["fund_code"]): row for row in facts["holdings"]}
    joined = rows["519674"]["benchmark_metrics"]
    assert joined["status"] == "qualified"
    assert joined["descriptive_only"] is True
    assert joined["execution_tilt_eligible"] is False
    # tracking_metrics 完整键名保留：后续接载体质量分直接读它。
    assert joined["tracking_metrics"]["tracking_error_annualized_percent"] == 0.9
    assert "fund_series" not in joined
    # 没有基准身份的持仓留空字典，下游能区分"没有基准"与"忘了挂"。
    assert rows["161725"]["benchmark_metrics"] == {}

    trimmed = trim_analysis_facts_for_llm(facts)
    assert "benchmark_metrics" not in trimmed["holdings"][0]
    assert "benchmark_research" not in trimmed
    assert "benchmark_research_contract" not in trimmed
    assert "benchmark_specs" not in trimmed
    assert facts["benchmark_research_contract"]["qualified_count"] == 1


def test_unsupported_lookthrough_narrative_is_sanitized_with_an_audit() -> None:
    """模型不得把"披露范围内没发现重合"说成组合分散、更不能据此支持加仓。"""
    from app.services.fund_lookthrough_claim_validator import (
        validate_fund_lookthrough_claims,
    )

    draft = {
        "title": "日报",
        "summary": "穿透后与其它持仓零重合，分散度好，适合加仓。",
        "fund_recommendations": [
            {
                "fund_code": "519674",
                "fund_name": "银河创新成长",
                "action": "分批加仓",
                "points": ["穿透后两只基金持仓重合度约 45%。"],
            }
        ],
        "caveats": ["以上不构成投资建议。"],
    }

    cleaned, audit = validate_fund_lookthrough_claims(draft, _lookthrough_payload())

    assert audit["schema_version"] == "fund_lookthrough_claim_audit.v1"
    assert audit["status"] == "sanitized"
    reasons = {str(item.get("reason")) for item in audit["changes"]}
    # 「低重合 → 更分散 → 可加仓」这条推理链必须被拦下。
    assert "overlap_used_as_positive_allocation_rationale" in reasons
    # 披露口径无法核验的具体重合百分比同样不能留给用户：portfolio_only 没有候选事实，
    # 逐只卡片里的重合数字无从核验，必须省略。
    assert "overlap_candidate_not_in_facts" in reasons
    # 原始断言被整段换成固定告知语，而不是留一句"零重合所以适合加仓"。
    assert "零重合" not in cleaned["summary"]
    assert "适合加仓" not in cleaned["summary"]
    assert "不能作为买入理由" in cleaned["summary"]
    assert "45%" not in cleaned["fund_recommendations"][0]["points"][0]
    # 动作与代码属于结构化字段，校验器不得改写。
    assert cleaned["fund_recommendations"][0]["action"] == "分批加仓"
    assert cleaned["fund_recommendations"][0]["fund_code"] == "519674"


def test_vehicle_quality_is_attached_after_the_benchmark_join() -> None:
    """载体质量必须读到刚挂上去的行内基准，否则跟踪质量恒为「样本未形成」中性分。

    顺序在 `prepare_analysis_bundle` 里是硬要求：基准投影 → 载体质量。这条用例通过
    「同一份 tracking_metrics 能否驱动出满分跟踪质量」来间接锁住那个顺序——如果哪天有人
    把载体质量提到基准挂载之前，tracking_quality 会掉到 10.0 中性分，这里立刻失败。
    """
    holdings = [
        Holding(
            fund_code="510300",
            fund_name="华泰柏瑞沪深300ETF",
            sector_name="沪深300",
            holding_amount=10_000.0,
        ),
        Holding(
            fund_code="519674",
            fund_name="银河创新成长",
            sector_name="半导体",
            holding_amount=8_000.0,
        ),
    ]
    facts = _bundle(
        lambda *_a, **_k: _lookthrough_payload(),
        holdings=holdings,
        snapshots=[
            FundSnapshot(
                fund_code="510300",
                fund_name="华泰柏瑞沪深300ETF",
                source="test",
                fund_type="指数型",
                management_fee="0.15%",
                fund_scale_yi=50.0,
            ),
            FundSnapshot(
                fund_code="519674",
                fund_name="银河创新成长",
                source="test",
                fund_type="混合型",
                management_fee="1.50%",
                fund_scale_yi=50.0,
            ),
        ],
        benchmark_research={
            "510300": {
                "schema_version": "fund_benchmark_research.v1",
                "status": "qualified",
                "qualified": True,
                "tracking_metrics": {
                    "applicable": True,
                    "available": True,
                    "tracking_error_annualized_percent": 0.9,
                },
            }
        },
    ).facts

    rows = {str(row["fund_code"]): row for row in facts["holdings"]}

    passive = rows["510300"]["vehicle_quality"]
    assert passive["applicable"] is True
    assert passive["status"] == "eligible"
    # 18.75 = 跟踪误差 <=1% 的满分档；拿到它就证明行内基准在此之前已经挂好。
    assert passive["components"]["tracking_quality"] == 18.75
    assert "跟踪误差较低" in passive["reasons"]

    # 主动持仓明确不适用，而不是 0 分 watch_only——缺经理业绩证据不等于载体更差。
    active = rows["519674"]["vehicle_quality"]
    assert active["applicable"] is False
    assert active["status"] == "not_applicable"
    assert active["score"] is None

    # 载体质量要进 prompt：模型只需知道这只工具合不合格，不必看完整打分树。
    trimmed = trim_analysis_facts_for_llm(facts)
    trimmed_rows = {str(row["fund_code"]): row for row in trimmed["holdings"]}
    assert trimmed_rows["510300"]["vehicle_quality"]["status"] == "eligible"
    assert trimmed_rows["510300"]["vehicle_quality"]["reasons"]
    assert "components" not in trimmed_rows["510300"]["vehicle_quality"]
    assert trimmed_rows["519674"]["vehicle_quality"]["applicable"] is False


def test_peer_research_is_attached_per_holding_and_reaches_the_prompt() -> None:
    """同类分位挂到落库行内；算不到的持仓必须带显式不可用而不是缺键。

    缺键与"同类里不占优"是两件事：前者是没有证据，后者是负面证据。落库行上永远有
    这个键，前端才不会把缺席读成利空。描述性分位不得倾斜仓位，因此不再喂给模型。
    """
    facts = _bundle(
        lambda *_a, **_k: _lookthrough_payload(),
        peer_research={
            "519674": {
                "available": True,
                "status": "descriptive_only",
                "execution_tilt_eligible": False,
                "descriptive_only": True,
                "group_label": "主动股票",
                "independent_peer_family_count": 31,
                "metrics": {
                    "return_3m_percent": {
                        "applicable": True,
                        "available": True,
                        "percentile": 62.5,
                    }
                },
            }
        },
    ).facts

    rows = {str(row["fund_code"]): row for row in facts["holdings"]}
    ranked = rows["519674"]["peer_research"]
    assert ranked["status"] == "descriptive_only"
    assert ranked["metrics"]["return_3m_percent"]["percentile"] == 62.5
    # 另一只没算到：键仍在，且执行语义显式为 false。
    unranked = rows["161725"]["peer_research"]
    assert unranked["available"] is False
    assert unranked["execution_tilt_eligible"] is False

    trimmed = trim_analysis_facts_for_llm(facts)
    trimmed_rows = {str(row["fund_code"]): row for row in trimmed["holdings"]}
    assert "peer_research" not in trimmed_rows["519674"]
    assert "peer_research" not in trimmed_rows["161725"]


def test_peer_research_resolver_failure_leaves_every_row_marked_unavailable() -> None:
    """同类分位是描述性证据，算不出来不得阻塞日报，也不得让键消失。"""

    def exploding_resolver(*_args, **_kwargs):
        raise RuntimeError("catalogue cache down")

    request = _request()
    snapshots = [
        FundSnapshot(fund_code=h.fund_code, fund_name=h.fund_name, source="test")
        for h in request.holdings
    ]
    bundle = prepare_analysis_bundle(
        request,
        RiskAssessment(
            level="medium",
            suggested_action="watch",
            weighted_return_percent=1.0,
            alerts=[],
        ),
        snapshots,
        [],
        budget_enhancements=True,
        decision_at=DECISION_AT,
        tradeability_resolver=lambda *_a, **_k: {},
        benchmark_resolver=lambda *_a, **_k: {},
        benchmark_research_resolver=lambda *_a, **_k: {},
        lookthrough_resolver=lambda *_a, **_k: _lookthrough_payload(),
        peer_research_resolver=exploding_resolver,
    )

    for row in bundle.facts["holdings"]:
        assert row["peer_research"]["available"] is False
        assert row["peer_research"]["execution_tilt_eligible"] is False
