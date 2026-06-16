"""养基宝详情页四只实测基金的 OCR 文本布局回归（保留代表性用例）。"""

from app.models import Holding
from app.services.fund_profile import FundProfileService, parse_profile_from_text
from app.services.sector_quote_label import sector_quote_lookup_label

FUND_025856 = """
华夏中证电网设备主题ETF联接A
025856
持有金额
12,406.59
8,721.07
47.89%
持有收益
582.94
4.85%
1.3784
当日收益
197.26
69.20
99
关联板块
业绩走势
我的收益
06-03
中证电网设备 +1.59%
场内指数
中证电网设备 +1.59%
关联板块
电网设备
9只同类基金
"""

FUND_519674 = """
银河创新成长混合A
519674
持有金额
4,042.24
329.24
15.60%
持有收益
80.70
1.94%
12.6335
关联板块
业绩走势
我的收益
06-03
半导体 +4.01%
关联板块
半导体 +4.01% >
国产算力
"""


def test_fund_025856_grid_equipment_index_and_board():
    profile = parse_profile_from_text(FUND_025856)
    assert profile is not None
    assert profile.fund_code == "025856"
    assert profile.intraday_index_name == "中证电网设备"
    assert profile.sector_name == "电网设备"
    assert profile.sector_return_percent == 1.59

    holding = Holding(
        fund_code="025856",
        fund_name=profile.fund_name,
        holding_amount=profile.holding_amount or 0,
        return_percent=0,
        sector_name=profile.sector_name,
        intraday_index_name=profile.intraday_index_name,
    )
    assert sector_quote_lookup_label(holding) == "中证电网设备"


def test_fund_519674_semiconductor_primary_not_domestic_compute():
    profile = parse_profile_from_text(FUND_519674)
    assert profile is not None
    assert profile.fund_code == "519674"
    assert profile.sector_name == "半导体"
    assert profile.sector_return_percent == 4.01

    holding = Holding(
        fund_code="519674",
        fund_name=profile.fund_name,
        holding_amount=profile.holding_amount or 0,
        return_percent=0,
        sector_name=profile.sector_name,
    )
    assert sector_quote_lookup_label(holding) == "半导体"


def test_reject_percent_only_related_board_line(tmp_path, monkeypatch):
    """OCR 常把「关联板块 +4.01%」误识别为板块名「+」。"""
    from app.config import refresh_settings

    text = """
银河创新成长混合A
519674
持有金额
4,042.24
关联板块 +4.01%
"""
    profile = parse_profile_from_text(text)
    assert profile is not None
    assert profile.sector_name != "+"

    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    refresh_settings()
    service = FundProfileService()
    good = parse_profile_from_text(FUND_519674)
    assert good is not None
    service.save_profile(good)
    bad = parse_profile_from_text(
        "银河创新成长混合A\n519674\n持有金额\n4042.24\n关联板块 +4.01%"
    )
    assert bad is not None
    merged = service.save_profile(bad)
    assert merged.sector_name == "半导体"
