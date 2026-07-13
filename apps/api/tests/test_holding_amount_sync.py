"""盘中持有金额应保持上一交易日结算额，不因份额×净值漂移而抬升。"""

import pytest

from app.models import FundProfile, Holding
from app.services.holding_amount_sync import (
    bootstrap_holding_baselines,
    resolve_display_settled_amount,
    sync_holding_amounts_from_shares,
)


def _intraday_session(monkeypatch):
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_effective_trade_date",
        lambda: "2026-06-26",
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_official_nav_return",
        lambda _code, _date: None,
    )


def test_intraday_does_not_roll_settled_when_shares_times_nav_drifts(monkeypatch):
    """OCR 结算额与 shares×昨净值有偏差时，盘中不得改持有金额展示。"""
    _intraday_session(monkeypatch)
    profile = FundProfile(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=9068.69,
        settled_holding_amount=9068.69,
        holding_shares=1000.0,
        holding_cost=8.5,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.list_fund_profiles",
        lambda: [profile],
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_latest_unit_nav",
        lambda _code, **_kwargs: 9.10038,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.fetch_fund_estimate_quotes",
        lambda *_args, **_kwargs: {},
    )

    holding = Holding(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=9100.38,
        settled_holding_amount=9068.69,
    )
    synced = sync_holding_amounts_from_shares(
        [holding], persist_profiles=False, allow_nav_fetch=False
    )
    assert synced[0].settled_holding_amount == 9068.69
    assert synced[0].holding_amount == 9068.69


def test_intraday_repairs_polluted_profile_holding_amount(monkeypatch):
    """档案 holding_amount 被旧 sync 污染时，盘中 sync 应回写 OCR 结算锚点。"""
    _intraday_session(monkeypatch)
    profile = FundProfile(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=9100.38,
        settled_holding_amount=9068.69,
        holding_shares=1000.0,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.list_fund_profiles",
        lambda: [profile],
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_latest_unit_nav",
        lambda _code, **_kwargs: 9.10038,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.fetch_fund_estimate_quotes",
        lambda *_args, **_kwargs: {},
    )
    # 显式 cache_only=True 走缓存快路径，避免真实 AkShare 子进程在无网络的
    # CI/沙箱环境里长时间挂起（曾因此触发 pytest-timeout）。
    holding = Holding(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=9100.38,
        settled_holding_amount=9068.69,
    )
    synced = sync_holding_amounts_from_shares(
        [holding],
        persist_profiles=False,
        allow_nav_fetch=False,
    )
    assert synced[0].holding_amount == 9068.69
    assert synced[0].settled_holding_amount == 9068.69


def test_resolve_settled_ignores_polluted_profile_holding_amount():
    from app.services.holding_amount_sync import _resolve_settled_amount

    holding = Holding(
        fund_code="008586",
        fund_name="测试",
        holding_amount=9068.69,
        settled_holding_amount=9068.69,
    )
    profile = FundProfile(
        fund_code="008586",
        fund_name="测试",
        holding_amount=9100.38,
        settled_holding_amount=9068.69,
    )
    assert _resolve_settled_amount(holding, profile) == 9068.69


def test_official_nav_published_rolls_settled(monkeypatch):
    """当日官方净值公布后滚入 shares×官方净值。"""
    _intraday_session(monkeypatch)
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_official_nav_return",
        lambda _code, _date: -5.02,
    )
    profile = FundProfile(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=9068.69,
        settled_holding_amount=9068.69,
        holding_shares=1000.0,
        holding_cost=8.5,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.list_fund_profiles",
        lambda: [profile],
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_latest_unit_nav",
        lambda _code, **_kwargs: 9.10038,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.fetch_fund_estimate_quotes",
        lambda *_args, **_kwargs: {},
    )

    monkeypatch.setattr(
        "app.services.fund_nav_service.prime_official_nav_cache",
        lambda *_args, **_kwargs: {},
    )
    holding = Holding(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=9068.69,
        settled_holding_amount=9068.69,
    )
    synced = sync_holding_amounts_from_shares([holding], persist_profiles=False)
    assert synced[0].settled_holding_amount == 9100.38
    assert synced[0].holding_amount == 9100.38


