"""板块自动匹配：行业映射与持仓穿透。"""

from __future__ import annotations

from app.services.fund_holdings_sector_infer import (
    HoldingStockRow,
    infer_sector_from_portfolio_stocks,
)
from app.services.fund_industry_theme_map import map_industry_to_theme_label
from app.services.fund_primary_sector_service import resolve_primary_sector


def test_map_industry_to_theme_label_semiconductor():
    assert map_industry_to_theme_label("半导体") == "半导体"


def test_map_industry_to_theme_label_em_industry_name():
    assert map_industry_to_theme_label("半导体设备") == "半导体"


def test_map_industry_to_theme_label_generalizes_to_unregistered_industry_name():
    """白名单没收录的官方行业名也应直接可用，而不是被丢弃。"""
    assert map_industry_to_theme_label("包装印刷") == "包装印刷"
    assert map_industry_to_theme_label(None) is None
    assert map_industry_to_theme_label("") is None


def test_infer_sector_from_portfolio_stocks_accepts_unregistered_theme():
    stocks = [
        HoldingStockRow(name="某印刷龙头", weight=15.0, industry="包装印刷"),
        HoldingStockRow(name="招商银行", weight=2.0, industry="银行"),
    ]
    result = infer_sector_from_portfolio_stocks("519999", stocks)
    assert result is not None
    sector_name, scores, _evidence = result
    assert sector_name == "包装印刷"
    assert scores["包装印刷"] == 15.0


def test_infer_sector_from_portfolio_stocks_weighted_vote():
    stocks = [
        HoldingStockRow(name="北方华创", weight=9.5, industry="半导体"),
        HoldingStockRow(name="中微公司", weight=8.0, industry="半导体"),
        HoldingStockRow(name="招商银行", weight=2.0, industry="银行"),
    ]
    result = infer_sector_from_portfolio_stocks("519674", stocks)
    assert result is not None
    sector_name, scores, evidence = result
    assert sector_name == "半导体"
    assert scores["半导体"] == 17.5
    assert len(evidence) == 3


def test_resolve_primary_sector_skips_name_infer_by_default(monkeypatch):
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_profile_by_code",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service._resolve_from_benchmark_index",
        lambda *_args, **_kwargs: None,
    )

    record = resolve_primary_sector("999999", fund_name="某某国防军工混合")
    assert record is None


def test_resolve_primary_sector_name_infer_only_when_allowed(monkeypatch):
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_profile_by_code",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service._resolve_from_benchmark_index",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.infer_semantic_sector_from_fund_name",
        lambda _fund_name: None,
    )

    record = resolve_primary_sector(
        "999999",
        fund_name="某某国防军工混合",
        allow_name_infer=True,
    )
    assert record is not None
    assert record.source == "name_infer"
    assert record.sector_name == "国防军工"


def test_resolve_primary_sector_uses_semantic_name_when_allowed(monkeypatch):
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_profile_by_code",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service._resolve_from_benchmark_index",
        lambda *_args, **_kwargs: None,
    )

    record = resolve_primary_sector(
        "999998",
        fund_name="天弘科创芯片设计主题ETF发起联接C",
        allow_name_infer=True,
        fetch_benchmark=False,
    )

    assert record is not None
    # "科创芯片设计" 不在主题白名单里，走的是 freeform 兜底分支——单独打上较低优先级的
    # semantic_name_freeform 来源，方便后续持仓穿透/LLM 兜底纠正。
    assert record.source == "semantic_name_freeform"
    assert record.sector_name == "科创芯片设计"
    assert record.confidence >= 0.55


