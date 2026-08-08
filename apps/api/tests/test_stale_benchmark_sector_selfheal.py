"""存量基准派生板块的读时自愈。

指数身份表被修正后，已经落库的 `benchmark_index` 行不会自动重算：全局行要等 TTL
过期，用户行**根本没有 TTL**。历史上这让「代码已经修好、页面还是旧板块」成为常态——
015788 鹏扬中证数字经济主题ETF发起联接C 明明跟踪 931582（数字经济），页面却长期
显示「信创」（931247），两者日均偏差 1.80pp 且出现过方向相反。

唯一的补救工具 `scripts/invalidate_stale_benchmark_sectors.py` 只能连 SQLite，
线上是 MySQL 部署，所以那次修复从来没有真正落地。这里锁定读时自愈的行为：
每次读取都用当前身份规则复核行里存的跟踪指数代码，对不上就用行里存的基准原文
重放一次（纯内存，不联网），从而在任何存储后端上都能立刻纠正。
"""

from __future__ import annotations

import json

import pytest

from app.services import fund_primary_sector_service as service

FUND_CODE = "015788"
FUND_NAME = "鹏扬中证数字经济主题ETF发起联接C"
BENCHMARK_TEXT = "中证数字经济主题指数收益率×95%+银行活期存款利率（税后）×5%"


def _row(
    sector_name: str,
    index_code: str | None,
    *,
    source: str = "benchmark_index",
    detail_as_json_text: bool = False,
    benchmark_text: str = BENCHMARK_TEXT,
) -> dict:
    detail: dict = {
        "benchmark_text": benchmark_text,
        "index_name": "中证数字经济主题指数",
        "relation_kind": "tracking_reference",
        "price_proxy_eligible": True,
    }
    if index_code is not None:
        detail["index_code"] = index_code
    return {
        "fund_code": FUND_CODE,
        "sector_name": sector_name,
        "intraday_index_name": sector_name,
        "source": source,
        "confidence": 0.68,
        "detail": json.dumps(detail, ensure_ascii=False) if detail_as_json_text else detail,
    }


class TestBenchmarkRowIdentityCheck:
    def test_stale_label_is_detected(self) -> None:
        # 旧规则直接采信 AMAC 主题标签，把 931582 写成了「信创」。
        assert service._benchmark_row_identity_is_current(_row("信创", "931582")) is False

    def test_matching_label_is_kept(self) -> None:
        assert service._benchmark_row_identity_is_current(_row("数字经济", "931582")) is True

    def test_genuine_xinchuang_fund_is_untouched(self) -> None:
        # 931247 是中证信创指数本体，这类基金的「信创」是对的，不能被顺手改掉。
        assert service._benchmark_row_identity_is_current(_row("信创", "931247")) is True

    def test_code_that_lost_its_identity_is_stale(self) -> None:
        # 399262 是已知错码（东财该代码是别的标的），当前规则不再发板块。
        assert service._benchmark_row_identity_is_current(_row("信创", "399262")) is False

    def test_legacy_row_without_index_code_is_left_alone(self) -> None:
        # 没存跟踪码就无从校验，放行而不是猜测。
        assert service._benchmark_row_identity_is_current(_row("信创", None)) is True


