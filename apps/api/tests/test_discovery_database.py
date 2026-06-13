from app.database import (
    delete_discovery_report,
    get_discovery_report,
    list_discovery_reports,
    save_discovery_report,
)
from app.models import DiscoveryRecommendation, FundDiscoveryReport


def test_save_and_list_discovery_report():
    report = FundDiscoveryReport(
        title="今日推荐扫描",
        summary="市场震荡，关注半导体与商业航天",
        target_sectors=["半导体", "商业航天"],
        recommendations=[
            DiscoveryRecommendation(
                fund_code="519674",
                fund_name="测试基金",
                sector_name="半导体",
                action="建议关注",
                hold_horizon="1-3个月",
                points=["板块热度靠前"],
            )
        ],
        caveats=["仅供参考，不构成投资建议"],
    )
    save_discovery_report(report)

    listed = list_discovery_reports()
    assert len(listed) >= 1
    assert listed[0]["id"] == report.id
    assert listed[0]["title"] == "今日推荐扫描"

    loaded = get_discovery_report(report.id)
    assert loaded is not None
    assert loaded["recommendations"][0]["fund_code"] == "519674"


def test_delete_discovery_report():
    report = FundDiscoveryReport(title="待删除")
    save_discovery_report(report)

    assert delete_discovery_report(report.id) is True
    assert get_discovery_report(report.id) is None
    assert delete_discovery_report(report.id) is False