def test_semantic_sector_from_fund_name_matches_competitor_examples():
    from app.services.sector_labels import infer_semantic_sector_from_fund_name

    # 命中主题白名单（sector_registry_data）的用 semantic_name；未命中、靠清洗后
    # freeform 短语兜底的用 semantic_name_freeform（信任度更低，可被持仓穿透/LLM 纠正）。
    cases = {
        "华夏中证电网设备主题ETF发起式联接C": ("电网设备", "semantic_name"),
        "中欧上证科创板人工智能指数C": ("人工智能", "semantic_name"),
        "天弘科创芯片设计主题ETF发起联接C": ("科创芯片设计", "semantic_name_freeform"),
        "富国全球科技互联网股票(QDII)C": ("海外基金", "semantic_name"),
        "天弘全球高端制造混合(QDII)C": ("全球高端制造", "semantic_name_freeform"),
        # "全球精选股票"只是"全球"+纯风格描述词("精选")+产品类型词("股票")的组合，
        # 并非真实主题，退回"海外基金"通用兜底比展示一个没有实际含义的伪主题更贴切。
        "广发全球精选股票(QDII)人民币C": ("海外基金", "semantic_name"),
    }

    for fund_name, (expected_sector, expected_source) in cases.items():
        candidate = infer_semantic_sector_from_fund_name(fund_name)
        assert candidate is not None, fund_name
        assert candidate.sector_name == expected_sector
        assert candidate.source == expected_source
        assert candidate.confidence >= 0.55


def test_semantic_sector_generalizes_beyond_whitelist_for_real_fund_names():
    """真实基金名（含"上证...科创板..."前缀）与全新主题都应产出候选，而不依赖白名单扩容。"""
    from app.services.sector_labels import infer_semantic_sector_from_fund_name

    real_name_candidate = infer_semantic_sector_from_fund_name(
        "天弘上证科创板芯片设计主题ETF发起联接C"
    )
    assert real_name_candidate is not None
    assert real_name_candidate.sector_name == "科创芯片设计"
    assert real_name_candidate.confidence >= 0.55

    novel_theme_candidate = infer_semantic_sector_from_fund_name(
        "某某低度白酒产业精选混合C"
    )
    assert novel_theme_candidate is not None
    assert novel_theme_candidate.confidence >= 0.55
    assert "白酒" in novel_theme_candidate.sector_name

    brand_new_qdii_candidate = infer_semantic_sector_from_fund_name(
        "某某全球新兴市场消费混合(QDII)C"
    )
    assert brand_new_qdii_candidate is not None
    assert brand_new_qdii_candidate.sector_name == "全球新兴市场消费"
    assert brand_new_qdii_candidate.confidence >= 0.55


def test_semantic_sector_rejects_pure_marketing_phrase_with_no_theme():
    """回归测试：'中航机遇领航混合发起C' 这类基金公司+纯营销词组合的基金名，不应该被
    freeform 兜底误当成板块名（历史上曾产出过'中航机遇领航'这种毫无主题含义的假标签）。
    真实主题（如持仓重仓的 CPO/光通信）只能靠持仓穿透或 LLM 兜底才能推断出来。"""
    from app.services.sector_labels import infer_semantic_sector_from_fund_name

    for fund_name in (
        "中航机遇领航混合发起C",
        "某某远见成长混合A",
        "某某睿享精选灵活配置混合C",
    ):
        assert infer_semantic_sector_from_fund_name(fund_name) is None, fund_name


def test_semantic_sector_ignores_generic_product_words():
    from app.services.sector_labels import infer_semantic_sector_from_fund_name

    for fund_name in (
        "某某灵活配置混合C",
        "某某成长精选股票A",
        "某某稳健回报混合C",
    ):
        assert infer_semantic_sector_from_fund_name(fund_name) is None


def test_legacy_name_infer_keeps_existing_keyword_behavior():
    from app.services.sector_labels import infer_sector_label_from_fund_name

    assert infer_sector_label_from_fund_name("某某国防军工混合C") == "国防军工"
    assert infer_sector_label_from_fund_name("某某CPO主题股票A") == "CPO"


