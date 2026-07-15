"""业绩比较基准 → 关联板块自动解析。"""

from __future__ import annotations

from app.models import FundProfile, Holding
from app.services.fund_benchmark_sector import (
    parse_benchmark_index,
    resolve_sector_from_benchmark,
)
from app.services.fund_primary_sector_service import (
    apply_primary_sector_to_holding,
    resolve_primary_sector,
)
from app.services.sector_canonical import get_canonical_sector


def test_parse_benchmark_index_semiconductor_material_equipment():
    text = "中证半导体材料设备主题指数收益率×95%+银行活期存款利率（税后）×5%"
    match = parse_benchmark_index(text)
    assert match is not None
    assert match.index_code == "931743"


def test_resolve_sector_from_benchmark_maps_to_display_label():
    text = "中证半导体材料设备主题指数收益率×95%+银行活期存款利率（税后）×5%"
    resolved = resolve_sector_from_benchmark(text)
    assert resolved is not None
    sector_name, intraday, match = resolved
    assert sector_name == "半导体材料"
    assert match.index_code == "931743"
    assert intraday is not None


def test_get_canonical_sector_prefers_semiconductor_material_over_semiconductor():
    canon = get_canonical_sector("半导体材料")
    assert canon is not None
    assert canon.source_code == "931743"
    assert canon.eastmoney_secid == "2.931743"


def test_get_canonical_sector_falls_back_to_theme_board_registry():
    expected = {
        "创新药": ("2.931152", "931152", "index"),
        "计算机": ("2.930651", "930651", "index"),
        "恒生科技": ("2.CESHKB", "CESHKB", "index"),
        "港股医药": ("2.931787", "931787", "index"),
    }

    for label, (secid, source_code, source_type) in expected.items():
        canon = get_canonical_sector(label)
        assert canon is not None
        assert canon.label == label
        assert canon.eastmoney_secid == secid
        assert canon.source_code == source_code
        assert canon.source_type == source_type


def test_resolve_primary_sector_benchmark_beats_alipay_overview_row(monkeypatch):
    benchmark = "中证半导体材料设备主题指数收益率×95%+银行活期存款利率（税后）×5%"

    monkeypatch.setattr(
        "app.services.fund_benchmark_sector.fetch_fund_benchmark_text",
        lambda _code: benchmark,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: {
            "fund_code": "021533",
            "sector_name": "半导体",
            "source": "alipay_overview",
            "intraday_index_name": None,
        },
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_profile_by_code",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.save_fund_primary_sector",
        lambda **kwargs: kwargs,
    )

    record = resolve_primary_sector("021533", fund_name="天弘半导体设备指数C")
    assert record is not None
    assert record.source == "benchmark_index"
    assert record.sector_name == "半导体材料"


def test_benchmark_fetch_ignores_holdings_global_cache_when_fetch_enabled(monkeypatch):
    benchmark = "中证人工智能主题指数收益率*95%+银行活期存款利率(税后)*5%"
    fetched: list[str] = []

    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.load_fresh_global_sector",
        lambda _code: {
            "fund_code": "026790",
            "sector_name": "半导体",
            "intraday_index_name": None,
            "source": "holdings_infer",
            "confidence": 0.9,
            "detail": {},
        },
    )
    monkeypatch.setattr(
        "app.services.fund_benchmark_sector.fetch_fund_benchmark_text",
        lambda code: fetched.append(code) or benchmark,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.save_fund_primary_sector",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.promote_record_to_global",
        lambda _record: None,
    )

    from app.services.fund_primary_sector_service import _resolve_from_benchmark_index

    record = _resolve_from_benchmark_index("026790", fetch=True)

    assert fetched == ["026790"]
    assert record is not None
    assert record.source == "benchmark_index"
    assert record.sector_name == "人工智能"


def test_benchmark_precompute_does_not_require_user_context(monkeypatch):
    benchmark = "中证人工智能主题指数收益率*95%+银行活期存款利率(税后)*5%"
    fetched: list[str] = []

    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: (_ for _ in ()).throw(RuntimeError("未设置当前用户上下文")),
    )
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
        lambda code: fetched.append(code) or benchmark,
    )

    from app.services.fund_primary_sector_service import _resolve_from_benchmark_index

    record = _resolve_from_benchmark_index(
        "026790",
        fetch=True,
        persist_user=False,
        promote_global=False,
    )

    assert fetched == ["026790"]
    assert record is not None
    assert record.source == "benchmark_index"
    assert record.sector_name == "人工智能"


