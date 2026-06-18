"""美股交易时段检测属性测试。

测试 `app.services.us_market_session.detect_us_session`。

本仓库未引入 `hypothesis`（见 `requirements.txt`），故按任务约定以
「参数化 + 多随机样本（≥100 次迭代，含跨 DST 边界与周末）」近似属性测试。
"""

from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.services.us_market_session import (
    AFTER_HOURS_CLOSE,
    PRE_MARKET_OPEN,
    REGULAR_CLOSE,
    REGULAR_OPEN,
    detect_us_session,
)

US_TZ = ZoneInfo("America/New_York")

VALID_KINDS = {"pre_market", "regular", "after_hours", "closed"}

# 属性测试迭代次数（≥100）
_ITERATIONS = 500


def _expected_kind(moment: datetime) -> str:
    """独立（不复用被测实现逻辑）地按规则推导期望时段。

    交易日（周一至周五，ET 墙钟日期）：
        09:30–16:00 ET → regular
        04:00–09:30 ET → pre_market
        16:00–20:00 ET → after_hours
        其余 → closed
    非交易日（周末）→ closed
    """
    et = moment.astimezone(US_TZ)
    if et.weekday() >= 5:  # 周六/周日
        return "closed"
    t = et.time()
    if REGULAR_OPEN <= t < REGULAR_CLOSE:
        return "regular"
    if PRE_MARKET_OPEN <= t < REGULAR_OPEN:
        return "pre_market"
    if REGULAR_CLOSE <= t < AFTER_HOURS_CLOSE:
        return "after_hours"
    return "closed"


def _random_et_instants(n: int, seed: int = 20260617) -> list[datetime]:
    """生成跨多年、覆盖 DST 边界与周末的随机美东时刻。"""
    rng = random.Random(seed)
    base = datetime(2024, 1, 1, tzinfo=US_TZ)
    instants: list[datetime] = []
    for _ in range(n):
        # 覆盖 ~3 年范围，秒级粒度
        offset = timedelta(seconds=rng.randint(0, 3 * 365 * 24 * 3600))
        instants.append(base + offset)
    return instants


def _dst_boundary_instants() -> list[datetime]:
    """围绕 2024/2025 春进、秋退切换日的密集采样（每 15 分钟一格，覆盖全天）。"""
    boundary_dates = [
        # 春进（DST 开始，第二个周日 02:00→03:00）
        datetime(2024, 3, 10, tzinfo=US_TZ).date(),
        datetime(2025, 3, 9, tzinfo=US_TZ).date(),
        # 秋退（DST 结束，第一个周日 02:00→01:00）
        datetime(2024, 11, 3, tzinfo=US_TZ).date(),
        datetime(2025, 11, 2, tzinfo=US_TZ).date(),
    ]
    instants: list[datetime] = []
    for d in boundary_dates:
        for minutes in range(0, 24 * 60, 15):
            instants.append(
                datetime(d.year, d.month, d.day, tzinfo=US_TZ)
                + timedelta(minutes=minutes)
            )
    return instants


# ---------------------------------------------------------------------------
# Property 1：时段划分完备且互斥
# Feature: us-market-overview, Property 1
# Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5
# ---------------------------------------------------------------------------


def test_property1_session_partition_complete_and_exclusive_random():
    """Feature: us-market-overview, Property 1

    For any 美东时刻：detect_us_session 恰返回 pre_market/regular/after_hours/closed
    之一（完备且互斥），且与时间窗口规则一致。

    Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5
    """
    instants = _random_et_instants(_ITERATIONS) + _dst_boundary_instants()
    assert len(instants) >= 100

    for moment in instants:
        result = detect_us_session(moment)
        kind = result["session_kind"]

        # 完备且互斥：恰为四类之一
        assert kind in VALID_KINDS, f"非法时段 {kind!r} @ {moment.isoformat()}"

        # 与窗口规则一致（独立推导）
        expected = _expected_kind(moment)
        assert kind == expected, (
            f"时段不一致 @ {moment.isoformat()}: got {kind!r}, expected {expected!r}"
        )


