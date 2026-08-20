"""份额锁定必须用与同步金额同一净值日期的单位净值（2026-08 支付宝金额漂移根因回归）。

生产事故还原（011373 招商前沿医疗保健股票A，数字取自真实数据）：
- 08-17 净值 0.8540，08-18 净值 0.8596（+0.66%），08-19 净值 0.8289，08-20 净值 0.8674；
- 用户 08-19 白天全量同步，支付宝显示 08-18 结算额 4596.26；
- 旧代码用「最近一条缓存净值」（还是 08-17 的 0.8540）锁份额 → 5382.04，
  比真实份额 5346.98 多 0.66%，此后每天 份额×净值 都比支付宝高 0.66%，
  且再次全量同步修正的金额会被下一次结算重新拉回错误轨道。
"""
from __future__ import annotations

import pytest

from app.models import FundProfile, Holding
from app.services import holding_amount_sync, transaction_ledger
from app.services.holding_amount_sync import (
    _amount_settle_date,
    _bootstrap_profile_baseline,
    _sync_one_holding,
)

CODE = "011373"


def _holding(**overrides) -> Holding:
    payload = {
        "fund_code": CODE,
        "fund_name": "招商前沿医疗保健股票A",
        "holding_amount": 4596.26,
        "settled_holding_amount": 4596.26,
        "holding_profit": 96.26,
        "holding_return_percent": 2.14,
        "return_percent": 2.14,
    }
    payload.update(overrides)
    return Holding.model_validate(payload)


def _profile(**overrides) -> FundProfile:
    payload = {
        "fund_code": CODE,
        "fund_name": "招商前沿医疗保健股票A",
        "holding_amount": 4596.26,
        "settled_holding_amount": 4596.26,
        "holding_shares": 5382.04,  # 旧 bug 锁出来的错误份额
        "shares_baseline_date": "2026-08-19",
    }
    payload.update(overrides)
    return FundProfile.model_validate(payload)


def _capture_saves(monkeypatch: pytest.MonkeyPatch) -> list[FundProfile]:
    saved: list[FundProfile] = []
    monkeypatch.setattr(
        holding_amount_sync,
        "save_fund_profile",
        lambda profile: saved.append(profile) or profile,
    )
    return saved


class TestAmountSettleDate:
    def test_intraday_amount_is_previous_trade_day_settlement(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            holding_amount_sync,
            "get_previous_trade_date",
            lambda _effective: "2026-08-18",
        )
        session = {"session_kind": "trading_day_intraday", "effective_trade_date": "2026-08-19"}
        assert _amount_settle_date(_holding(), session=session) == "2026-08-18"

    def test_official_daily_marker_means_amount_includes_effective_day(self) -> None:
        session = {"session_kind": "trading_day_after_close", "effective_trade_date": "2026-08-19"}
        updated = _holding(
            daily_profit=-164.09,
            daily_return_percent=-3.57,
            daily_return_percent_source="official_nav",
        )
        assert _amount_settle_date(updated, session=session) == "2026-08-19"

    def test_non_trading_day_amount_matches_effective_date(self) -> None:
        session = {"session_kind": "non_trading_day", "effective_trade_date": "2026-08-19"}
        assert _amount_settle_date(_holding(), session=session) == "2026-08-19"


