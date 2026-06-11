from app.models import Holding
from app.config import refresh_settings
from app.main import app
from app.services.fund_profile import (
    FundProfileService,
    parse_profile_from_text,
)
from fastapi.testclient import TestClient


DETAIL_TEXT = """
华夏中证电网设备主题ETF联接A
025856
当日涨幅05-29
近1年
持有人数排名
-0.57%
+41.61%
181/26141
持有金额
持有份额
持仓占比
15,075.46
10,645.76
52.76%
持有收益
持有收益率
持仓成本
+401.80
+2.74%
1.3784
当日收益
昨日收益
持有天数
-85.93
-86.23
95
关联板块
业绩走势
我的收益
日期05-29
中证电网设备▼-0.59%
"""


def test_parse_yangjibao_detail_profile_text():
    profile = parse_profile_from_text(DETAIL_TEXT)

    assert profile is not None
    assert profile.fund_code == "025856"
    assert profile.fund_name == "华夏中证电网设备主题ETF联接A"
    assert profile.holding_amount == 15075.46
    assert profile.holding_shares == 10645.76
    assert profile.position_percent == 52.76
    assert profile.holding_profit == 401.80
    assert profile.holding_return_percent == 2.74
    assert profile.holding_cost == 1.3784
    assert profile.daily_profit == -85.93
    assert profile.holding_days == 95
    assert profile.intraday_index_name == "中证电网设备"
    assert profile.sector_name == "电网设备"
    assert profile.sector_return_percent == -0.59


DETAIL_WITH_INDEX_AND_BOARD = """
华夏中证电网设备主题ETF联接A
025856
持有金额
12,406.59
场内指数
中证电网设备 +1.59%
关联板块：电网设备 +1.52%
"""


def test_parse_detail_with_intraday_index_and_related_board():
    profile = parse_profile_from_text(DETAIL_WITH_INDEX_AND_BOARD)
    assert profile is not None
    assert profile.intraday_index_name == "中证电网设备"
    assert profile.sector_name == "电网设备"
    assert profile.sector_return_percent == 1.59


COMMERCIAL_AEROSPACE_DETAIL = """
易方达国防军工混合C
015945
持有金额
1,188.96
关联板块：商业航天 +3.52%
"""