def test_property1_label_and_date_consistency_random():
    """Feature: us-market-overview, Property 1

    返回结构始终携带与 session_kind 一致的中文标签与 ET 日期。

    Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5
    """
    label_by_kind = {
        "pre_market": "盘前交易中",
        "regular": "盘中",
        "after_hours": "盘后",
        "closed": "休市",
    }
    for moment in _random_et_instants(_ITERATIONS, seed=42):
        result = detect_us_session(moment)
        kind = result["session_kind"]
        assert result["session_label"] == label_by_kind[kind]
        assert result["et_date"] == moment.astimezone(US_TZ).date().isoformat()


def test_property1_exhaustive_minute_grid_on_trading_day():
    """Feature: us-market-overview, Property 1

    在一个交易日（2026-06-17 周三）上按分钟穷举全天，验证四类时段恰好
    覆盖一天的不同分钟且互斥（完备 + 互斥的强校验）。

    Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5
    """
    day = datetime(2026, 6, 17, tzinfo=US_TZ)
    seen: set[str] = set()
    for minutes in range(0, 24 * 60):
        moment = day + timedelta(minutes=minutes)
        kind = detect_us_session(moment)["session_kind"]
        assert kind in VALID_KINDS
        assert kind == _expected_kind(moment)
        seen.add(kind)
    # 一个普通交易日应当覆盖全部四类时段
    assert seen == VALID_KINDS


# ---------------------------------------------------------------------------
# Property 2：夏令时墙钟一致性
# Feature: us-market-overview, Property 2
# Validates: Requirements 3.1
# ---------------------------------------------------------------------------


def _weekday_in_month(year: int, month: int, day_of_month: int) -> date:
    """返回给定年月内 day_of_month 当天日期；若落在周末则前移到最近的工作日。

    用于把同一「时刻（时:分:秒）」分别放到夏令时月份（如 7 月）与标准时月份
    （如 1 月）的某个工作日，从而比较跨 DST/标准时的相同墙钟时间。
    """
    d = date(year, month, day_of_month)
    # 若为周末，回退至最近的周五（保证落在交易日，排除「周末恒 closed」的干扰）
    while d.weekday() >= 5:
        d = d - timedelta(days=1)
    return d


def test_property2_same_wallclock_same_session_kind_across_dst_random():
    """Feature: us-market-overview, Property 2

    对相同的 ET 墙钟时间（时:分:秒），分别构造在夏令时（7 月，DST 生效）与
    标准时（1 月，标准时）的某个工作日，detect_us_session 必须返回相同的
    `session_kind`。

    校验时段判定基于 DST 感知的墙钟时间而非固定 UTC 偏移：夏令时与标准时下
    相同墙钟时间应得到一致的时段。

    Validates: Requirements 3.1
    """
    rng = random.Random(20260618)
    summer_day = _weekday_in_month(2025, 7, 15)  # DST 生效（夏令时）
    winter_day = _weekday_in_month(2025, 1, 15)  # 标准时

    # 抽样确认：构造日确实分处 DST 与标准时（utcoffset 不同）
    summer_probe = datetime.combine(summer_day, time(12, 0), tzinfo=US_TZ)
    winter_probe = datetime.combine(winter_day, time(12, 0), tzinfo=US_TZ)
    assert summer_probe.utcoffset() != winter_probe.utcoffset(), (
        "构造的夏季/冬季日期未能跨越 DST 边界，测试前提不成立"
    )

    for _ in range(_ITERATIONS):
        h = rng.randint(0, 23)
        m = rng.randint(0, 59)
        s = rng.randint(0, 59)
        wall = time(h, m, s)

        summer_moment = datetime.combine(summer_day, wall, tzinfo=US_TZ)
        winter_moment = datetime.combine(winter_day, wall, tzinfo=US_TZ)

        summer_kind = detect_us_session(summer_moment)["session_kind"]
        winter_kind = detect_us_session(winter_moment)["session_kind"]

        assert summer_kind == winter_kind, (
            f"相同墙钟 {wall.isoformat()} 跨 DST/标准时时段不一致："
            f"DST→{summer_kind!r}, 标准时→{winter_kind!r}"
        )


