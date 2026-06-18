"""Unit tests for us_futures_client（任务 3.1）.

校验：
  - 模块可干净导入
  - 离线 fixture（futures_global_spot_em 真实返回，620 行）解析正确，
    映射出 NASDAQ_FUT / SP500_FUT / DOW_FUT 三品种，优先「当月连续」主力合约，
    数值取「最新价」/「涨跌幅」。

_Requirements: 1.1, 1.3_
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import us_futures_client
from app.services.us_futures_client import (
    fetch_us_index_futures,
    parse_us_index_futures,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "us_futures_global_spot_em.json"
)


@pytest.fixture(scope="module")
def fixture_records() -> list[dict]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return payload["records"]


def test_module_imports_cleanly():
    assert callable(fetch_us_index_futures)
    assert callable(parse_us_index_futures)


def test_parses_three_symbols(fixture_records):
    quotes = parse_us_index_futures(fixture_records, quote_time="2026-06-17T08:12:00-04:00")
    symbols = [q["symbol"] for q in quotes]
    assert symbols == ["NASDAQ_FUT", "SP500_FUT", "DOW_FUT"]


def test_picks_front_month_continuous_values(fixture_records):
    quotes = parse_us_index_futures(fixture_records, quote_time="2026-06-17T08:12:00-04:00")
    by_symbol = {q["symbol"]: q for q in quotes}

    # 真实 fixture 中「当月连续」主力合约的数值（小型纳指/标普/道指当月连续）
    assert by_symbol["NASDAQ_FUT"]["last_price"] == pytest.approx(30102.4)
    assert by_symbol["NASDAQ_FUT"]["change_percent"] == pytest.approx(-0.7)
    assert by_symbol["SP500_FUT"]["last_price"] == pytest.approx(7505.4)
    assert by_symbol["DOW_FUT"]["last_price"] == pytest.approx(52056.0)


def test_quote_time_and_display_names(fixture_records):
    quotes = parse_us_index_futures(fixture_records, quote_time="2026-06-17T08:12:00-04:00")
    for quote in quotes:
        assert quote["quote_time"] == "2026-06-17T08:12:00-04:00"
        assert quote["display_name"] in {"纳斯达克", "标普500", "道琼斯"}
        assert quote["last_price"] is not None


def test_empty_records_returns_empty_list():
    assert parse_us_index_futures([]) == []


def test_no_close_price_endpoint_referenced():
    """禁止使用指数/收盘接口作为数值来源（需求 1.3）。

    仅检查实际执行的子进程脚本：它只能调用真实期货实时源，
    不得调用任何指数/收盘接口。
    """
    script = us_futures_client._FUTURES_SCRIPT
    assert "ak.futures_global_spot_em()" in script
    assert "index_us_stock_sina" not in script
    assert "stock_us_" not in script
    assert "stock_zh_index" not in script
