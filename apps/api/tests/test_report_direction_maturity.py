"""日报接入方向成熟度（`sector_entry_maturity.2026-08.v3`）。

背景：这一层在日报侧此前**完全不存在**，但不是因为缺代码——`report_sector_opportunity`
一直在调 `describe_sector_opportunity`，而后者的 `entry_policy_enabled` 默认 False，
于是日报长期只拿到旧版机会分，`mainline_regime` 恒为 None。结果是同一天同一板块，
荐基对一个过热方向只按 40% 试仓、日报却给满档加仓，两个界面互相矛盾。

日报不自己算主线快照（逐板块日线请求 + 只对持仓板块算会让横截面分位只有 3~5 个样本
+ 纯缓存路径拿不到基准腿），只复用荐基当天冻结的那一份。因此有一条明确边界：
**当天没跑过发现基金就没有成熟度**，必须干净地退回旧行为——这是本文件最重要的断言。
"""

import pytest

from app.models import (
    AnalysisRequest,
    FundRecommendation,
    Holding,
    InvestorProfile,
    NewsItem,
    RiskAssessment,
)
from app.services import report_sector_opportunity as report_sector_opportunity_module
from app.services.mainline_snapshot_repository import (
    load_mainline_snapshot_for_trade_date,
)
from app.services.recommendation_guard import (
    _entry_state_add_block_reason,
    _first_tranche_scaled_percent,
    _resolve_deterministic_position_change,
    _weak_evidence_reasons,
    apply_recommendation_guards,
)
from app.services.report_sector_opportunity import (
    build_holding_sector_opportunity_context,
    resolve_holding_mainline_context,
)
from app.services.sector_opportunity_scoring import describe_sector_opportunity

TRADE_DATE = "2026-08-07"


def _mainline_row(*, status: str = "leading", label: str = "半导体") -> dict:
    """一个足以让 `_supports_entry_maturity_v2` 认可的 regime 行。"""
    return {
        "schema_version": "mainline_regime.v1",
        "sector_label": label,
        "status": status,
        "score": 78.0,
        "confidence": "高",
        "feature_coverage": 0.9,
        "component_scores": {
            "trend": 72.0,
            "participation": 64.0,
            "position": 58.0,
        },
        "trend_strength_score": 72.0,
        "participation_score": 64.0,
        "price_position_score": 58.0,
        "relative_strength_percentile": 83.0,
    }


def _heat_row(label: str = "半导体") -> dict:  # noqa: D401 - 见下方 fixture 说明
    return {
        "sector_label": label,
        "change_1d_percent": 1.4,
        "change_5d_percent": 5.2,
        "heat_score": 62.0,
    }


def _flow_row() -> dict:
    return {
        "available": True,
        "date_aligned": True,
        "today_available": True,
        "five_day_available": True,
        "today_main_force_net_yi": 6.2,
        "cumulative_5d_net_yi": 18.4,
    }


# --------------------------------------------------------------------------- #
# 打分层：成熟度只在传入主线且显式开启时才附加
# --------------------------------------------------------------------------- #


def test_without_mainline_the_legacy_scoring_is_unchanged() -> None:
    """这是日报此前的真实行为：没有 entry_state，mainline_regime 恒为 None。"""
    row = describe_sector_opportunity(_heat_row(), _flow_row(), focus={"半导体"})

    assert row is not None
    assert "entry_state" not in row
    assert "first_tranche_scale" not in row
    assert row["mainline_regime"] is None


def test_with_mainline_the_maturity_layer_is_attached() -> None:
    row = describe_sector_opportunity(
        _heat_row(),
        _flow_row(),
        focus={"半导体"},
        mainline=_mainline_row(),
        entry_policy_enabled=True,
    )

    assert row is not None
    assert "entry_state" in row
    assert "first_tranche_scale" in row
    assert row["mainline_regime"] is not None
    assert row["mainline_regime"]["status"] == "leading"
    # opportunity_available 改由 entry_state 决定，不再只看资金派发。
    assert row["opportunity_available"] == (row["entry_state"] != "invalid")