def test_property2_wallclock_grid_across_dst_consistent():
    """Feature: us-market-overview, Property 2

    在全天分钟网格（每 5 分钟一格）上穷举：相同 ET 墙钟时间在夏令时与标准时
    工作日上的 `session_kind` 必须一致。这是上面随机采样的强化（确定性覆盖
    全部边界时刻）。

    Validates: Requirements 3.1
    """
    summer_day = _weekday_in_month(2025, 7, 16)
    winter_day = _weekday_in_month(2025, 1, 16)

    checked = 0
    for minutes in range(0, 24 * 60, 5):
        wall_dt = datetime(2000, 1, 1) + timedelta(minutes=minutes)
        wall = wall_dt.time()

        summer_kind = detect_us_session(
            datetime.combine(summer_day, wall, tzinfo=US_TZ)
        )["session_kind"]
        winter_kind = detect_us_session(
            datetime.combine(winter_day, wall, tzinfo=US_TZ)
        )["session_kind"]

        assert summer_kind == winter_kind, (
            f"相同墙钟 {wall.isoformat()} 跨 DST/标准时时段不一致："
            f"DST→{summer_kind!r}, 标准时→{winter_kind!r}"
        )
        checked += 1

    assert checked >= 100


# ---------------------------------------------------------------------------
# Property 3：非交易日（周末）恒为 closed
# Feature: us-market-overview, Property 3
# Validates: Requirements 3.5
# ---------------------------------------------------------------------------


def _random_weekend_et_instants(n: int, seed: int = 20260619) -> list[datetime]:
    """生成跨多年、覆盖一天各时刻的随机「周末」美东时刻（周六/周日）。

    任取一个 ET 墙钟瞬时，若其落在周六或周日则保留；否则平移到同周的周六，
    从而保证样本均为周末时刻，同时覆盖全天各时刻（含盘前/盘中/盘后时间窗）。
    """
    rng = random.Random(seed)
    base = datetime(2024, 1, 1, tzinfo=US_TZ)
    instants: list[datetime] = []
    while len(instants) < n:
        # 覆盖 ~3 年范围，秒级粒度
        offset = timedelta(seconds=rng.randint(0, 3 * 365 * 24 * 3600))
        moment = (base + offset).astimezone(US_TZ)
        weekday = moment.weekday()  # 周一=0 … 周六=5、周日=6
        if weekday < 5:
            # 平移到本周周六（保持时:分:秒不变）
            moment = moment + timedelta(days=(5 - weekday))
        instants.append(moment)
    return instants


def test_property3_weekend_always_closed_random():
    """Feature: us-market-overview, Property 3

    任意落在周六或周日（ET 墙钟）的时刻，无论当日处于盘前/盘中/盘后的时间窗，
    detect_us_session 必须恒返回 session_kind == "closed"。

    Validates: Requirements 3.5
    """
    instants = _random_weekend_et_instants(_ITERATIONS)
    assert len(instants) >= 100

    for moment in instants:
        et = moment.astimezone(US_TZ)
        assert et.weekday() >= 5, (
            f"样本未落在周末 @ {moment.isoformat()} (weekday={et.weekday()})"
        )
        result = detect_us_session(moment)
        assert result["session_kind"] == "closed", (
            f"周末时刻应为 closed @ {moment.isoformat()}: "
            f"got {result['session_kind']!r}"
        )
        assert result["session_label"] == "休市"