def test_official_nav_rolls_settled_from_cached_return_without_unit_nav(monkeypatch):
    """fast 路径无单位净值缓存时，仍可用昨结算×(1+日涨跌%)滚入。"""
    _intraday_session(monkeypatch)
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_official_nav_return",
        lambda _code, _date: 1.4,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_cached_official_nav_return",
        lambda _code, _date: 1.4,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_latest_unit_nav",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.fund_nav_service.prime_official_nav_cache",
        lambda *_args, **_kwargs: {},
    )
    profile = FundProfile(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=13671.67,
        settled_holding_amount=13671.67,
        holding_shares=7390.09,
        holding_cost=1.85,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.list_fund_profiles",
        lambda: [profile],
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.fetch_fund_estimate_quotes",
        lambda *_args, **_kwargs: {},
    )

    holding = Holding(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=13671.67,
        settled_holding_amount=13671.67,
    )
    synced = sync_holding_amounts_from_shares(
        [holding],
        persist_profiles=False,
        allow_nav_fetch=False,
    )
    assert synced[0].settled_holding_amount == 13863.07
    assert synced[0].holding_amount == 13863.07


def test_official_nav_rolls_settled_forward_using_profile_cost_as_ground_truth(monkeypatch):
    """官方净值结算：settled 直接按官方日涨跌滚入，profit 用档案固定成本价重算。

    本用例原名 test_official_nav_rolls_from_inflated_cost_plus_profit_base，
    曾断言 settled 会被"识破"污染成本价+累计收益、回退到 cost×(1+累计%) 再滚入。
    但这个"识破"逻辑本身是数学恒等式：settled=cost×(1+return%) 正是「成本」
    「收益率」这两个概念的定义式，任何真实盈利、数据自洽的持仓都会满足这个式
    子——从数字本身完全无法区分"真实盈利"和"污染数据"，这两种情况数据长得
    一模一样。所以改为直接信任 settled 字面值，用官方日涨跌滚动；profit 则
    始终用 profile.holding_cost（固定成本价）与滚动后的新 settled 重算，
    保证「持有收益跟着结算金额变化」这个核心诉求正确生效。
    """
    _intraday_session(monkeypatch)
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_official_nav_return",
        lambda _code, _date: 1.4,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_cached_official_nav_return",
        lambda _code, _date: 1.4,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_latest_unit_nav",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.fund_nav_service.prime_official_nav_cache",
        lambda *_args, **_kwargs: {},
    )
    profile = FundProfile(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=14253.95,
        settled_holding_amount=14253.95,
        holding_shares=7390.09,
        holding_cost=1.85,
        holding_profit=582.28,
        holding_return_percent=4.26,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.list_fund_profiles",
        lambda: [profile],
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.fetch_fund_estimate_quotes",
        lambda *_args, **_kwargs: {},
    )

    holding = Holding(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=14253.95,
        settled_holding_amount=14253.95,
        holding_profit=582.28,
        holding_return_percent=4.26,
    )
    synced = sync_holding_amounts_from_shares(
        [holding],
        persist_profiles=False,
        allow_nav_fetch=False,
    )
    # settled 按官方日涨跌 1.4% 直接滚入：14253.95 × 1.014 = 14453.51。
    assert synced[0].settled_holding_amount == pytest.approx(14453.51, abs=0.01)
    assert synced[0].holding_amount == pytest.approx(14453.51, abs=0.01)
    # profit 用固定成本价重算：cost_total = 1.85×7390.09 = 13671.67；
    # profit = 14453.51 − 13671.67 = 781.84（不再是"保持 582.28 不变"）。
    assert synced[0].holding_profit == pytest.approx(781.84, abs=0.01)


