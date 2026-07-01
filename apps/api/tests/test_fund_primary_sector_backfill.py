"""存量持仓关联板块一次性补跑。"""

from __future__ import annotations

import json

import pytest

from app.models import Holding
from app.services.fund_primary_sector_backfill import (
    backfill_primary_sectors_for_existing_holdings,
    has_backfill_completed,
)
from app.services.fund_primary_sector_types import PrimarySectorRecord


def _holding(fund_code: str, fund_name: str, sector_name: str | None = None) -> Holding:
    return Holding(
        fund_code=fund_code,
        fund_name=fund_name,
        sector_name=sector_name,
        holding_amount=1000.0,
    )


@pytest.fixture(autouse=True)
def _isolated_status_path(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.fund_primary_sector_backfill._status_path",
        lambda: tmp_path / "backfill_status.json",
    )
    yield


def test_has_backfill_completed_false_when_no_status_file():
    assert has_backfill_completed() is False


def test_backfill_resolves_missing_sectors_and_persists_once(monkeypatch):
    monkeypatch.setattr(
        "app.services.fund_primary_sector_backfill.list_distinct_portfolio_user_ids",
        lambda: [1, 2],
    )

    holdings_by_user = {
        1: [
            _holding("000001", "某某全球精选混合(QDII)C"),
            _holding("000002", "某某中证半导体ETF", "半导体"),
        ],
        2: [
            _holding("000001", "某某全球精选混合(QDII)C"),
        ],
    }

    def _fake_load(**_kwargs):
        from app.request_context import try_get_request_user_id

        user_id = try_get_request_user_id()
        return holdings_by_user.get(user_id, []), "snapshot", None, None

    monkeypatch.setattr(
        "app.services.fund_primary_sector_backfill.load_persisted_holdings",
        _fake_load,
    )
    # 000002 已有可信来源（ocr_detail）记录在案，不应被重新尝试解析。
    monkeypatch.setattr(
        "app.services.fund_primary_sector_backfill.get_fund_primary_sector",
        lambda code: (
            {"fund_code": code, "sector_name": "半导体", "source": "ocr_detail", "confidence": 0.95}
            if code == "000002"
            else None
        ),
    )

    resolve_calls: list[str] = []

    def _fake_resolve(code, **_kwargs):
        resolve_calls.append(code)
        if code == "000001":
            return PrimarySectorRecord(
                fund_code=code,
                sector_name="全球科技",
                intraday_index_name=None,
                source="llm_infer",
                confidence=0.6,
                detail={},
            )
        return None

    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.resolve_primary_sector",
        _fake_resolve,
    )

    persisted: list[tuple[int, list[Holding]]] = []

    def _fake_persist(holdings, **_kwargs):
        from app.request_context import try_get_request_user_id

        persisted.append((try_get_request_user_id(), holdings))
        return holdings

    monkeypatch.setattr(
        "app.services.fund_primary_sector_backfill.persist_holdings_after_sector_refresh",
        _fake_persist,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_backfill._PER_CODE_SLEEP_SECONDS",
        0.0,
    )

    stats = backfill_primary_sectors_for_existing_holdings()

    # 000001 在两个用户下都缺板块，但只应解析一次（按 fund_code 去重）。
    assert resolve_calls == ["000001"]
    assert stats["codes_pending"] == 1
    assert stats["codes_resolved"] == 1
    assert stats["users_fixed"] == 2
    assert stats["holdings_fixed"] == 2

    persisted_by_user = dict(persisted)
    assert persisted_by_user[1][0].sector_name == "全球科技"
    assert persisted_by_user[1][1].sector_name == "半导体"  # 已有有效板块，不应被覆盖
    assert persisted_by_user[2][0].sector_name == "全球科技"

    assert has_backfill_completed() is True

    # 再次调用（force=False）应直接跳过，不重复解析/持久化。
    resolve_calls.clear()
    persisted.clear()
    skip_stats = backfill_primary_sectors_for_existing_holdings()
    assert skip_stats.get("skipped") == "already_completed"
    assert resolve_calls == []
    assert persisted == []