def test_qdii_semantic_sector_beats_domestic_benchmark(monkeypatch):
    from app.services.fund_primary_sector_types import PrimarySectorRecord

    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_profile_by_code",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.load_fresh_global_sector",
        lambda _code: {
            "fund_code": _code,
            "sector_name": "电子",
            "source": "precompute_benchmark",
            "confidence": 0.82,
        },
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service._resolve_from_benchmark_index",
        lambda code, **_kwargs: PrimarySectorRecord(
            fund_code=code,
            sector_name="电子",
            intraday_index_name=None,
            source="benchmark_index",
            confidence=0.82,
        ),
    )

    record = resolve_primary_sector(
        "123456",
        fund_name="华夏全球科技先锋混合(QDII)C",
        allow_name_infer=True,
        fetch_benchmark=True,
    )

    assert record is not None
    assert record.source == "semantic_name"
    assert record.sector_name == "海外基金"


def test_qdii_semantic_sector_beats_stale_overview_ocr_detail(monkeypatch):
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: {
            "fund_code": "123456",
            "sector_name": "电子",
            "intraday_index_name": None,
            "source": "ocr_detail",
            "confidence": 0.95,
            "detail": {"fund_name": "华夏全球科技先锋混合(QDII)C"},
        },
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_profile_by_code",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.load_fresh_global_sector",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service._resolve_from_benchmark_index",
        lambda *_args, **_kwargs: None,
    )

    record = resolve_primary_sector(
        "123456",
        fund_name="华夏全球科技先锋混合(QDII)C",
        allow_name_infer=True,
        fetch_benchmark=False,
    )

    assert record is not None
    assert record.source == "semantic_name"
    assert record.sector_name == "海外基金"


def test_apply_primary_sector_to_holdings_matches_competitor_ocr_names(monkeypatch):
    from app.models import Holding
    from app.services.fund_primary_sector_service import apply_primary_sector_to_holdings

    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_profile_by_code",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.load_fresh_global_sector",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service._resolve_from_benchmark_index",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.save_fund_primary_sector",
        lambda **_kwargs: None,
    )

    cases = {
        "华夏全球科技先锋混合(QDII)C": "海外基金",
        "华夏中证电网设备主题ETF联接C": "电网设备",
        "中欧上证科创板人工智能指数C": "人工智能",
        "天弘科创芯片设计ETF联接C": "科创芯片设计",
        "富国全球科技互联网股票(QDII)C": "海外基金",
        "天弘全球高端制造混合(QDII)C": "全球高端制造",
        # "全球精选股票"只是"全球"+泛化描述词组合，不是真实主题，退回"海外基金"。
        "广发全球精选股票(QDII)C": "海外基金",
    }
    holdings = [
        Holding(fund_code=str(index).zfill(6), fund_name=fund_name, holding_amount=100)
        for index, fund_name in enumerate(cases, start=1)
    ]

    updated = apply_primary_sector_to_holdings(holdings, fetch_benchmark=False)

    assert {item.fund_name: item.sector_name for item in updated} == cases


def test_record_should_override_holding_sector_by_priority(monkeypatch):
    """holdings_infer/llm_infer 等更可信来源，应能纠正历史上由低优先级来源写入、
    但格式上"看起来合法"从而被 _is_valid_sector_label 放行的错误标签
    （例如把"机遇领航"这种基金自身营销短语误当成板块名）。"""
    from app.models import Holding
    from app.services.fund_primary_sector_service import _record_should_override_holding_sector
    from app.services.fund_primary_sector_types import PrimarySectorRecord

    holding = Holding(
        fund_code="018957",
        fund_name="中航机遇领航混合发起C",
        sector_name="中航机遇领航",
        holding_amount=100,
    )

    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: {
            "fund_code": "018957",
            "sector_name": "中航机遇领航",
            "source": "alipay_overview",
            "confidence": 0.88,
        },
    )

    # holdings_infer(70) > alipay_overview(50)：应允许覆盖。
    holdings_record = PrimarySectorRecord(
        fund_code="018957",
        sector_name="CPO",
        intraday_index_name=None,
        source="holdings_infer",
        confidence=0.8,
    )
    assert _record_should_override_holding_sector(holding, holdings_record) is True

    # 当前 alipay_overview 记录的"中航机遇领航"其实是基金名称本身的营销短语残留
    # （非真实主题），其"有效优先级"会被下调到远低于 llm_infer(30)，因此 LLM
    # 兜底的判断（结合重仓股猜出"光通信"）应该能够覆盖它。
    llm_record = PrimarySectorRecord(
        fund_code="018957",
        sector_name="光通信",
        intraday_index_name=None,
        source="llm_infer",
        confidence=0.6,
    )
    assert _record_should_override_holding_sector(holding, llm_record) is True

    # 同理，semantic_name_freeform(25) 也应该能覆盖这种"残留"标签。
    freeform_record = PrimarySectorRecord(
        fund_code="018957",
        sector_name="机遇领航",
        intraday_index_name=None,
        source="semantic_name_freeform",
        confidence=0.58,
    )
    assert _record_should_override_holding_sector(holding, freeform_record) is True