def test_mainline_passed_but_policy_disabled_stays_on_legacy() -> None:
    """两个开关都要显式给——避免哪天有人只传 mainline 就以为成熟度生效了。"""
    row = describe_sector_opportunity(
        _heat_row(),
        _flow_row(),
        focus={"半导体"},
        mainline=_mainline_row(),
    )

    assert "entry_state" not in row


# --------------------------------------------------------------------------- #
# 主线快照复用：严格按交易日匹配，不用过期的顶替
# --------------------------------------------------------------------------- #


def _artifact(snapshot: dict) -> dict:
    return {"payload": {"artifact": {"snapshot": snapshot}}}


def _snapshot(*, trade_date: str, schema_version: str = "mainline_regime_snapshot.v1") -> dict:
    return {
        "schema_version": schema_version,
        "effective_trade_date": trade_date,
        "entry_policy_version": "sector_entry_maturity.2026-08.v3",
        "snapshot_hash": "a" * 64,
        "sector_count": 2,
        "percentile_universe_size": 68,
        "sectors": [_mainline_row(label="半导体"), _mainline_row(label="白酒")],
    }


@pytest.fixture()
def _schema_version() -> str:
    from app.services.mainline_regime import MAINLINE_SNAPSHOT_SCHEMA_VERSION

    return MAINLINE_SNAPSHOT_SCHEMA_VERSION


def test_snapshot_from_a_different_trade_date_is_not_substituted(
    monkeypatch: pytest.MonkeyPatch,
    _schema_version: str,
) -> None:
    """过期主线比没有主线更危险，必须严格按 effective_trade_date 匹配。"""
    monkeypatch.setattr(
        "app.services.mainline_snapshot_repository.list_decision_quality_input_artifacts",
        lambda **_kwargs: [
            _artifact(_snapshot(trade_date="2026-08-06", schema_version=_schema_version))
        ],
    )

    assert (
        load_mainline_snapshot_for_trade_date(user_id=1, trade_date=TRADE_DATE) is None
    )


def test_snapshot_matching_the_trade_date_is_returned(
    monkeypatch: pytest.MonkeyPatch,
    _schema_version: str,
) -> None:
    monkeypatch.setattr(
        "app.services.mainline_snapshot_repository.list_decision_quality_input_artifacts",
        lambda **_kwargs: [
            _artifact(_snapshot(trade_date="2026-08-06", schema_version=_schema_version)),
            _artifact(_snapshot(trade_date=TRADE_DATE, schema_version=_schema_version)),
        ],
    )

    loaded = load_mainline_snapshot_for_trade_date(user_id=1, trade_date=TRADE_DATE)

    assert loaded is not None
    assert loaded["effective_trade_date"] == TRADE_DATE


def test_unknown_snapshot_schema_version_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.mainline_snapshot_repository.list_decision_quality_input_artifacts",
        lambda **_kwargs: [
            _artifact(_snapshot(trade_date=TRADE_DATE, schema_version="something.else"))
        ],
    )

    assert (
        load_mainline_snapshot_for_trade_date(user_id=1, trade_date=TRADE_DATE) is None
    )