def test_backfill_corrects_low_priority_source_when_new_result_outranks_it(monkeypatch):
    """回归 018957 场景：alipay_overview 历史脏数据（"中航机遇领航"）即使格式合法，
    也应该被更高优先级的新解析结果（如 holdings_infer/CPO）纠正；但如果新结果反而
    优先级更低（如 llm_infer 猜出一个不如现有 alipay_overview 可信的答案），不应覆盖。"""
    monkeypatch.setattr(
        "app.services.fund_primary_sector_backfill.list_distinct_portfolio_user_ids",
        lambda: [1, 2],
    )

    holdings_by_user = {
        1: [_holding("018957", "中航机遇领航混合发起C", "中航机遇领航")],
        2: [_holding("018957", "中航机遇领航混合发起C", "中航机遇领航")],
    }

    def _fake_load(**_kwargs):
        from app.request_context import try_get_request_user_id

        user_id = try_get_request_user_id()
        return holdings_by_user.get(user_id, []), "snapshot", None, None

    monkeypatch.setattr(
        "app.services.fund_primary_sector_backfill.load_persisted_holdings",
        _fake_load,
    )

    primary_sector_rows = {
        "018957": {
            "fund_code": "018957",
            "sector_name": "中航机遇领航",
            "source": "alipay_overview",
            "confidence": 0.88,
        }
    }
    monkeypatch.setattr(
        "app.services.fund_primary_sector_backfill.get_fund_primary_sector",
        lambda code: primary_sector_rows.get(code),
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda code: primary_sector_rows.get(code),
    )

    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.resolve_primary_sector",
        lambda code, **_kwargs: PrimarySectorRecord(
            fund_code=code,
            sector_name="CPO",
            intraday_index_name=None,
            source="holdings_infer",
            confidence=0.8,
            detail={},
        ),
    )

    from app.request_context import try_get_request_user_id

    persisted: list[tuple[int, list[Holding]]] = []

    def _fake_persist(holdings, **_kwargs):
        persisted.append((try_get_request_user_id(), holdings))
        return holdings

    monkeypatch.setattr(
        "app.services.fund_primary_sector_backfill.persist_holdings_after_sector_refresh",
        _fake_persist,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_backfill._PER_CODE_SLEEP_SECONDS",
        0.0,
    )

    stats = backfill_primary_sectors_for_existing_holdings()

    assert stats["codes_pending"] == 1
    assert stats["holdings_fixed"] == 2
    persisted_by_user = dict(persisted)
    assert persisted_by_user[1][0].sector_name == "CPO"
    assert persisted_by_user[2][0].sector_name == "CPO"


def test_backfill_cleans_unusable_intraday_index_name_without_full_reresolve(monkeypatch):
    """回归测试：业绩基准原文抠出来的场内指数名（如"中证高端装备制造指数"）查不到
    行情，而板块短名（如"机械设备"）已经注册过行情源——即使这只基金的板块来源是
    高可信度（ocr_detail，不会被判定为需要重新解析），也应该顺手把这个查不到数据
    的指数名清掉，不需要走完整规则链/网络请求。"""
    monkeypatch.setattr(
        "app.services.fund_primary_sector_backfill.list_distinct_portfolio_user_ids",
        lambda: [1],
    )
    stale_holding = Holding(
        fund_code="016665",
        fund_name="天弘全球高端制造混合(QDII)C",
        sector_name="机械设备",
        intraday_index_name="中证高端装备制造指数",
        holding_amount=100.0,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_backfill.load_persisted_holdings",
        lambda **_kwargs: ([stale_holding], "snapshot", None, None),
    )
    # 高可信来源：_needs_backfill 判定不需要重新解析（不会进入 pending_codes/resolve 流程）。
    monkeypatch.setattr(
        "app.services.fund_primary_sector_backfill.get_fund_primary_sector",
        lambda code: {
            "fund_code": code,
            "sector_name": "机械设备",
            "source": "ocr_detail",
            "confidence": 0.95,
        },
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_backfill._PER_CODE_SLEEP_SECONDS",
        0.0,
    )

    persisted: list[list[Holding]] = []

    def _fake_persist(holdings, **_kwargs):
        persisted.append(holdings)
        return holdings

    monkeypatch.setattr(
        "app.services.fund_primary_sector_backfill.persist_holdings_after_sector_refresh",
        _fake_persist,
    )

    stats = backfill_primary_sectors_for_existing_holdings()

    assert stats["codes_pending"] == 0
    assert stats["codes_resolved"] == 0
    assert stats["intraday_index_names_cleaned"] == 1
    assert len(persisted) == 1
    assert persisted[0][0].sector_name == "机械设备"
    assert persisted[0][0].intraday_index_name is None