def test_record_should_override_holding_sector_keeps_genuine_alipay_label(monkeypatch):
    """alipay_overview 记录的是真实行业分类（非基金名称残留）时，llm_infer(30) 这种
    更低优先级的猜测不应该反复覆盖它，避免 LLM 兜底结果抖动已经可信的标签。"""
    from app.models import Holding
    from app.services.fund_primary_sector_service import _record_should_override_holding_sector
    from app.services.fund_primary_sector_types import PrimarySectorRecord

    holding = Holding(
        fund_code="016032",
        fund_name="华夏中证电网设备主题ETF发起式联接C",
        sector_name="电网设备",
        holding_amount=100,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: {
            "fund_code": "016032",
            "sector_name": "电网设备",
            "source": "alipay_overview",
            "confidence": 0.88,
        },
    )

    llm_record = PrimarySectorRecord(
        fund_code="016032",
        sector_name="新能源",
        intraday_index_name=None,
        source="llm_infer",
        confidence=0.6,
    )
    assert _record_should_override_holding_sector(holding, llm_record) is False


def test_record_should_override_holding_sector_stops_benchmark_flip_flop_for_cross_market_fund(
    monkeypatch,
):
    """回归测试：'天弘全球高端制造混合(QDII)C' 曾经在"机械设备"（业绩基准境内细分
    行业）和"全球高端制造"（基金自身跨市场主题）之间反复横跳——因为两条刷新路径
    （fetch_holdings_infer=True/False）算出的结果不一致、且业绩基准来源总是被
    无条件允许覆盖。这里验证：跨市场主题基金已经有主题标签时，业绩基准不应该
    再抢占它；反过来，主题标签应该能够纠正已经写入的业绩基准标签。"""
    from app.models import Holding
    from app.services.fund_primary_sector_service import _record_should_override_holding_sector
    from app.services.fund_primary_sector_types import PrimarySectorRecord

    holding = Holding(
        fund_code="016665",
        fund_name="天弘全球高端制造混合(QDII)C",
        sector_name="全球高端制造",
        holding_amount=100,
    )

    # 已经有跨市场主题标签时，业绩基准（境内细分行业"机械设备"）不应该再抢占它。
    benchmark_record = PrimarySectorRecord(
        fund_code="016665",
        sector_name="机械设备",
        intraday_index_name="中证高端装备制造指数",
        source="benchmark_index",
        confidence=0.82,
    )
    assert _record_should_override_holding_sector(holding, benchmark_record) is False

    # 反过来：如果当前显示的是业绩基准算出的"机械设备"，主题标签应该能纠正回来。
    holding_with_benchmark_label = holding.model_copy(update={"sector_name": "机械设备"})
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: {
            "fund_code": "016665",
            "sector_name": "机械设备",
            "source": "benchmark_index",
            "confidence": 0.82,
        },
    )
    semantic_record = PrimarySectorRecord(
        fund_code="016665",
        sector_name="全球高端制造",
        intraday_index_name=None,
        source="semantic_name_freeform",
        confidence=0.61,
    )
    assert (
        _record_should_override_holding_sector(holding_with_benchmark_label, semantic_record)
        is True
    )


