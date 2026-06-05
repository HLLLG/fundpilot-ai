"""养基宝详情页四只实测基金的 OCR 文本布局回归。"""

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

FUND_015945 = """
易方达国防军工混合C
015945
持有金额
1,188.96
613.18
4.59%
持有收益
-89.15
-6.94%
2.0946
关联板块
业绩走势
我的收益
06-03
商业航天 +3.35%
关联板块：商业航天 +3.52%
16只同类基金
"""

FUND_008586 = """
华夏人工智能ETF联接C
008586
持有金额
8,270.43
关联板块
业绩走势
我的收益
06-03
中证人工智能 +4.78%
场内指数
中证人工智能 +4.58%
关联板块
人工智能 +4.00%
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


def test_fund_015945_commercial_aerospace_only_board():
    profile = parse_profile_from_text(FUND_015945)
    assert profile is not None
    assert profile.fund_code == "015945"
    assert profile.intraday_index_name is None
    assert profile.sector_name == "商业航天"
    assert profile.sector_return_percent == 3.52

    holding = Holding(
        fund_code="015945",
        fund_name=profile.fund_name,
        holding_amount=profile.holding_amount or 0,
        return_percent=0,
        sector_name=profile.sector_name,
    )
    assert sector_quote_lookup_label(holding) == "商业航天"


def test_fund_008586_csi_ai_index_and_ai_board():
    profile = parse_profile_from_text(FUND_008586)
    assert profile is not None
    assert profile.fund_code == "008586"
    assert profile.intraday_index_name == "中证人工智能"
    assert profile.sector_name == "人工智能"
    assert profile.sector_return_percent == 4.58

    holding = Holding(
        fund_code="008586",
        fund_name=profile.fund_name,
        holding_amount=profile.holding_amount or 0,
        return_percent=0,
        sector_name=profile.sector_name,
        intraday_index_name=profile.intraday_index_name,
    )
    assert sector_quote_lookup_label(holding) == "中证人工智能"


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


def test_fund_519674_intraday_index_from_sector_on_resolve(tmp_path, monkeypatch):
    from app.database import save_fund_profile
    from app.models import FundProfile
    from app.services.fund_profile import FundProfileService

    db_path = tmp_path / "test.db"
    monkeypatch.setenv("FUND_AI_DB_PATH", str(db_path))
    from app.config import get_settings

    get_settings.cache_clear()

    save_fund_profile(
        FundProfile(
            fund_code="519674",
            fund_name="银河创新成长混合A",
            sector_name="半导体",
        )
    )
    holding = Holding(
        fund_code="519674",
        fund_name="银河创新成长混合A",
        holding_amount=4042.24,
        return_percent=1.94,
        sector_name="半导体",
    )
    resolved = FundProfileService().resolve_holding(holding)
    assert resolved.intraday_index_name == "中证半导体"


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

    bad = parse_profile_from_text(
        "银河创新成长混合A\n519674\n持有金额\n4042.24\n关联板块 +4.01%"
    )
    assert bad is not None
    assert bad.sector_name != "+"

    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    refresh_settings()
    service = FundProfileService()
    good = parse_profile_from_text(FUND_519674)
    assert good is not None
    service.save_profile(good)
    merged = service.save_profile(bad)
    assert merged.sector_name == "半导体"


def test_save_all_four_profiles_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    from app.config import refresh_settings

    refresh_settings()
    service = FundProfileService()
    for text in (FUND_025856, FUND_015945, FUND_008586, FUND_519674):
        profile = parse_profile_from_text(text)
        assert profile is not None
        saved = service.save_profile(profile)
        assert saved.sector_name

    codes = {p.fund_code: p for p in service.list_profiles()}
    assert codes["025856"].sector_name == "电网设备"
    assert codes["015945"].sector_name == "商业航天"
    assert codes["008586"].sector_name == "人工智能"
    assert codes["519674"].sector_name == "半导体"
