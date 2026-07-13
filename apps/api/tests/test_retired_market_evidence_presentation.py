from __future__ import annotations

from app import main


def _legacy_report() -> dict:
    return {
        "id": "legacy-report",
        "summary": "市场震荡。北向实时净买额暂停披露。仍需控制仓位。",
        "caveats": ["北向数据缺失影响判断", "仅供参考"],
        "analysis_facts": {
            "stock_connect_flow": {
                "northbound_status": "not_disclosed",
                "southbound_net_yi": 3.2,
            }
        },
    }


def _assert_presentation_is_clean(report: dict) -> None:
    assert "北向" not in str(report)
    assert "northbound" not in str(report).lower()
    assert report["summary"] == "市场震荡。仍需控制仓位。"
    assert report["caveats"] == ["仅供参考"]
    assert report["analysis_facts"]["stock_connect_flow"] == {
        "southbound_net_yi": 3.2
    }


def test_discovery_history_list_and_detail_sanitize_presentation_copy(monkeypatch) -> None:
    raw = _legacy_report()
    monkeypatch.setattr(main, "list_discovery_reports", lambda: [raw])
    monkeypatch.setattr(main, "get_discovery_report", lambda _report_id: raw)

    listed = main.fund_discovery_reports()
    detail = main.fund_discovery_report_detail("legacy-report")

    _assert_presentation_is_clean(listed[0])
    _assert_presentation_is_clean(detail)
    assert "北向" in raw["summary"]


def test_daily_history_list_and_detail_sanitize_presentation_copy(monkeypatch) -> None:
    raw = _legacy_report()
    monkeypatch.setattr(main, "list_reports", lambda: [raw])
    monkeypatch.setattr(main, "get_report", lambda _report_id: raw)

    listed = main.reports()
    detail = main.report_detail("legacy-report")

    _assert_presentation_is_clean(listed[0])
    _assert_presentation_is_clean(detail)
    assert "北向" in raw["summary"]