def test_record_should_override_holding_sector_allows_llm_to_fix_untracked_label(monkeypatch):
    """holding.sector_name 有效但从未被 fund_primary_sectors 记录过来源时（未知来源，
    优先级视为 0），任何有名字的新来源都应该能够补上并接管。"""
    from app.models import Holding
    from app.services.fund_primary_sector_service import _record_should_override_holding_sector
    from app.services.fund_primary_sector_types import PrimarySectorRecord

    holding = Holding(
        fund_code="018957",
        fund_name="中航机遇领航混合发起C",
        sector_name="中航机遇领航",
        holding_amount=100,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: None,
    )

    llm_record = PrimarySectorRecord(
        fund_code="018957",
        sector_name="光通信",
        intraday_index_name=None,
        source="llm_infer",
        confidence=0.6,
    )
    assert _record_should_override_holding_sector(holding, llm_record) is True


def test_apply_primary_sector_overrides_stale_cross_market_sector_on_holding(monkeypatch):
    from app.models import Holding
    from app.services.fund_primary_sector_service import apply_primary_sector_to_holding

    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: {
            "fund_code": "123456",
            "sector_name": "电子",
            "intraday_index_name": None,
            "source": "ocr_detail",
            "confidence": 0.95,
            "detail": {"fund_name": "华夏全球科技先锋混合(QDII)C"},
        },
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_profile_by_code",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.load_fresh_global_sector",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service._resolve_from_benchmark_index",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.save_fund_primary_sector",
        lambda **_kwargs: None,
    )

    updated = apply_primary_sector_to_holding(
        Holding(
            fund_code="123456",
            fund_name="华夏全球科技先锋混合(QDII)C",
            holding_amount=100,
            sector_name="电子",
        ),
        fetch_benchmark=False,
    )

    assert updated.sector_name == "海外基金"


def test_extract_freeform_theme_from_benchmark_for_unregistered_index():
    from app.services.fund_benchmark_sector import extract_freeform_theme_from_benchmark

    assert (
        extract_freeform_theme_from_benchmark(
            "上证科创板芯片设计主题指数收益率×80%+中债综合全价指数收益率×20%"
        )
        == "科创板芯片设计"
    )
    assert extract_freeform_theme_from_benchmark("银行活期存款利率（税后）") is None
    assert extract_freeform_theme_from_benchmark("") is None


def test_extract_freeform_theme_from_benchmark_rejects_broad_market_noise():
    """回归 018957 场景：业绩基准里全是宽基/固收指数（沪深300、中债综合、中证港股通
    综合）时，不应该抠出"综合"这种毫无主题含义的假标签，应该整体判空，交给持仓穿透/
    LLM 兜底去判断真实主题（如 CPO/光通信）。"""
    from app.services.fund_benchmark_sector import extract_freeform_theme_from_benchmark

    assert (
        extract_freeform_theme_from_benchmark(
            "沪深300指数收益率×70%+中债综合指数收益率×25%＋中证港股通综合指数收益率×5%"
        )
        is None
    )
    assert extract_freeform_theme_from_benchmark("中证全债指数收益率×100%") is None


def test_resolve_from_benchmark_index_falls_back_to_freeform_theme(monkeypatch):
    from app.services.fund_primary_sector_service import _resolve_from_benchmark_index

    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.try_get_request_user_id",
        lambda: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.load_fresh_global_sector",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_benchmark_sector.fetch_fund_benchmark_text",
        lambda _code: "上证科创板芯片设计主题指数收益率×80%+中债综合全价指数收益率×20%",
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.promote_record_to_global",
        lambda _record: None,
    )

    record = _resolve_from_benchmark_index("021999", fetch=True, persist_user=False)

    assert record is not None
    assert record.source == "benchmark_freeform"
    assert record.sector_name == "科创板芯片设计"