class TestBootstrapShareLock:
    def test_lock_uses_settle_date_nav_not_stale_latest(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """生产事故还原：金额是 08-18 结算值，必须除以 08-18 净值，而不是缓存里更旧的净值。"""
        saved = _capture_saves(monkeypatch)
        monkeypatch.setattr(
            holding_amount_sync,
            "get_unit_nav_on_date",
            lambda _code, date, **_kwargs: 0.8596 if date == "2026-08-18" else None,
        )

        _bootstrap_profile_baseline(
            _holding(),
            profile=_profile(),
            estimate_quote=None,
            persist_profile=True,
            force_reset_shares=True,
            skip_network=True,
            settle_date="2026-08-18",
        )

        assert saved, "profile 应当被写回"
        locked = saved[-1]
        assert locked.holding_shares == pytest.approx(5346.98)  # 4596.26 / 0.8596
        assert locked.holding_shares != pytest.approx(5382.04)  # 旧 bug：4596.26 / 0.8540
        assert locked.shares_baseline_date == "2026-08-18"
        assert locked.settled_amount_trade_date == "2026-08-18"
        assert locked.profit_settled_trade_date == "2026-08-18"
        # 支付宝口径成本价 = (金额 − 持有收益) / 份额 = 4500.00 / 5346.98
        assert locked.holding_cost == pytest.approx(0.8416, abs=0.0002)

    def test_lock_deferred_when_dated_nav_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """D 日净值不可得时宁可不锁份额，也不能用错日期的净值把误差锁死。"""
        saved = _capture_saves(monkeypatch)
        monkeypatch.setattr(
            holding_amount_sync,
            "get_unit_nav_on_date",
            lambda *_args, **_kwargs: None,
        )

        _bootstrap_profile_baseline(
            _holding(),
            profile=_profile(),
            estimate_quote=None,
            persist_profile=True,
            force_reset_shares=True,
            skip_network=True,
            settle_date="2026-08-18",
        )

        deferred = saved[-1]
        assert deferred.holding_shares is None
        assert deferred.settled_amount_trade_date == "2026-08-18"
        assert deferred.settled_holding_amount == 4596.26

    def test_ride_along_row_keeps_profile_settle_date(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """部分截图同步：金额未变的搭车行保留档案净值日期，不得改标成今天。

        搭车行带着上次滚动留下的 amount_includes_today/官方日收益旧信号，若按
        这些信号把 T-1 的金额标成 T，当晚官方净值结算会被误跳过一天。
        """
        saved = _capture_saves(monkeypatch)
        profile = _profile(
            holding_shares=5346.98,
            holding_amount=4432.11,
            settled_holding_amount=4432.11,
            settled_amount_trade_date="2026-08-19",
        )
        ride_along = _holding(
            holding_amount=4432.11,
            settled_holding_amount=4432.11,
            daily_profit=-158.23,
            daily_return_percent=-3.57,
            daily_return_percent_source="official_nav",
            amount_includes_today=True,  # 上次滚动留下的旧信号
        )

        _bootstrap_profile_baseline(
            ride_along,
            profile=profile,
            estimate_quote=None,
            persist_profile=True,
            force_reset_shares=False,
            skip_network=True,
            settle_date="2026-08-20",  # 按旧信号误判出的"今天"
        )

        kept = saved[-1]
        assert kept.settled_amount_trade_date == "2026-08-19"
        assert kept.profit_settled_trade_date == "2026-08-19"

    def test_undated_estimate_quote_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        saved = _capture_saves(monkeypatch)
        monkeypatch.setattr(
            holding_amount_sync,
            "get_unit_nav_on_date",
            lambda *_args, **_kwargs: None,
        )

        _bootstrap_profile_baseline(
            _holding(),
            profile=_profile(),
            estimate_quote={"previous_nav": 0.8540, "nav_date": "2026-08-17"},
            persist_profile=True,
            force_reset_shares=True,
            skip_network=True,
            settle_date="2026-08-18",
        )

        assert saved[-1].holding_shares is None  # 日期对不上的兜底净值不可用


class TestDeferredLockThenSettle:
    def test_lazy_lock_uses_recorded_settle_date_then_rolls_to_alipay_amount(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """延迟锁定 → 官方净值公布后结算额与支付宝逐分吻合。"""
        saved = _capture_saves(monkeypatch)
        profile = _profile(
            holding_shares=None,
            settled_amount_trade_date="2026-08-18",
            profit_settled_trade_date="2026-08-18",
        )
        navs = {"2026-08-18": 0.8596, "2026-08-19": 0.8289}
        monkeypatch.setattr(
            holding_amount_sync,
            "get_unit_nav_on_date",
            lambda _code, date, **_kwargs: navs.get(date),
        )
        monkeypatch.setattr(
            holding_amount_sync,
            "get_previous_trade_date",
            lambda _date: "2026-08-18",
        )
        monkeypatch.setattr(
            holding_amount_sync,
            "get_official_nav_return",
            lambda *_args, **_kwargs: -3.57,
        )

        holding, latest_profile = _sync_one_holding(
            _holding(),
            profile=profile,
            trade_date="2026-08-19",
            estimate_quote=None,
            persist_profile=True,
        )

        # 补锁：份额 = 08-18 金额 ÷ 08-18 净值
        assert any(p.holding_shares == pytest.approx(5346.98) for p in saved)
        assert latest_profile is not None
        assert latest_profile.shares_baseline_date == "2026-08-18"
        # 官方净值公布后：5346.98 × 0.8289 = 4432.11，与支付宝 08-19 结算额一致
        assert holding.settled_holding_amount == pytest.approx(4432.11)
        assert holding.amount_includes_today is True


class TestOfficialNavRoll:
    def _roll(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        profile: FundProfile,
        holding: Holding,
        shares_override: dict[str, float] | None = None,
    ) -> tuple[Holding, list[FundProfile]]:
        saved = _capture_saves(monkeypatch)
        monkeypatch.setattr(
            holding_amount_sync,
            "get_official_nav_return",
            lambda *_args, **_kwargs: 4.64,
        )
        monkeypatch.setattr(
            holding_amount_sync,
            "get_unit_nav_on_date",
            lambda _code, date, **_kwargs: 0.8674 if date == "2026-08-20" else None,
        )
        monkeypatch.setattr(
            holding_amount_sync,
            "get_previous_trade_date",
            lambda _date: "2026-08-19",
        )
        rolled, _ = _sync_one_holding(
            holding,
            profile=profile,
            trade_date="2026-08-20",
            estimate_quote=None,
            persist_profile=True,
            shares_override=shares_override,
        )
        return rolled, saved

    def test_roll_multiplies_once_and_refreshes_holding_profit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """结算 = 份额×当日净值，且持有收益按固定成本重算（支付宝口径）。"""
        profile = _profile(
            holding_shares=5346.98,
            holding_amount=4432.11,
            settled_holding_amount=4432.11,
            holding_cost=0.8416,
            settled_amount_trade_date="2026-08-19",
            profit_settled_trade_date="2026-08-14",  # 生产数据：收益长期未推进
        )
        holding = _holding(holding_amount=4432.11, settled_holding_amount=4432.11)

        # shares_override 与档案份额一致（没有加减仓）→ 必须走净值滚动分支，
        # 而不是旧 bug 里绕过收益结算的快速重算分支。
        rolled, saved = self._roll(
            monkeypatch,
            profile=profile,
            holding=holding,
            shares_override={CODE: 5346.98},
        )

        assert rolled.settled_holding_amount == pytest.approx(4637.97)  # 支付宝 08-20 金额
        assert rolled.amount_includes_today is True
        # 持有收益 = 4637.97 − 0.8416×5346.98 ≈ +137.95（支付宝显示 +137.97）
        assert rolled.holding_profit == pytest.approx(137.95, abs=0.05)
        assert rolled.holding_return_percent == pytest.approx(3.07, abs=0.01)
        assert saved[-1].profit_settled_trade_date == "2026-08-20"
        assert saved[-1].settled_amount_trade_date == "2026-08-20"

    def test_same_day_roll_is_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        profile = _profile(
            holding_shares=5346.98,
            holding_amount=4637.97,
            settled_holding_amount=4637.97,
            holding_cost=0.8416,
            settled_amount_trade_date="2026-08-20",
            profit_settled_trade_date="2026-08-20",
        )
        holding = _holding(
            holding_amount=4637.97,
            settled_holding_amount=4637.97,
            holding_profit=137.95,
        )

        rolled, _ = self._roll(monkeypatch, profile=profile, holding=holding)

        assert rolled.settled_holding_amount == pytest.approx(4637.97)
        assert rolled.holding_profit == pytest.approx(137.95)

    def test_stale_baseline_never_single_day_rolls(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """基线金额停在多天前时，缺当日净值就等待，不能只乘一天涨跌。"""
        saved = _capture_saves(monkeypatch)
        profile = _profile(
            holding_shares=None,
            holding_amount=4300.00,
            settled_holding_amount=4300.00,
            settled_amount_trade_date="2026-08-14",
        )
        holding = _holding(holding_amount=4300.00, settled_holding_amount=4300.00)
        monkeypatch.setattr(
            holding_amount_sync,
            "get_official_nav_return",
            lambda *_args, **_kwargs: 4.64,
        )
        monkeypatch.setattr(
            holding_amount_sync,
            "get_unit_nav_on_date",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(
            holding_amount_sync,
            "get_previous_trade_date",
            lambda _date: "2026-08-19",
        )

        rolled, _ = _sync_one_holding(
            holding,
            profile=profile,
            trade_date="2026-08-20",
            estimate_quote=None,
            persist_profile=True,
        )

        assert rolled.settled_holding_amount == pytest.approx(4300.00)
        assert all(p.settled_holding_amount == pytest.approx(4300.00) for p in saved)


class TestLedgerSeedGuard:
    def test_pending_lock_profile_is_not_seeded_from_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """待补锁档案（金额已同步、份额未锁）不得从 0 份额叠加历史流水。"""
        from app.models import FundTransaction

        pending_profile = _profile(
            holding_shares=None,
            settled_amount_trade_date="2026-08-18",
            shares_baseline_date="2026-08-18",
        )
        tx = FundTransaction(
            id="tx-1",
            fund_code=CODE,
            fund_name="招商前沿医疗保健股票A",
            direction="buy",
            amount_yuan=500.0,
            trade_time="2026-08-19 10:00:00",
            confirm_date="2026-08-19",
            status="confirmed",
            shares_delta=600.0,
            dedup_key="k1",
            created_at="2026-08-19T02:00:00+00:00",
        )
        monkeypatch.setattr(
            transaction_ledger,
            "list_fund_transactions",
            lambda **_kwargs: [tx],
        )

        effective = transaction_ledger.compute_effective_shares_map(
            [CODE],
            profiles_by_code={CODE: pending_profile},
        )

        assert CODE not in effective