def test_alipay_cost_profit_return_alignment(monkeypatch):
    """官方净值结算后，持有收益随结算金额同步变化（不再永久冻结在 OCR 上传值）。"""
    _intraday_session(monkeypatch)
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_official_nav_return",
        lambda _code, _date: 1.4,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_cached_official_nav_return",
        lambda _code, _date: 1.4,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_latest_unit_nav",
        lambda *_args, **_kwargs: 1.8759,
    )
    monkeypatch.setattr(
        "app.services.fund_nav_service.prime_official_nav_cache",
        lambda *_args, **_kwargs: {},
    )
    profile = FundProfile(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=13671.67,
        settled_holding_amount=13671.67,
        holding_shares=7390.09,
        holding_cost=1.85,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.list_fund_profiles",
        lambda: [profile],
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.fetch_fund_estimate_quotes",
        lambda *_args, **_kwargs: {},
    )

    holding = Holding(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=13671.67,
        settled_holding_amount=13671.67,
        holding_profit=552.10,
        holding_return_percent=4.15,
    )
    synced = sync_holding_amounts_from_shares(
        [holding],
        persist_profiles=False,
        allow_nav_fetch=False,
    )
    assert synced[0].holding_amount == 13863.07
    # 回归 bug 核心断言：金额涨了多少（13863.07-13671.67=191.40），
    # 收益就该跟着涨多少（新收益-旧收益(552.10) 应≈191.40），而不是原地不动。
    assert synced[0].holding_profit == pytest.approx(743.44, abs=0.1)
    assert synced[0].holding_profit - 552.10 == pytest.approx(191.4, abs=0.1)
    assert synced[0].holding_return_percent == pytest.approx(5.67, abs=0.05)


def test_ocr_official_nav_amount_not_rolled_again(monkeypatch):
    """支付宝 OCR 金额已是官方净值更新后总额，sync 不得再按日涨跌改写。"""
    _intraday_session(monkeypatch)
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_official_nav_return",
        lambda _code, _date: -1.06,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_cached_official_nav_return",
        lambda _code, _date: -1.06,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_latest_unit_nav",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.fund_nav_service.prime_official_nav_cache",
        lambda *_args, **_kwargs: {},
    )
    profile = FundProfile(
        fund_code="025856",
        fund_name="华夏中证电网设备主题ETF联接A",
        holding_amount=11104.30,
        settled_holding_amount=11104.30,
        holding_shares=7843.13,
        holding_cost=1.4165,
        # OCR 确认写入当日总额时，ocr_pipeline.apply_confirmed_holdings 会同步
        # 打上 profit_settled_trade_date（本用例对齐 _intraday_session 的交易日）。
        # 这是本次 bug 修复后的显式契约：sync 是否跳过结算只认这个持久化的
        # 日期标记，不再认 holding.amount_includes_today 这个会被下一天快照
        # 原样带入、永久为真的临时字段。
        profit_settled_trade_date="2026-06-26",
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.list_fund_profiles",
        lambda: [profile],
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.fetch_fund_estimate_quotes",
        lambda *_args, **_kwargs: {},
    )
    holding = Holding(
        fund_code="025856",
        fund_name="华夏中证电网设备主题ETF联接A",
        holding_amount=11104.30,
        settled_holding_amount=11104.30,
        holding_profit=142.18,
        holding_return_percent=1.30,
        amount_includes_today=True,
        daily_return_percent_source="official_nav",
    )
    synced = sync_holding_amounts_from_shares(
        [holding],
        persist_profiles=False,
        allow_nav_fetch=False,
    )
    assert synced[0].holding_amount == 11104.30
    assert synced[0].settled_holding_amount == 11104.30
    assert synced[0].holding_profit == pytest.approx(142.18, abs=0.5)
    assert synced[0].holding_return_percent == pytest.approx(1.30, abs=0.05)