class TestBenchmarkRowSelfHeal:
    @pytest.mark.parametrize("stale_code", ["931582", "399262"])
    def test_stale_row_is_remapped_to_the_current_sector(self, stale_code: str) -> None:
        record = service._usable_benchmark_row_record(_row("信创", stale_code), FUND_CODE)

        assert record is not None
        assert record.sector_name == "数字经济"
        assert record.source == "benchmark_index"
        assert record.detail["index_code"] == "931582"
        # 保留纠正痕迹，便于事后核对这条行为什么变了。
        assert record.detail["remapped_from_sector_name"] == "信创"

    def test_detail_stored_as_json_text_is_also_repaired(self) -> None:
        record = service._usable_benchmark_row_record(
            _row("信创", "931582", detail_as_json_text=True), FUND_CODE
        )

        assert record is not None
        assert record.sector_name == "数字经济"

    def test_healthy_row_is_returned_verbatim(self) -> None:
        record = service._usable_benchmark_row_record(_row("数字经济", "931582"), FUND_CODE)

        assert record is not None
        assert record.sector_name == "数字经济"
        # 未触发重放时不写纠正痕迹。
        assert "remapped_from_sector_name" not in (record.detail or {})

    def test_unresolvable_row_yields_no_sector(self) -> None:
        # 重放也核验不出身份时宁可"暂无关联板块"，也不继续展示错的涨跌归因。
        stale = _row("信创", "399262", benchmark_text="某不存在的私募指数收益率*100%")

        assert service._replay_benchmark_row(stale, FUND_CODE, persist=False) is None
        assert service._usable_benchmark_row_record(stale, FUND_CODE) is None

    def test_row_without_benchmark_text_cannot_be_replayed(self) -> None:
        stale = {
            "fund_code": FUND_CODE,
            "sector_name": "信创",
            "source": "benchmark_index",
            "detail": {"index_code": "931582"},
        }

        assert service._usable_benchmark_row_record(stale, FUND_CODE) is None