def test_is_fund_name_residue_label_detects_marketing_phrase_prefix():
    from app.services.fund_primary_sector_service import _is_fund_name_residue_label

    # "中航机遇领航"只是"中航机遇领航混合发起C"去掉产品类型后缀后剩下的营销短语，
    # 并非真实主题——应判定为名称残留。
    assert (
        _is_fund_name_residue_label("中航机遇领航混合发起C", "中航机遇领航") is True
    )


def test_is_fund_name_residue_label_keeps_registered_theme_prefix():
    from app.services.fund_primary_sector_service import _is_fund_name_residue_label

    # "电网设备"不是基金名称的前缀（前面还有"华夏中证"），且是注册主题词，
    # 不应被误判为残留。
    assert (
        _is_fund_name_residue_label(
            "华夏中证电网设备主题ETF发起式联接C", "电网设备"
        )
        is False
    )
    # 即便恰好是名称前缀，只要清洗后命中注册主题（如"半导体"），也不算残留。
    assert _is_fund_name_residue_label("半导体产业混合C", "半导体") is False


def test_upsert_primary_sector_from_profile_rejects_residue_alipay_overview(monkeypatch):
    """总览页 OCR 出的"关联板块"如果只是基金名称残留，不应该被当作可信来源写入，
    否则会用数字优先级永久挡住持仓穿透/LLM 兜底给出的正确结果。"""
    from app.models import FundProfile
    from app.services.fund_primary_sector_service import upsert_primary_sector_from_profile

    save_calls: list[dict] = []
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.save_fund_primary_sector",
        lambda **kwargs: save_calls.append(kwargs),
    )

    upsert_primary_sector_from_profile(
        FundProfile(
            fund_code="018957",
            fund_name="中航机遇领航混合发起C",
            aliases=[],
            sector_name="中航机遇领航",
            source="alipay-overview",
        ),
        source="alipay_overview",
    )
    assert save_calls == []

    # 真实行业分类（非残留）依然应该正常写入。
    upsert_primary_sector_from_profile(
        FundProfile(
            fund_code="016032",
            fund_name="华夏中证电网设备主题ETF发起式联接C",
            aliases=[],
            sector_name="电网设备",
            source="alipay-overview",
        ),
        source="alipay_overview",
    )
    assert len(save_calls) == 1
    assert save_calls[0]["sector_name"] == "电网设备"


def test_resolve_primary_sector_falls_back_to_llm_when_existing_row_is_residue(monkeypatch):
    """存量 fund_primary_sectors 行是名称残留（如"中航机遇领航"）时，即使没有更好的
    业绩基准/持仓穿透结果，也应该继续尝试 LLM 兜底，而不是直接把残留标签当作最终结果。"""
    from app.services.fund_primary_sector_service import resolve_primary_sector

    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: {
            "fund_code": "018957",
            "sector_name": "中航机遇领航",
            "source": "alipay_overview",
            "confidence": 0.88,
        },
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_profile_by_code",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.load_fresh_global_sector",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service._resolve_from_benchmark_index",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service._resolve_from_holdings_infer",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.promote_record_to_global",
        lambda _record: None,
    )

    def _fake_llm(code: str, fund_name: str, **_kwargs):
        return "CPO", 0.7

    monkeypatch.setattr(
        "app.services.fund_sector_llm_infer.infer_sector_via_llm",
        _fake_llm,
    )

    record = resolve_primary_sector(
        "018957",
        fund_name="中航机遇领航混合发起C",
        allow_name_infer=False,
        fetch_benchmark=False,
        fetch_holdings_infer=True,
    )
    assert record is not None
    assert record.source == "llm_infer"
    assert record.sector_name == "CPO"


