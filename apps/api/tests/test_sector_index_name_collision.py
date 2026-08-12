"""同名指数不得互相顶替：`boards["index"]` 只能按简称索引，简称并不唯一。

回归背景（生产事故，2026-08-12 盘中日报）：东财对**两只成分完全不同**的指数都显示
简称「数字经济」——深证 399262 与中证 931582。`fetch_eastmoney_boards` 把四个指数池
（主要指数 / 中证 m:2 / 上证 m:1+t:1 / 深证 m:0+t:5）压平进同一个按简称索引的字典，
用的是裸 `update()`，于是**最后**并入的深证池覆盖了中证池。

后果不是"数字差一点"，而是换了一只标的：同一时刻深证 +2.29%、中证 +1.54%。持仓
「鹏扬中证数字经济主题ETF发起联接C」跟踪的是中证那只，日报却拿深证的涨幅写成
「板块今日涨+2.34%」，并据此判断量价背离。同一份报告里 `sector_opportunity`
（走 m:2 单池，没被污染）写着 1.35，`sector_return_percent` 写着 2.34——两个字段
描述同一个板块的同一天，差了近一个百分点。

registry 早就固定了身份（`THEME_BOARD_INDEX["数字经济"] = 2.931582`），也为它登记了
`THEME_BOARD_PROVIDER_IDENTITIES`；但那道校验只在 canonical secid 路径上跑，按简称
取值的那几条路（持久化映射快捷路径、canonical 的 board 兜底、模糊匹配）完全绕过它。
所以修复必须落在"简称字典本身不许出现替身"这一层。
"""
from __future__ import annotations

from app.services import eastmoney_spot_client

# 2026-08-12 11:30 的真实值，用于让断言绑定在"取错标的"而非"取错日期"上。
CSI_DIGITAL_ECONOMY = ("931582", 1.54)
SZSE_DIGITAL_ECONOMY = ("399262", 2.29)


def _rows(*entries: tuple[str, str, float]) -> list[dict]:
    return [
        {"f14": name, "f12": code, "f3": change} for name, code, change in entries
    ]


def _install_pools(monkeypatch, pools: dict[str, list[dict]]) -> None:
    """按 clist 的 `fs` 参数分派伪造分页响应。"""

    class FakeResponse:
        def __init__(self, rows: list[dict]) -> None:
            self._rows = rows

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"data": {"diff": self._rows, "total": len(self._rows)}}

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def get(self, _url: str, params: dict | None = None, **_kwargs) -> FakeResponse:
            fs = str((params or {}).get("fs") or "")
            return FakeResponse(pools.get(fs, []))

    monkeypatch.setattr(
        eastmoney_spot_client,
        "eastmoney_httpx_client",
        lambda **kwargs: FakeClient(**kwargs),
    )


def _index_board(monkeypatch, pools: dict[str, list[dict]]) -> dict[str, float]:
    _install_pools(monkeypatch, pools)
    return eastmoney_spot_client.fetch_eastmoney_boards(max_retries=1)["index"]


def test_later_pool_cannot_overwrite_the_registry_owned_index(monkeypatch) -> None:
    """深证池最后并入，但它那只同名指数不能顶掉 registry 认定的中证 931582。"""
    board = _index_board(
        monkeypatch,
        {
            "m:2": _rows(("数字经济", *CSI_DIGITAL_ECONOMY)),
            "m:0+t:5": _rows(("数字经济", *SZSE_DIGITAL_ECONOMY)),
        },
    )

    assert board["数字经济"] == 1.54


def test_registry_owned_index_wins_regardless_of_pool_order(monkeypatch) -> None:
    """身份由 registry 决定，不由东财返回顺序决定——把两只对调依然取中证那只。

    这一条是与"改成先到者优先"的关键区别：单纯反转优先级只是把赌注押在另一个
    池上，池的构成一变就又错了。
    """
    board = _index_board(
        monkeypatch,
        {
            "m:2": _rows(("数字经济", *SZSE_DIGITAL_ECONOMY)),
            "m:0+t:5": _rows(("数字经济", *CSI_DIGITAL_ECONOMY)),
        },
    )

    assert board["数字经济"] == 1.54