def test_repository_error_degrades_to_no_mainline(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(**_kwargs):
        raise RuntimeError("artifact store down")

    monkeypatch.setattr(
        "app.services.mainline_snapshot_repository.list_decision_quality_input_artifacts",
        explode,
    )

    assert (
        load_mainline_snapshot_for_trade_date(user_id=1, trade_date=TRADE_DATE) is None
    )


def test_resolve_context_reports_why_maturity_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """缺快照必须给出可读原因，而不是静默退回让人以为方向不成立。"""
    monkeypatch.setattr(
        report_sector_opportunity_module,
        "build_holding_sector_opportunity_context",
        build_holding_sector_opportunity_context,
    )
    monkeypatch.setattr(
        "app.services.mainline_snapshot_repository.list_decision_quality_input_artifacts",
        lambda **_kwargs: [],
    )

    by_label, meta = resolve_holding_mainline_context(TRADE_DATE)

    assert by_label == {}
    assert meta["available"] is False
    assert meta["reason"] == "mainline_snapshot_missing_for_trade_date"
    assert meta["trade_date"] == TRADE_DATE


def test_resolve_context_without_trade_date_is_unavailable() -> None:
    by_label, meta = resolve_holding_mainline_context(None)

    assert by_label == {}
    assert meta == {"available": False, "reason": "no_trade_date"}


def test_context_threads_mainline_into_held_rows_and_reports_meta() -> None:
    holdings = [
        Holding(
            fund_code="519674",
            fund_name="银河创新成长",
            sector_name="半导体",
            holding_amount=10_000.0,
        )
    ]

    context = build_holding_sector_opportunity_context(
        holdings,
        trade_date=TRADE_DATE,
        fetch_sector_heat=lambda: [_heat_row()],
        mainline_by_label={"半导体": _mainline_row()},
        mainline_meta={"available": True, "source": "discovery_frozen_snapshot"},
    )

    # 传入的 meta 必须原样透出，同时补上跨日滞回的来源披露（日报只读那张状态账本，
    # 所以 `hysteresis_applied` 是否为真取决于能否读到上一交易日的记录）。
    mainline_meta = context["mainline"]
    assert mainline_meta["available"] is True
    assert mainline_meta["source"] == "discovery_frozen_snapshot"
    assert mainline_meta["hysteresis"]["read_only"] is True
    assert mainline_meta["hysteresis_applied"] == mainline_meta["hysteresis"]["applied"]
    held = context["held"]["半导体"]
    assert "entry_state" in held
    assert held["mainline_regime"]["status"] == "leading"


def test_context_without_price_structure_reports_why_and_keeps_legacy_rows() -> None:
    """取不到 20 日价格结构时退回旧版机会分，并如实给出原因。"""
    holdings = [
        Holding(
            fund_code="519674",
            fund_name="银河创新成长",
            sector_name="半导体",
            holding_amount=10_000.0,
        )
    ]

    context = build_holding_sector_opportunity_context(
        holdings,
        trade_date=TRADE_DATE,
        fetch_sector_heat=lambda: [_heat_row()],
        fetch_sector_position=lambda _labels, _date: {},
    )

    assert context["mainline"]["available"] is False
    assert context["mainline"]["reason"] in {
        "sector_position_unavailable",
        "mainline_snapshot_missing_for_trade_date",
        "no_trade_date",
    }
    assert "entry_state" not in context["held"]["半导体"]


def test_context_computes_its_own_mainline_without_any_discovery_run() -> None:
    """日报自己算主线快照——不依赖当天是否跑过发现基金。

    这是本文件最重要的一条：只喂热度 + 持仓板块的价格结构，`entry_state` 与
    `first_tranche_scale` 就必须出现，`mainline.source` 为 `report_computed`。
    """
    holdings = [
        Holding(
            fund_code="519674",
            fund_name="银河创新成长",
            sector_name="半导体",
            holding_amount=10_000.0,
        )
    ]

    def fake_position(labels, _trade_date):
        return {
            label: {
                "available": True,
                "sector_label": label,
                "benchmark_code": "000300",
                "return_10d_percent": 3.1,
                "return_20d_percent": 6.4,
                "return_60d_percent": 14.2,
                "relative_return_10d_percent": 1.2,
                "relative_return_20d_percent": 2.8,
                "relative_return_60d_percent": 5.1,
                "distance_ma20_percent": 2.2,
                "distance_high_percent": -3.5,
                "above_ma20_ratio": 0.8,
                "trend_persistence_ratio": 0.7,
                "proxy_member_count": 30,
            }
            for label in labels
        }

    context = build_holding_sector_opportunity_context(
        holdings,
        trade_date=TRADE_DATE,
        fetch_sector_heat=lambda: [_heat_row(), _heat_row("白酒"), _heat_row("光伏")],
        fetch_sector_position=fake_position,
    )

    mainline = context["mainline"]
    assert mainline["available"] is True
    assert mainline["source"] == "report_computed"
    # 滞回归荐基单方所有，日报这一份是当日原始档位。
    assert mainline["hysteresis_applied"] is False
    # 分位分母是全白名单，不是那几个持仓板块——否则会出现"在 3 个样本里排 83 分位"。
    assert mainline["percentile_universe_size"] >= 3

    held = context["held"]["半导体"]
    assert "entry_state" in held
    assert "first_tranche_scale" in held
    assert held["score_policy_version"] == "sector_entry_maturity.2026-08.v3"


# --------------------------------------------------------------------------- #
# guard 消费：分段试仓系数与档位拦截
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _no_live_intraday_reversal_signal(monkeypatch):
    monkeypatch.setattr(
        "app.services.recommendation_guard.summarize_sector_intraday_for_holding",
        lambda _holding: None,
    )
    monkeypatch.setattr(
        "app.services.recommendation_guard.build_sector_momentum_context",
        lambda _holding, _nav_trend: None,
    )


@pytest.mark.parametrize(
    ("opportunity", "case"),
    [
        (None, "missing"),
        ({}, "empty"),
        ({"score": 85}, "legacy_row_without_entry_state"),
        ({"score": 85, "first_tranche_scale": 1.0}, "scale_one"),
        ("junk", "non_dict"),
    ],
)
def test_absent_maturity_layer_changes_nothing(opportunity, case: str) -> None:
    """最重要的一条：当天没有主线快照时，加仓比例与拦截行为必须与接入前完全一致。"""
    scaled, basis = _first_tranche_scaled_percent(20.0, opportunity)

    assert scaled == 20.0, case
    assert basis is None, case
    assert _entry_state_add_block_reason(opportunity) is None, case


def test_first_tranche_scale_shrinks_the_percentage_multiplicatively() -> None:
    """过热方向：荐基按 60% 试仓，日报必须给出同一口径的比例，而不是满档。"""
    scaled, basis = _first_tranche_scaled_percent(
        20.0,
        {"first_tranche_scale": 0.6, "overheat_flags": ["涨幅透支", "换手过热"]},
    )

    assert scaled == 12.0
    assert "60%" in basis
    assert "涨幅透支、换手过热" in basis


def test_first_tranche_scale_never_raises_the_percentage() -> None:
    scaled, basis = _first_tranche_scaled_percent(10.0, {"first_tranche_scale": 1.8})

    assert scaled == 10.0
    assert basis is None


@pytest.mark.parametrize(
    ("state", "fragment"),
    [
        ("forming", "条件仍在形成中"),
        ("invalid", "未通过入场线"),
        ("ready_on_pullback", "不宜追高"),
    ],
)
def test_non_ready_entry_states_block_add(state: str, fragment: str) -> None:
    reason = _entry_state_add_block_reason({"entry_state": state})

    assert reason is not None
    assert fragment in reason


def test_ready_to_start_does_not_block() -> None:
    assert _entry_state_add_block_reason({"entry_state": "ready_to_start"}) is None


def test_flow_improving_probe_keeps_the_add_open() -> None:
    """资金刚转强的通道已标定，日报继续开门，避免与荐基打架。"""
    reason = _entry_state_add_block_reason(
        {"entry_state": "forming", "flow_improving_probe_eligible": True}
    )

    assert reason is None


def test_probability_early_probe_no_longer_opens_daily_add() -> None:
    """趋势成形信号分未经校准：日报只披露、不给现持仓加仓。"""
    reason = _entry_state_add_block_reason(
        {"entry_state": "forming", "probability_early_probe_eligible": True}
    )

    assert reason is not None
    assert "条件仍在形成中" in reason


def test_weak_evidence_reasons_include_the_entry_state_block() -> None:
    reasons = _weak_evidence_reasons(
        {"entry_state": "forming", "opportunity_available": True, "confidence": "中"},
        {"composite": {"level": "高", "score": 3.0}},
    )

    assert any("条件仍在形成中" in text for text in reasons)


def _request() -> AnalysisRequest:
    return AnalysisRequest(
        holdings=[
            Holding(
                fund_code="519674",
                fund_name="银河创新成长",
                sector_name="半导体",
                holding_amount=10_000.0,
            )
        ],
        profile=InvestorProfile(
            max_drawdown_percent=15,
            concentration_limit_percent=100,
            expected_investment_amount=100_000,
            avoid_chasing=False,
        ),
    )


def test_position_change_applies_scale_after_the_tier_reductions() -> None:
    """三层顺序：板块档位 → 基金证据降档 → 载体质量降档 → 分段试仓系数缩放。"""
    request = _request()
    percent, basis, _note = _resolve_deterministic_position_change(
        "分批加仓",
        holding=request.holdings[0],
        profile=request.profile,
        weight_denominator=100_000,
        sector_opportunity={
            "score": 85,
            "entry_state": "ready_to_start",
            "first_tranche_scale": 0.5,
        },
        evidence={"composite": {"level": "高", "score": 3.0}},
    )

    # 强机会档 20% → 系数 0.5 → 10%
    assert percent == 10.0
    assert "强机会档 20%" in basis
    assert "方向分段试仓系数 50%" in basis


def test_full_guard_downgrades_add_when_direction_is_only_forming() -> None:
    facts = {
        "holdings": [
            {
                "fund_code": "519674",
                "sector_opportunity": {
                    "score": 85,
                    "confidence": "高",
                    "opportunity_available": True,
                    "pattern_label": "price_flow_aligned_up",
                    "entry_state": "forming",
                    "first_tranche_scale": 0.6,
                },
                "evidence": {
                    "composite": {"level": "高", "score": 3.0},
                    "components": [
                        {"source": "factor", "level": "高", "basis": "主因子动量"}
                    ],
                },
            }
        ]
    }

    _, guarded = apply_recommendation_guards(
        [
            FundRecommendation(
                fund_code="519674",
                fund_name="银河创新成长",
                action="分批加仓",
            )
        ],
        [],
        _request(),
        RiskAssessment(
            level="medium",
            weighted_return_percent=1.2,
            suggested_action="watch",
            alerts=[],
        ),
        [NewsItem(topic="半导体", title="半导体行业利好消息", is_today=True)],
        facts=facts,
    )

    assert guarded[0].action == "观察"
    assert any("形成中" in note for note in guarded[0].validation_notes) or any(
        "形成中" in str(point) for point in guarded[0].points
    )


# --------------------------------------------------------------------------- #
# trim：成熟度字段必须穿过 fast 模式投影
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("analysis_mode", ["fast", "deep"])
def test_maturity_fields_survive_the_llm_projection(analysis_mode: str) -> None:
    """服务端按 first_tranche_scale 缩了比例，模型就必须能看到这个系数。

    fast 模式对 `sector_opportunity` 走的是白名单投影；成熟度字段若被投影掉，模型会
    去解释一个被缩过的比例而手上没有依据，叙述必然与服务端结论冲突。
    """
    from app.services.analysis_payload import trim_analysis_facts_for_llm

    facts = {
        "holdings": [
            {
                "fund_code": "519674",
                "sector_opportunity": {
                    "track": "momentum",
                    "confidence": "高",
                    "opportunity_available": True,
                    "entry_hint": "可以开始分批布局",
                    "entry_state": "ready_to_start",
                    "entry_reason": "中期方向、资金确认和价格位置已同时通过入场线。",
                    "first_tranche_scale": 0.6,
                    "trend_formation_probability": 72.0,
                    "waiting_reason_code": "none",
                    "overheat_flags": ["涨幅透支"],
                    "sector_group": "成长",
                    "score_policy_version": "sector_entry_maturity.2026-08.v3",
                    "verbose_history": [{"i": i} for i in range(20)],
                },
            }
        ],
        "sector_direction_maturity": {"available": True},
    }

    trimmed = trim_analysis_facts_for_llm(
        facts,
        analysis_mode=analysis_mode,
    )
    opportunity = trimmed["holdings"][0]["sector_opportunity"]

    assert opportunity["entry_state"] == "ready_to_start"
    assert opportunity["first_tranche_scale"] == 0.6
    assert opportunity["trend_formation_probability"] == 72.0
    assert opportunity["overheat_flags"] == ["涨幅透支"]
    # 可用性说明也要一起到达，模型才能区分"方向未成熟"与"今天没有主线快照"。
    assert trimmed["sector_direction_maturity"]["available"] is True
    # 内部分组键不进 prompt。
    assert "sector_group" not in opportunity
    assert "verbose_history" not in opportunity
    assert "score_policy_version" not in opportunity