def test_ocr_official_nav_amount_rolled_next_trading_day_despite_stale_amount_includes_today(
    monkeypatch,
):
    """回归 bug：即使 holding.amount_includes_today 从上次快照原样带入为 True，
    只要 profile.profit_settled_trade_date 不等于本交易日，下一交易日的官方
    净值结算必须照常滚入——不能被这个过期的临时字段挡住。"""
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_effective_trade_date",
        lambda: "2026-06-29",
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_official_nav_return",
        lambda _code, _date: 0.8,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_cached_official_nav_return",
        lambda _code, _date: 0.8,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_latest_unit_nav",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.fund_nav_service.prime_official_nav_cache",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.fetch_fund_estimate_quotes",
        lambda *_args, **_kwargs: {},
    )
    profile = FundProfile(
        fund_code="025856",
        fund_name="华夏中证电网设备主题ETF联接A",
        holding_amount=11104.30,
        settled_holding_amount=11104.30,
        holding_shares=7843.13,
        holding_cost=1.4165,
        # 上一交易日（2026-06-26）已结算过，本交易日（2026-06-29）尚未结算。
        profit_settled_trade_date="2026-06-26",
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.list_fund_profiles",
        lambda: [profile],
    )
    holding = Holding(
        fund_code="025856",
        fund_name="华夏中证电网设备主题ETF联接A",
        holding_amount=11104.30,
        settled_holding_amount=11104.30,
        holding_profit=142.18,
        holding_return_percent=1.30,
        # 上次快照持久化时写入的值，原样带入本交易日（无任何重置逻辑）。
        amount_includes_today=True,
        daily_return_percent_source="official_nav",
    )
    synced = sync_holding_amounts_from_shares(
        [holding],
        persist_profiles=False,
        allow_nav_fetch=False,
    )
    assert synced[0].settled_holding_amount != pytest.approx(11104.30, abs=0.5)
    assert synced[0].holding_profit != pytest.approx(142.18, abs=0.5)


def test_official_nav_settlement_updates_holding_profit_across_multiple_days(monkeypatch):
    """回归 bug：连续两次官方净值结算后，holding_profit 应逐次反映最新结算金额，
    而不是永久冻结在 OCR 上传时的初始值。

    根因：_ocr_holding_profit_is_cumulative 用「amount/profit/return% 是否自洽」
    判断是否为「刚从 OCR 读出的原始值」，但这是数学恒等式——系统自己结算写回的
    新三元组同样自洽，导致该判定从第一次结算起永久为真，profit 被冻死。
    """
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_latest_unit_nav",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.fetch_fund_estimate_quotes",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "app.services.fund_nav_service.prime_official_nav_cache",
        lambda *_args, **_kwargs: {},
    )
    profile = FundProfile(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=10000.0,
        settled_holding_amount=10000.0,
        holding_shares=1000.0,
        holding_cost=9.804,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.list_fund_profiles",
        lambda: [profile],
    )

    # day0：OCR 上传时刻的持有收益（196.0）。
    day0_holding = Holding(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=10000.0,
        settled_holding_amount=10000.0,
        holding_profit=196.0,
        holding_return_percent=2.0,
    )

    # day1：官方净值公布 +1.0%。
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_effective_trade_date",
        lambda: "2026-06-27",
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_official_nav_return",
        lambda _code, _date: 1.0,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_cached_official_nav_return",
        lambda _code, _date: 1.0,
    )
    day1_holding = sync_holding_amounts_from_shares(
        [day0_holding], persist_profiles=False, allow_nav_fetch=False
    )[0]

    # 核心回归断言：结算金额应该跟着官方净值涨跌变化（不是原地不动）。
    assert day1_holding.settled_holding_amount != pytest.approx(10000.0, abs=0.5)
    # 核心回归断言：持有收益不应还停在 OCR 上传时的 196.0。
    assert day1_holding.holding_profit != pytest.approx(196.0, abs=0.5)

    # day2：官方净值再公布 +1.5%（在 day1 结算结果基础上，模拟次日重新加载持仓）。
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_effective_trade_date",
        lambda: "2026-06-28",
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_official_nav_return",
        lambda _code, _date: 1.5,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_cached_official_nav_return",
        lambda _code, _date: 1.5,
    )
    day2_holding = sync_holding_amounts_from_shares(
        [day1_holding], persist_profiles=False, allow_nav_fetch=False
    )[0]

    # 第二次结算的金额/收益应该与第一次不同；若被冻结，会与 day1 完全相等。
    assert day2_holding.settled_holding_amount != pytest.approx(
        day1_holding.settled_holding_amount, abs=0.5
    )
    assert day2_holding.holding_profit != day1_holding.holding_profit


