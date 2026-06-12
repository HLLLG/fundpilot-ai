from app.services.fund_profile import parse_profile_from_text
from app.services.ocr_parser import detect_ocr_source, is_yangjibao_detail_page

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


def test_detect_yangjibao_detail_source():
    assert detect_ocr_source(DETAIL_TEXT) == "yangjibao_detail"


def test_is_yangjibao_detail_page():
    lines = [line.strip() for line in DETAIL_TEXT.splitlines() if line.strip()]
    assert is_yangjibao_detail_page(lines) is True


def test_yangjibao_detail_pipeline_preview():
    from app.services.ocr_pipeline import _run_yangjibao_detail_pipeline
    from app.services.fund_profile import FundProfileService

    result = _run_yangjibao_detail_pipeline(
        text=DETAIL_TEXT,
        upload_path=None,
        cache_hit=False,
        preview=True,
        profile_service=FundProfileService(),
    )

    assert result["ocr_source"] == "yangjibao_detail"
    assert result["holdings"]
    assert result["detail_profile"]["fund_code"] == "025856"
    assert result["detail_profile"]["sector_name"]


def test_parse_profile_from_detail_text():
    profile = parse_profile_from_text(DETAIL_TEXT)
    assert profile is not None
    assert profile.fund_code == "025856"