YANGJIBAO_BOTTOM_LAYOUT = """
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


def test_parse_detail_without_intraday_index_uses_related_board():
    profile = parse_profile_from_text(COMMERCIAL_AEROSPACE_DETAIL)
    assert profile is not None
    assert profile.intraday_index_name is None
    assert profile.sector_name == "商业航天"
    assert profile.sector_return_percent == 3.52


def test_parse_yangjibao_bottom_section_layout():
    profile = parse_profile_from_text(YANGJIBAO_BOTTOM_LAYOUT)
    assert profile is not None
    assert profile.intraday_index_name == "中证电网设备"
    assert profile.sector_name == "电网设备"
    assert profile.sector_return_percent == 1.59


def test_merge_detail_profile_preserves_sector_when_ocr_misses(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    from app.config import refresh_settings

    refresh_settings()
    service = FundProfileService()
    full = parse_profile_from_text(YANGJIBAO_BOTTOM_LAYOUT)
    assert full is not None
    service.save_profile(full)

    partial = parse_profile_from_text(
        "华夏中证电网设备主题ETF联接A\n025856\n持有金额\n12,500.00\n8,800.00\n48.00%"
    )
    assert partial is not None
    assert partial.sector_name == "电网设备"
    assert partial.intraday_index_name == "中证电网设备"
    merged = service.save_profile(partial)
    assert merged.sector_name == "电网设备"
    assert merged.intraday_index_name == "中证电网设备"


def test_resolve_overview_holding_with_saved_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    refresh_settings()
    service = FundProfileService()
    profile = parse_profile_from_text(DETAIL_TEXT)
    assert profile is not None
    service.save_profile(profile)

    holding = Holding(
        fund_code="000000",
        fund_name="华夏中证电网设备...",
        holding_amount=15161.69,
        return_percent=0.87,
        daily_profit=488.03,
        sector_name="中证电网设备",
        sector_return_percent=3.33,
    )

    resolved = service.resolve_holding(holding)

    assert resolved.fund_code == "025856"
    assert resolved.fund_name == "华夏中证电网设备主题ETF联接A"
    assert resolved.user_note is None


def test_resolve_truncated_overview_names_with_profile_aliases(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    refresh_settings()
    service = FundProfileService()
    service.save_profile(
        parse_profile_from_text(
            "华夏人工智能ETF联接C\n008586\n持有金额\n7,427.01\n4,221.57\n25.99%"
        )
    )
    service.save_profile(
        parse_profile_from_text(
            "易方达国防军工混合C\n015945\n持有金额\n1,846.93\n922.08\n6.46%"
        )
    )

    artificial = service.resolve_holding(
        Holding(
            fund_code="000000",
            fund_name="华夏人工智能ETF.",
            holding_amount=7701.83,
        )
    )
    defense = service.resolve_holding(
        Holding(
            fund_code="000000",
            fund_name="易方达国防军工混...",
            holding_amount=1949.28,
        )
    )

    assert artificial.fund_code == "008586"
    assert defense.fund_code == "015945"


def test_save_profile_from_text_and_use_it_in_analysis(tmp_path, monkeypatch):
    from app.services.fund_profile import FundProfileService, parse_profile_from_text

    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", "")
    refresh_settings()
    client = TestClient(app)

    profile = parse_profile_from_text(DETAIL_TEXT)
    assert profile is not None
    assert profile.fund_code == "025856"
    FundProfileService().save_profile(profile)

    list_response = client.get("/api/fund-profiles")
    assert list_response.status_code == 200
    assert list_response.json()[0]["fund_code"] == "025856"

    analysis_response = client.post(
        "/api/analyze",
        json={
            "holdings": [
                {
                    "fund_code": "000000",
                    "fund_name": "华夏中证电网设备...",
                    "holding_amount": 15161.69,
                    "return_percent": 0.87,
                    "daily_profit": 488.03,
                    "sector_name": "中证电网设备",
                    "sector_return_percent": 3.33,
                }
            ]
        },
    )

    body = analysis_response.json()
    assert analysis_response.status_code == 200
    assert body["holdings"][0]["fund_code"] == "025856"
    assert body["holdings"][0]["fund_name"] == "华夏中证电网设备主题ETF联接A"


# 问题1修复：多行版式关联板块识别
DETAIL_MULTILINE_BOARD_NAME_SEPARATE = """
华夏人工智能ETF联接C
008586
持有金额
7,250.12
4,687.39
31.92%
持有收益
229.92
2.77%
1.7730
当日收益
昨日收益
持有天数
270.44
220.31
23
关联板块
业绩走势
我的收益
日期 06-03
中证人工智能
+3.27%
"""


def test_parse_detail_multiline_related_board_name_separate():
    """关联板块标签和板块名在不同行（OCR多行分割）"""
    profile = parse_profile_from_text(DETAIL_MULTILINE_BOARD_NAME_SEPARATE)
    assert profile is not None
    assert profile.sector_name == "人工智能"
    assert profile.intraday_index_name == "中证人工智能"
    assert profile.sector_return_percent == 3.27


DETAIL_BOARD_NAME_ONLY_NO_PERCENT = """
银河创新成长混合A
519674
持有金额
4,042.24
329.24
15.60%
关联板块
半导体
+2.74%
"""


def test_parse_detail_board_name_without_full_percent_marker():
    """关联板块仅有名称和百分比，但百分比可能在下一行（多行版）"""
    profile = parse_profile_from_text(DETAIL_BOARD_NAME_ONLY_NO_PERCENT)
    assert profile is not None
    assert profile.sector_name == "半导体"
    assert profile.sector_return_percent == 2.74


DETAIL_PERCENT_IN_NEXT_LINE = """
易方达国防军工混合C
015945
持有金额
1,188.96
613.18
4.59%
关联板块
商业航天
+2.39%
"""


def test_parse_detail_board_percent_in_next_line():
    """关联板块名和百分比分别在不同行"""
    profile = parse_profile_from_text(DETAIL_PERCENT_IN_NEXT_LINE)
    assert profile is not None
    assert profile.sector_name == "商业航天"
    assert profile.sector_return_percent == 2.39


DETAIL_WITH_TRAILING_LIKE_LABELS = """
华夏中证电网设备主题ETF联接A
025856
关联板块
中证电网设备
9只同类基金
+0.91%
"""


def test_parse_detail_strips_trailing_fund_count_label():
    """关联板块名后跟'9只同类基金'标签的情况"""
    profile = parse_profile_from_text(DETAIL_WITH_TRAILING_LIKE_LABELS)
    assert profile is not None
    assert profile.sector_name == "电网设备"
    assert profile.intraday_index_name == "中证电网设备"
    assert profile.sector_return_percent == 0.91