def test_property3_weekend_closed_even_in_trading_time_windows():
    """Feature: us-market-overview, Property 3

    强校验：在周末上穷举本应属于交易日各时段（盘前 04:00、盘中 10:00、
    盘后 17:00 等）的墙钟时刻，仍恒为 closed —— 即「非交易日」凌驾于
    时间窗判定之上。

    Validates: Requirements 3.5
    """
    # 选取已知的周六与周日（2026-06-20 周六、2026-06-21 周日）
    weekend_days = [date(2026, 6, 20), date(2026, 6, 21)]
    # 覆盖盘前/盘中/盘后/夜间等典型时刻
    probe_times = [
        time(0, 0),
        time(4, 0),    # 盘前窗口起点
        time(9, 30),   # 盘中窗口起点
        time(12, 0),
        time(15, 59),
        time(16, 0),   # 盘后窗口起点
        time(19, 59),
        time(20, 0),
        time(23, 59),
    ]
    for d in weekend_days:
        assert d.weekday() >= 5, f"{d} 不是周末"
        for wall in probe_times:
            moment = datetime.combine(d, wall, tzinfo=US_TZ)
            result = detect_us_session(moment)
            assert result["session_kind"] == "closed", (
                f"周末交易时间窗仍应 closed @ {moment.isoformat()}: "
                f"got {result['session_kind']!r}"
            )


def test_property3_weekend_minute_grid_all_closed():
    """Feature: us-market-overview, Property 3

    在一个完整周末（周六+周日）上按分钟穷举全天（≥100 次迭代），所有时刻
    必须为 closed，且不会出现任何其他时段类别。

    Validates: Requirements 3.5
    """
    weekend_days = [date(2025, 11, 1), date(2025, 11, 2)]  # 周六、周日
    checked = 0
    for d in weekend_days:
        assert d.weekday() >= 5
        for minutes in range(0, 24 * 60, 10):
            moment = datetime(d.year, d.month, d.day, tzinfo=US_TZ) + timedelta(
                minutes=minutes
            )
            kind = detect_us_session(moment)["session_kind"]
            assert kind == "closed", (
                f"周末分钟网格应恒为 closed @ {moment.isoformat()}: got {kind!r}"
            )
            checked += 1
    assert checked >= 100

# ---------------------------------------------------------------------------
# 单元测试：时段边界（精确到秒）
# Feature: us-market-overview, Task 2.5
# Validates: Requirements 9.1
# ---------------------------------------------------------------------------

# 选用一个已知交易日（2026-06-17 周三），排除「周末恒 closed」的干扰。
_BOUNDARY_DAY = date(2026, 6, 17)


def test_unit_boundary_session_kinds_on_trading_day():
    """Feature: us-market-overview, Task 2.5

    在已知交易日（2026-06-17 周三）上逐秒校验时段窗口的开闭边界：

        09:29:59 → pre_market   （盘前窗口末秒，尚未开盘）
        09:30:00 → regular      （盘中窗口起点，闭区间下界）
        15:59:59 → regular      （盘中窗口末秒）
        16:00:00 → after_hours  （盘后窗口起点）
        19:59:59 → after_hours  （盘后窗口末秒）
        20:00:00 → closed       （盘后窗口上界为开区间，已休市）

    Validates: Requirements 9.1
    """
    assert _BOUNDARY_DAY.weekday() < 5, "基准日必须是交易日（周一至周五）"

    cases = [
        (time(9, 29, 59), "pre_market"),
        (time(9, 30, 0), "regular"),
        (time(15, 59, 59), "regular"),
        (time(16, 0, 0), "after_hours"),
        (time(19, 59, 59), "after_hours"),
        (time(20, 0, 0), "closed"),
    ]

    for wall, expected in cases:
        moment = datetime.combine(_BOUNDARY_DAY, wall, tzinfo=US_TZ)
        result = detect_us_session(moment)
        assert result["session_kind"] == expected, (
            f"边界判定错误 @ {wall.isoformat()}: "
            f"got {result['session_kind']!r}, expected {expected!r}"
        )


# ---------------------------------------------------------------------------
# 单元测试：DST 切换日（春进 / 秋退）
# Feature: us-market-overview, Task 2.5
# Validates: Requirements 9.1
# ---------------------------------------------------------------------------


def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    """返回某年某月「第 n 个 weekday」的日期（weekday: 周一=0 … 周日=6）。"""
    d = date(year, month, 1)
    # 首个目标 weekday 与 1 号的偏移
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + (n - 1) * 7)