def test_amount_includes_today_true_from_snapshot_does_not_freeze_settlement(monkeypatch):
    """回归 bug：amount_includes_today=True 一旦从上次持久化的快照原样带入，
    不应导致此后交易日的官方净值结算被永久跳过。

    根因：OCR 确认写入快照时该字段被设为 True 且没有重置逻辑；
    _should_skip_official_nav_roll 只要看到它为 True 就直接跳过整只基金结算。
    """
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_effective_trade_date",
        lambda: "2026-06-27",
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_official_nav_return",
        lambda _code, _date: 2.0,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_cached_official_nav_return",
        lambda _code, _date: 2.0,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_latest_unit_nav",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.fetch_fund_estimate_quotes",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "app.services.fund_nav_service.prime_official_nav_cache",
        lambda *_args, **_kwargs: {},
    )
    profile = FundProfile(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=10000.0,
        settled_holding_amount=10000.0,
        holding_shares=1000.0,
        holding_cost=9.804,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.list_fund_profiles",
        lambda: [profile],
    )

    # 模拟：上一次 OCR 确认写入快照时 amount_includes_today 被设为 True，
    # 快照持久化后次日重新加载 Holding 时原样带入（无任何重置逻辑）。
    holding = Holding(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=10000.0,
        settled_holding_amount=10000.0,
        amount_includes_today=True,
    )
    synced = sync_holding_amounts_from_shares(
        [holding], persist_profiles=False, allow_nav_fetch=False
    )[0]

    # 新交易日官方净值 +2.0% 已公布，结算金额应该反映它，不应永久停在昨天的 10000。
    assert synced.settled_holding_amount != pytest.approx(10000.0, abs=0.01)


def test_batch_paths_load_profiles_once_without_point_queries(monkeypatch):
    profiles = [
        FundProfile(
            fund_code="001111",
            fund_name="基金一",
            holding_shares=100.0,
            settled_holding_amount=100.0,
        ),
        FundProfile(
            fund_code="002222",
            fund_name="基金二",
            holding_shares=200.0,
            settled_holding_amount=200.0,
        ),
    ]
    list_calls = 0

    def list_profiles():
        nonlocal list_calls
        list_calls += 1
        return profiles

    monkeypatch.setattr(
        "app.services.holding_amount_sync.list_fund_profiles",
        list_profiles,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_fund_profile_by_code",
        lambda _code: (_ for _ in ()).throw(AssertionError("batch path used point query")),
    )
    holdings = [
        Holding(fund_code="001111", fund_name="基金一", holding_amount=100.0),
        Holding(fund_code="002222", fund_name="基金二", holding_amount=200.0),
    ]

    bootstrap_holding_baselines(
        holdings,
        estimate_quotes={},
        persist_profiles=False,
        skip_network=True,
    )
    assert list_calls == 1

    monkeypatch.setattr(
        "app.services.fund_nav_service.prime_official_nav_cache",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_effective_trade_date",
        lambda: "2026-07-13",
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_cached_official_nav_return",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_latest_unit_nav",
        lambda *_args, **_kwargs: None,
    )
    sync_holding_amounts_from_shares(
        holdings,
        estimate_quotes={},
        persist_profiles=False,
        allow_nav_fetch=False,
    )
    assert list_calls == 2