def test_backfill_does_not_downgrade_when_new_result_has_lower_priority(monkeypatch):
    # 用真实行业标签（非基金名称残留）验证：llm_infer(30) 这种更低优先级的猜测
    # 不应该覆盖已有的、可信的 alipay_overview 分类（避免抖动）。
    # 注意：像"中航机遇领航"这种基金名称本身的营销短语残留，即使 source 也是
    # alipay_overview，也会被判定为"残留标签"从而允许更低优先级来源纠正——
    # 见 test_backfill_resolves_missing_sectors_and_persists_once 系列用例。
    monkeypatch.setattr(
        "app.services.fund_primary_sector_backfill.list_distinct_portfolio_user_ids",
        lambda: [1],
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_backfill.load_persisted_holdings",
        lambda **_kwargs: (
            [_holding("016032", "华夏中证电网设备主题ETF发起式联接C", "电网设备")],
            "snapshot",
            None,
            None,
        ),
    )
    primary_sector_rows = {
        "016032": {
            "fund_code": "016032",
            "sector_name": "电网设备",
            "source": "alipay_overview",
            "confidence": 0.88,
        }
    }
    monkeypatch.setattr(
        "app.services.fund_primary_sector_backfill.get_fund_primary_sector",
        lambda code: primary_sector_rows.get(code),
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda code: primary_sector_rows.get(code),
    )
    # llm_infer(30) < alipay_overview(50)：新结果优先级更低，不应覆盖已有标签。
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.resolve_primary_sector",
        lambda code, **_kwargs: PrimarySectorRecord(
            fund_code=code,
            sector_name="新能源",
            intraday_index_name=None,
            source="llm_infer",
            confidence=0.6,
            detail={},
        ),
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_backfill.persist_holdings_after_sector_refresh",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not persist")),
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_backfill._PER_CODE_SLEEP_SECONDS",
        0.0,
    )

    stats = backfill_primary_sectors_for_existing_holdings()
    assert stats["holdings_fixed"] == 0
    assert stats["users_fixed"] == 0


def test_backfill_calls_get_fund_primary_sector_within_user_context(monkeypatch):
    """回归测试：_needs_backfill / _record_should_override_holding_sector 内部都会调用
    get_fund_primary_sector，这个函数依赖 request_context 里的当前用户 id（未设置会
    直接抛 RuntimeError）。补跑必须保证这些调用都发生在 set_request_user_id 生效期间，
    不能像早期版本那样在 reset_request_user_id 之后才做过滤判断。"""
    from app.request_context import try_get_request_user_id

    monkeypatch.setattr(
        "app.services.fund_primary_sector_backfill.list_distinct_portfolio_user_ids",
        lambda: [1],
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_backfill.load_persisted_holdings",
        lambda **_kwargs: (
            [_holding("018957", "中航机遇领航混合发起C", "中航机遇领航")],
            "snapshot",
            None,
            None,
        ),
    )

    def _strict_get_fund_primary_sector(code):
        if try_get_request_user_id() is None:
            raise RuntimeError("未设置当前用户上下文")
        return {
            "fund_code": code,
            "sector_name": "中航机遇领航",
            "source": "alipay_overview",
            "confidence": 0.88,
        }

    monkeypatch.setattr(
        "app.services.fund_primary_sector_backfill.get_fund_primary_sector",
        _strict_get_fund_primary_sector,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        _strict_get_fund_primary_sector,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.resolve_primary_sector",
        lambda code, **_kwargs: PrimarySectorRecord(
            fund_code=code,
            sector_name="CPO",
            intraday_index_name=None,
            source="holdings_infer",
            confidence=0.8,
            detail={},
        ),
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_backfill.persist_holdings_after_sector_refresh",
        lambda holdings, **_kwargs: holdings,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_backfill._PER_CODE_SLEEP_SECONDS",
        0.0,
    )

    stats = backfill_primary_sectors_for_existing_holdings()
    assert stats["holdings_fixed"] == 1


def test_backfill_skips_holding_with_no_fund_name(monkeypatch):
    monkeypatch.setattr(
        "app.services.fund_primary_sector_backfill.list_distinct_portfolio_user_ids",
        lambda: [1],
    )

    def _fake_load(**_kwargs):
        return [_holding("000003", "")], "snapshot", None, None

    monkeypatch.setattr(
        "app.services.fund_primary_sector_backfill.load_persisted_holdings",
        _fake_load,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.resolve_primary_sector",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not resolve")),
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_backfill.persist_holdings_after_sector_refresh",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not persist")),
    )

    stats = backfill_primary_sectors_for_existing_holdings()
    assert stats["codes_pending"] == 0
    assert stats["users_fixed"] == 0