class TestUnverifiableRows:
    """`detail` 只剩 fund_name 的行无从复核身份。

    历史上 `upsert_primary_sector_from_holding` 无条件把 detail 覆盖成
    `{"fund_name": ...}`，把刚存好的 index_code / benchmark_text 冲掉，于是这条行
    再也没法参与身份复核。能重新抓基准时就别再采信它。
    """

    def _row(self) -> dict:
        return {
            "fund_code": FUND_CODE,
            "sector_name": "信创",
            "source": "benchmark_index",
            "confidence": 0.88,
            "detail": {"fund_name": FUND_NAME},
        }

    def test_identity_is_reported_as_unverifiable(self) -> None:
        assert service._benchmark_row_identity_is_verifiable(self._row()) is False
        assert service._benchmark_row_identity_is_verifiable(_row("信创", "931582")) is True

    def test_kept_when_the_caller_cannot_refetch(self) -> None:
        # 低时延读路径拿不到网络，沿用旧值，不引入额外时延。
        record = service._usable_benchmark_row_record(
            self._row(), FUND_CODE, trust_unverifiable=True
        )

        assert record is not None
        assert record.sector_name == "信创"

    def test_dropped_when_the_caller_can_refetch(self) -> None:
        assert (
            service._usable_benchmark_row_record(
                self._row(), FUND_CODE, trust_unverifiable=False
            )
            is None
        )

    def test_resolution_refetches_the_benchmark(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(service, "get_fund_primary_sector", lambda _code: self._row())
        monkeypatch.setattr(service, "load_fresh_global_sector", lambda _code: None)
        monkeypatch.setattr(service, "get_fund_profile_by_code", lambda _code: None)
        monkeypatch.setattr(service, "try_get_request_user_id", lambda: None)
        monkeypatch.setattr(service, "promote_record_to_global", lambda record: None)
        monkeypatch.setattr(
            "app.services.fund_benchmark_sector.fetch_fund_benchmark_text",
            lambda _code: BENCHMARK_TEXT,
        )

        record = service.resolve_primary_sector(
            FUND_CODE, fund_name=FUND_NAME, fetch_benchmark=True
        )

        assert record is not None
        assert record.sector_name == "数字经济"
        assert record.detail["index_code"] == "931582"


class TestBenchmarkProvenanceIsPreserved:
    def test_holding_upsert_keeps_the_tracking_index_code(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """写回持仓板块时必须带上溯源信息，否则下次就无法复核身份。"""
        from app.models import Holding

        saved: dict = {}

        def fake_save(**kwargs):
            saved.update(kwargs)
            return {"fund_code": FUND_CODE, **kwargs}

        monkeypatch.setattr(service, "get_fund_primary_sector", lambda _code: None)
        monkeypatch.setattr(service, "save_fund_primary_sector", fake_save)

        service.upsert_primary_sector_from_holding(
            Holding(
                fund_code=FUND_CODE,
                fund_name=FUND_NAME,
                holding_amount=509.09,
                sector_name="数字经济",
            ),
            source="benchmark_index",
            detail={"index_code": "931582", "benchmark_text": BENCHMARK_TEXT},
        )

        assert saved["detail"]["index_code"] == "931582"
        assert saved["detail"]["benchmark_text"] == BENCHMARK_TEXT
        assert saved["detail"]["fund_name"] == FUND_NAME

    def test_detail_defaults_to_fund_name_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.models import Holding

        saved: dict = {}
        monkeypatch.setattr(service, "get_fund_primary_sector", lambda _code: None)
        monkeypatch.setattr(
            service,
            "save_fund_primary_sector",
            lambda **kwargs: saved.update(kwargs) or {"fund_code": FUND_CODE, **kwargs},
        )

        service.upsert_primary_sector_from_holding(
            Holding(
                fund_code=FUND_CODE,
                fund_name=FUND_NAME,
                holding_amount=509.09,
                sector_name="数字经济",
            ),
            source="alipay_overview",
        )

        assert saved["detail"] == {"fund_name": FUND_NAME}


class TestResolvePrimarySectorSelfHeal:
    """端到端：持仓页读取路径必须直接拿到纠正后的板块。"""

    @pytest.fixture(autouse=True)
    def _no_network_no_writes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(service, "get_fund_profile_by_code", lambda _code: None)
        monkeypatch.setattr(service, "try_get_request_user_id", lambda: None)
        monkeypatch.setattr(service, "promote_record_to_global", lambda record: None)

    def test_stale_user_row_is_corrected_without_fetching(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            service, "get_fund_primary_sector", lambda _code: _row("信创", "931582")
        )
        monkeypatch.setattr(service, "load_fresh_global_sector", lambda _code: None)

        record = service.resolve_primary_sector(
            FUND_CODE,
            fund_name=FUND_NAME,
            # 读路径不允许联网抓基准，纠正必须来自行里已存的原文。
            fetch_benchmark=False,
        )

        assert record is not None
        assert record.sector_name == "数字经济"

    def test_stale_global_row_is_corrected_without_fetching(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stale_global = _row("信创", "931582", source="precompute_benchmark")
        stale_global.pop("fund_code")
        monkeypatch.setattr(service, "get_fund_primary_sector", lambda _code: None)
        monkeypatch.setattr(service, "load_fresh_global_sector", lambda _code: stale_global)

        record = service.resolve_primary_sector(
            FUND_CODE,
            fund_name=FUND_NAME,
            fetch_benchmark=False,
        )

        assert record is not None
        assert record.sector_name == "数字经济"

    def test_batch_context_path_is_also_corrected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            service,
            "get_fund_primary_sector",
            lambda _code: pytest.fail("batch path must not issue point queries"),
        )
        context = service.PrimarySectorBatchContext(
            user_rows_by_code={FUND_CODE: _row("信创", "931582")}
        )

        record = service.resolve_primary_sector(
            FUND_CODE,
            fund_name=FUND_NAME,
            fetch_benchmark=False,
            batch_context=context,
        )

        assert record is not None
        assert record.sector_name == "数字经济"

    def test_holding_display_label_is_overwritten(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """快照里存的旧板块名也必须被纠正，否则会被再次写回成 alipay_overview。"""
        from app.models import Holding

        monkeypatch.setattr(
            service, "get_fund_primary_sector", lambda _code: _row("信创", "931582")
        )
        monkeypatch.setattr(service, "load_fresh_global_sector", lambda _code: None)
        monkeypatch.setattr(
            service, "upsert_primary_sector_from_holding", lambda *a, **k: None
        )

        holding = Holding(
            fund_code=FUND_CODE,
            fund_name=FUND_NAME,
            holding_amount=509.09,
            sector_name="信创",
        )
        updated = service.apply_primary_sector_to_holding(holding, fetch_benchmark=False)

        assert updated.sector_name == "数字经济"