def test_bootstrap_batch_reuses_saved_profile_for_duplicate_code(monkeypatch):
    profile = FundProfile(
        fund_code="001111",
        fund_name="原名称",
        holding_shares=100.0,
    )
    saved: list[FundProfile] = []

    monkeypatch.setattr(
        "app.services.holding_amount_sync.list_fund_profiles",
        lambda: [profile],
    )

    def save_profile(current: FundProfile) -> FundProfile:
        saved.append(current)
        if len(saved) == 1:
            return current.model_copy(update={"fund_name": "已回写名称"})
        return current

    monkeypatch.setattr(
        "app.services.holding_amount_sync.save_fund_profile",
        save_profile,
    )
    holdings = [
        Holding(
            fund_code="001111",
            fund_name="基金一",
            holding_amount=100.0,
            holding_profit=10.0,
        ),
        Holding(fund_code="001111", fund_name="基金一", holding_amount=200.0),
    ]

    bootstrap_holding_baselines(
        holdings,
        estimate_quotes={},
        skip_network=True,
    )

    assert len(saved) == 2
    assert saved[1].fund_name == "已回写名称"
    assert saved[1].holding_profit == 10.0


def test_sync_batch_reuses_saved_profile_for_duplicate_code(monkeypatch):
    profile = FundProfile(
        fund_code="001111",
        fund_name="原名称",
        holding_shares=100.0,
        settled_holding_amount=100.0,
    )
    saved: list[FundProfile] = []

    monkeypatch.setattr(
        "app.services.holding_amount_sync.list_fund_profiles",
        lambda: [profile],
    )
    monkeypatch.setattr(
        "app.services.fund_nav_service.prime_official_nav_cache",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_effective_trade_date",
        lambda: "2026-07-13",
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_cached_official_nav_return",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_latest_unit_nav",
        lambda *_args, **_kwargs: None,
    )

    def save_profile(current: FundProfile) -> FundProfile:
        saved.append(current)
        if len(saved) == 1:
            return current.model_copy(update={"fund_name": "已回写名称"})
        return current

    monkeypatch.setattr(
        "app.services.holding_amount_sync.save_fund_profile",
        save_profile,
    )
    holdings = [
        Holding(
            fund_code="001111",
            fund_name="基金一",
            holding_amount=100.0,
            settled_holding_amount=100.0,
        ),
        Holding(
            fund_code="001111",
            fund_name="基金一",
            holding_amount=100.0,
            settled_holding_amount=100.0,
        ),
    ]

    sync_holding_amounts_from_shares(
        holdings,
        estimate_quotes={},
        allow_nav_fetch=False,
    )

    assert len(saved) == 2
    assert saved[1].fund_name == "已回写名称"


def test_empty_batch_paths_do_not_load_profiles(monkeypatch):
    monkeypatch.setattr(
        "app.services.holding_amount_sync.list_fund_profiles",
        lambda: (_ for _ in ()).throw(AssertionError("empty batch queried profiles")),
    )

    assert bootstrap_holding_baselines([]) == []
    assert sync_holding_amounts_from_shares([]) == []

    placeholder = Holding(
        fund_code="000000",
        fund_name="待匹配基金",
        holding_amount=0.0,
    )
    assert bootstrap_holding_baselines(
        [placeholder],
        estimate_quotes={},
        skip_network=True,
    )
    assert sync_holding_amounts_from_shares(
        [placeholder],
        estimate_quotes={},
        allow_nav_fetch=False,
    ) == [placeholder]


def test_resolve_display_settled_amount_keeps_single_point_lookup(monkeypatch):
    profile = FundProfile(
        fund_code="001111",
        fund_name="基金一",
        settled_holding_amount=88.0,
    )
    point_queries: list[str] = []
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_fund_profile_by_code",
        lambda code: point_queries.append(code) or profile,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.list_fund_profiles",
        lambda: (_ for _ in ()).throw(AssertionError("single path used batch query")),
    )
    holding = Holding(fund_code="001111", fund_name="基金一", holding_amount=100.0)

    assert resolve_display_settled_amount(holding) == 88.0
    assert point_queries == ["001111"]
