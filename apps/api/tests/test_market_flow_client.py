from app.services.market_flow_client import _parse_northbound_summary


def test_parse_northbound_summary_reads_net_flow():
    rows = [
        {
            "交易日": "2026-06-10",
            "资金方向": "北向",
            "板块": "沪股通",
            "成交净买额": "25.0",
        },
        {
            "交易日": "2026-06-10",
            "资金方向": "北向",
            "板块": "深股通",
            "成交净买额": "-60.0",
        },
    ]
    summary = _parse_northbound_summary(rows, "2026-06-10")
    assert summary is not None
    assert summary["northbound_net_yi"] == -35.0
    assert "净流出" in summary["interpretation"]