def test_fast_resolution_prefers_local_benchmark_over_holdings_global(monkeypatch):
    saved: list[dict] = []

    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.load_fresh_global_sector",
        lambda _code: {
            "fund_code": "026790",
            "sector_name": "半导体",
            "intraday_index_name": None,
            "source": "holdings_infer",
            "confidence": 0.9,
            "detail": {},
        },
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: {
            "fund_code": "026790",
            "sector_name": "人工智能",
            "intraday_index_name": "中证人工智能",
            "source": "benchmark_index",
            "confidence": 0.82,
            "detail": {},
        },
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.save_fund_primary_sector",
        lambda **kwargs: saved.append(kwargs),
    )
    monkeypatch.setattr(
        "app.services.fund_benchmark_sector.fetch_fund_benchmark_text",
        lambda _code: (_ for _ in ()).throw(AssertionError("benchmark fetch should be skipped")),
    )

    holding = Holding(
        fund_code="026790",
        fund_name="中欧上证科创板人工智能指数C",
        holding_amount=3000.0,
        sector_name=None,
        intraday_index_name=None,
    )

    updated = apply_primary_sector_to_holding(holding, fetch_benchmark=False)

    assert updated.sector_name == "人工智能"
    assert updated.intraday_index_name == "中证人工智能"
    assert saved == [
        {
            "fund_code": "026790",
            "sector_name": "人工智能",
            "intraday_index_name": "中证人工智能",
            "source": "benchmark_index",
            "confidence": 0.88,
            "detail": {"fund_name": "中欧上证科创板人工智能指数C"},
        }
    ]


def test_fetch_fund_benchmark_text_falls_back_when_akshare_unavailable(monkeypatch):
    monkeypatch.setattr(
        "app.services.fund_benchmark_sector.subprocess.run",
        lambda *args, **kwargs: type("R", (), {"returncode": 1, "stdout": ""})(),
    )
    from app.services.fund_benchmark_sector import fetch_fund_benchmark_text

    text = fetch_fund_benchmark_text("021533")
    assert text is not None
    assert "931743" in text or "半导体材料设备" in text


def test_fetch_fund_benchmark_text_marks_xq_akshare_as_aggregator(monkeypatch):
    monkeypatch.setattr(
        "app.services.fund_benchmark_sector.subprocess.run",
        lambda *args, **kwargs: type(
            "R",
            (),
            {
                "returncode": 0,
                "stdout": (
                    '{"text":"reference index 931743 x 100%",'
                    '"kind":"performance_benchmark"}'
                ),
            },
        )(),
    )
    from app.services.fund_benchmark_sector import (
        fetch_fund_benchmark_text,
        get_fund_benchmark_fetch_metadata,
    )

    text = fetch_fund_benchmark_text("021533")
    assert text == "reference index 931743 x 100%"
    metadata = get_fund_benchmark_fetch_metadata("021533", text)
    assert metadata["benchmark_text_kind"] == "performance_benchmark"
    assert metadata["benchmark_text_source_kind"] == "xq_akshare_aggregator"


def test_resolve_primary_sector_021533_uses_benchmark(monkeypatch):
    benchmark = "中证半导体材料设备主题指数收益率×95%+银行活期存款利率（税后）×5%"

    monkeypatch.setattr(
        "app.services.fund_benchmark_sector.fetch_fund_benchmark_text",
        lambda _code: benchmark,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_profile_by_code",
        lambda _code: FundProfile(
            fund_code="021533",
            fund_name="天弘半导体设备指数C",
            sector_name="半导体",
            source="alipay-overview",
        ),
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.save_fund_primary_sector",
        lambda **kwargs: kwargs,
    )

    record = resolve_primary_sector("021533", fund_name="天弘半导体设备指数C")
    assert record is not None
    assert record.source == "benchmark_index"
    assert record.sector_name == "半导体材料"
    assert record.detail is not None
    assert record.detail["index_code"] == "931743"


def test_apply_primary_sector_overrides_wrong_semiconductor_on_holding(monkeypatch):
    benchmark = "中证半导体材料设备主题指数收益率×95%+银行活期存款利率（税后）×5%"

    monkeypatch.setattr(
        "app.services.fund_benchmark_sector.fetch_fund_benchmark_text",
        lambda _code: benchmark,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_profile_by_code",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.save_fund_primary_sector",
        lambda **kwargs: kwargs,
    )

    holding = Holding(
        fund_code="021533",
        fund_name="天弘半导体设备指数C",
        holding_amount=3000.0,
        sector_name="半导体",
    )
    updated = apply_primary_sector_to_holding(holding)
    assert updated.sector_name == "半导体材料"