def test_unit_dst_switch_days_are_sundays_and_closed():
    """Feature: us-market-overview, Task 2.5

    「春进」为 3 月第二个周日、「秋退」为 11 月第一个周日。两者均为周日，
    因此切换日当天应恒为 closed（非交易日）。同时校验切换日确实是 DST
    边界（同日 00:00 与 12:00 的 UTC 偏移不同）。

    Validates: Requirements 9.1
    """
    spring_forward = _nth_weekday_of_month(2026, 3, 6, 2)  # 3 月第二个周日
    fall_back = _nth_weekday_of_month(2026, 11, 6, 1)      # 11 月第一个周日

    # 确认计算正确：均为周日
    assert spring_forward.weekday() == 6, f"{spring_forward} 应为周日"
    assert fall_back.weekday() == 6, f"{fall_back} 应为周日"
    # 2026 年具体日期
    assert spring_forward == date(2026, 3, 8)
    assert fall_back == date(2026, 11, 1)

    for switch_day in (spring_forward, fall_back):
        # 切换日确为 DST 边界：当日早晚 UTC 偏移不同
        early = datetime.combine(switch_day, time(0, 0), tzinfo=US_TZ)
        late = datetime.combine(switch_day, time(12, 0), tzinfo=US_TZ)
        assert early.utcoffset() != late.utcoffset(), (
            f"{switch_day} 未跨越 DST 边界，测试前提不成立"
        )
        # 周日 → 恒 closed
        result = detect_us_session(late)
        assert result["session_kind"] == "closed", (
            f"DST 切换日（周日）应为 closed @ {switch_day.isoformat()}: "
            f"got {result['session_kind']!r}"
        )
        assert result["session_label"] == "休市"


def test_unit_dst_switch_surrounding_trading_days_wallclock_consistent():
    """Feature: us-market-overview, Task 2.5

    围绕 DST 切换日的相邻交易日：切换前的周五（标准时/夏令时之一）与切换后的
    周一（另一侧）。同一 ET 墙钟时间在两侧的 `session_kind` 必须一致，证明
    时段判定基于 DST 感知的墙钟时间，而非固定 UTC 偏移。

    Validates: Requirements 9.1
    """
    spring_forward = _nth_weekday_of_month(2026, 3, 6, 2)  # 2026-03-08 周日
    fall_back = _nth_weekday_of_month(2026, 11, 6, 1)      # 2026-11-01 周日

    probe_times = [
        time(9, 30, 0),    # 盘中起点
        time(12, 0, 0),    # 盘中
        time(16, 0, 0),    # 盘后起点
        time(20, 0, 0),    # 休市
    ]

    for switch_day in (spring_forward, fall_back):
        friday_before = switch_day - timedelta(days=2)   # 切换日前的周五
        monday_after = switch_day + timedelta(days=1)     # 切换日后的周一
        assert friday_before.weekday() == 4, f"{friday_before} 应为周五"
        assert monday_after.weekday() == 0, f"{monday_after} 应为周一"

        # 确认两个交易日分处 DST 边界两侧（UTC 偏移不同）
        before_off = datetime.combine(
            friday_before, time(12, 0), tzinfo=US_TZ
        ).utcoffset()
        after_off = datetime.combine(
            monday_after, time(12, 0), tzinfo=US_TZ
        ).utcoffset()
        assert before_off != after_off, (
            f"{friday_before} 与 {monday_after} 未跨越 DST 边界"
        )

        for wall in probe_times:
            before_kind = detect_us_session(
                datetime.combine(friday_before, wall, tzinfo=US_TZ)
            )["session_kind"]
            after_kind = detect_us_session(
                datetime.combine(monday_after, wall, tzinfo=US_TZ)
            )["session_kind"]
            assert before_kind == after_kind, (
                f"相同墙钟 {wall.isoformat()} 跨 DST 切换日两侧时段不一致："
                f"{friday_before}→{before_kind!r}, {monday_after}→{after_kind!r}"
            )