def test_usable_intraday_index_name_drops_unmapped_benchmark_index_text():
    """回归测试：业绩基准原文抠出来的场内指数名（如"中证高端装备制造指数"）大多不在
    行情源别名表里，前端详情页分时图会拿它直接查询、查不到数据就一直显示"暂无分时
    数据"；而对应的板块短名（如"机械设备"）已经注册过行情源。写入前应该直接不落这个
    查不到数据的指数名，让下游统一退回板块短名，而不是持续扩充指数名别名表。"""
    from app.services.fund_primary_sector_service import _usable_intraday_index_name

    assert _usable_intraday_index_name("中证高端装备制造指数", "机械设备") is None
    # 指数名本身已经有行情源映射时，应该保留（更精确）。
    assert (
        _usable_intraday_index_name("中证半导体材料设备主题指数", "半导体")
        == "中证半导体材料设备主题指数"
    )
    # 板块短名也没有行情源时，保留原始指数名（总比什么都没有强）。
    assert _usable_intraday_index_name("某某冷门指数", "某某冷门主题") == "某某冷门指数"
    assert _usable_intraday_index_name(None, "机械设备") is None


def test_resolve_primary_sector_prefers_cross_market_theme_regardless_of_allow_name_infer(
    monkeypatch,
):
    """回归测试：跨市场主题基金（QDII/全球/海外）的名称主题偏好必须不受 allow_name_infer
    影响——慢路径（fetch_holdings_infer=True 时习惯性传 allow_name_infer=False）和
    快路径（allow_name_infer=True）应该算出同一个结果，否则两次刷新之间会反复横跳。"""
    from app.services.fund_primary_sector_service import resolve_primary_sector

    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service._resolve_from_benchmark_index",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("跨市场主题基金优先，不应该再去查业绩基准")
        ),
    )

    for allow_name_infer in (True, False):
        record = resolve_primary_sector(
            "016665",
            fund_name="天弘全球高端制造混合(QDII)C",
            allow_name_infer=allow_name_infer,
            fetch_benchmark=True,
            fetch_holdings_infer=not allow_name_infer,
        )
        assert record is not None
        assert record.sector_name == "全球高端制造"


def test_resolve_primary_sector_uses_llm_fallback_only_when_holdings_infer_allowed(monkeypatch):
    """LLM 兜底只在愿意花网络时延的慢路径（fetch_holdings_infer=True）触发，且是最后一道兜底。"""
    llm_calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_profile_by_code",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.load_fresh_global_sector",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service._resolve_from_benchmark_index",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service._resolve_from_holdings_infer",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.infer_semantic_sector_from_fund_name",
        lambda _fund_name: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.promote_record_to_global",
        lambda _record: None,
    )

    def _fake_llm(code: str, fund_name: str, **_kwargs):
        llm_calls.append((code, fund_name))
        return "小众另类主题", 0.55

    monkeypatch.setattr(
        "app.services.fund_sector_llm_infer.infer_sector_via_llm",
        _fake_llm,
    )

    # fetch_holdings_infer=False：不应该触发 LLM 调用。
    record = resolve_primary_sector(
        "888888",
        fund_name="某某小众另类主题混合C",
        allow_name_infer=True,
        fetch_benchmark=False,
        fetch_holdings_infer=False,
    )
    assert record is None
    assert llm_calls == []

    # fetch_holdings_infer=True：允许触发 LLM 兜底。
    record = resolve_primary_sector(
        "888888",
        fund_name="某某小众另类主题混合C",
        allow_name_infer=True,
        fetch_benchmark=False,
        fetch_holdings_infer=True,
    )
    assert record is not None
    assert record.source == "llm_infer"
    assert record.sector_name == "小众另类主题"
    assert llm_calls == [("888888", "某某小众另类主题混合C")]


def test_legacy_name_infer_does_not_expand_to_registered_themes():
    from app.services.sector_labels import infer_sector_label_from_fund_name

    assert infer_sector_label_from_fund_name("某某银行指数A") is None
    assert infer_sector_label_from_fund_name("某某黄金ETF联接C") is None
