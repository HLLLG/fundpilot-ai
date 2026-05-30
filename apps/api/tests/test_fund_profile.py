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
    assert profile.sector_name == "中证电网设备"
    assert profile.sector_return_percent == -0.59


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
    assert resolved.user_note is not None
    assert "基金档案" in resolved.user_note


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


def test_create_profile_from_text_endpoint_and_use_it_in_analysis(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", "")
    refresh_settings()
    client = TestClient(app)

    profile_response = client.post(
        "/api/fund-profiles/ocr",
        data={"raw_text": DETAIL_TEXT},
    )

    assert profile_response.status_code == 200
    assert profile_response.json()["fund_code"] == "025856"

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