def test_wrong_security_alone_is_dropped_instead_of_relabelled(monkeypatch) -> None:
    """中证池缺失时没有冲突可判，但也不能把深证那只当成「数字经济」交出去。

    缺席会让取值退回 canonical secid 路径（过 identity 校验的那条）；交出替身则会
    被原样写进持仓行并喂给模型。fail-closed 是这里唯一安全的选择。
    """
    board = _index_board(
        monkeypatch,
        {"m:0+t:5": _rows(("数字经济", *SZSE_DIGITAL_ECONOMY))},
    )

    assert "数字经济" not in board


def test_registry_owned_index_with_the_right_code_is_kept(monkeypatch) -> None:
    """别把 fail-closed 做成"凡是 registry 认识的简称都删"。"""
    board = _index_board(
        monkeypatch,
        {"m:2": _rows(("数字经济", *CSI_DIGITAL_ECONOMY))},
    )

    assert board["数字经济"] == 1.54


def test_names_outside_the_registry_are_left_usable(monkeypatch) -> None:
    """registry 不认识的简称照旧可用——沪深300 在沪深两市各有一个代码，值也一致。

    这类"同名同物"不是事故，删掉它们只会白白牺牲可用性。
    """
    board = _index_board(
        monkeypatch,
        {
            "m:1+t:1": _rows(("沪深300", "000300", 0.65)),
            "m:0+t:5": _rows(("沪深300", "399300", 0.65)),
        },
    )

    assert board["沪深300"] == 0.65


def test_same_security_across_pools_still_refreshes_its_value(monkeypatch) -> None:
    """同一只标的在两个池都出现时不该被当成冲突而冻结在旧值上。"""
    board = _index_board(
        monkeypatch,
        {
            "m:2": _rows(("中证医疗", "399989", 0.10)),
            "m:0+t:5": _rows(("中证医疗", "399989", 0.15)),
        },
    )

    assert board["中证医疗"] == 0.15


def test_rows_without_a_code_are_not_dropped(monkeypatch) -> None:
    """拿不到 f12 证明不了任何事，此时保持原样，不制造可用性回退。"""
    _install_pools(
        monkeypatch,
        {"m:2": [{"f14": "数字经济", "f3": 1.54}]},
    )

    board = eastmoney_spot_client.fetch_eastmoney_boards(max_retries=1)["index"]

    assert board["数字经济"] == 1.54


def test_concept_and_industry_boards_are_untouched_by_the_index_rule(monkeypatch) -> None:
    """消歧只针对被压平的指数池；概念/行业各自单池，行为不变。"""
    _install_pools(
        monkeypatch,
        {
            "m:90 t:3 f:!50": _rows(("固态电池", "BK0968", 3.1)),
            "m:90 t:2 f:!50": _rows(("煤炭", "BK0437", -1.05)),
        },
    )

    boards = eastmoney_spot_client.fetch_eastmoney_boards(max_retries=1)

    assert boards["concept"]["固态电池"] == 3.1
    assert boards["industry"]["煤炭"] == -1.05


def test_registry_index_code_map_pins_digital_economy_to_the_csi_series() -> None:
    """消歧依据来自 registry 本身，不是测试里另写一份表。"""
    allowed = eastmoney_spot_client._registry_index_codes_by_display_name()

    assert allowed["数字经济"] == frozenset({"931582"})
    assert "沪深300" not in allowed


def test_spot_board_only_fills_gaps_and_never_overwrites_canonical() -> None:
    """现货榜（按简称）不得顶掉 canonical（按 secid + identity 校验）的值。

    这是同一起事故的第二道口子：即使指数池的消歧修好了，
    `refresh_holdings_sector_quotes` 里原先的 `merged.update(spot)` 依然会让
    prefetch 拿到的、已过身份校验的 secid 值被现货榜的简称值顶掉。
    """
    from app.services.sector_quote_service import _merge_spot_board_under_canonical

    merged = _merge_spot_board_under_canonical(
        canonical={"数字经济": 1.54},
        spot={"数字经济": 2.29, "煤炭": -1.05},
    )

    assert merged["数字经济"] == 1.54
    # 缺口仍然要补上，否则 canonical 没覆盖的板块会整体失去行情。
    assert merged["煤炭"] == -1.05


def test_merge_handles_missing_boards_without_raising() -> None:
    from app.services.sector_quote_service import _merge_spot_board_under_canonical

    assert _merge_spot_board_under_canonical(canonical=None, spot=None) == {}
    assert _merge_spot_board_under_canonical(canonical={"稀土": 0.87}, spot=None) == {
        "稀土": 0.87
    }
    assert _merge_spot_board_under_canonical(canonical=None, spot={"稀土": 0.87}) == {
        "稀土": 0.87
    }
