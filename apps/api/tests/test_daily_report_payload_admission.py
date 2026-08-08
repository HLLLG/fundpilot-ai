"""日报输出的准入校验：文案字段的类型偏差不得否决整份报告。

`_is_valid_daily_report_payload` 是"这份模型输出能不能当成真实分析"的准入门槛，
任何一项不过就整份丢弃、改用 `_offline_report`，并把每条建议改写成
「模型服务不可用…」。历史实现把 `caveats` / `recommendations` 也算作硬性字段，
可它们只是提示文案：服务端随后会用 `_user_facing_caveats` 过滤、追加新闻与流水线
提示，缺失时还有本地兜底，取值处 `_non_empty_list` 本来就容忍任意形状。

于是出现过这种情况：deepseek-v4-pro 在 HTTP 200、finish_reason=stop、内容完整
（title / summary / 4 条逐只建议全部可解析）的情况下，只因为把 caveats 写成
一个字符串而不是单元素数组，整份日报被判成 `invalid_json`、退化成离线兜底。
用户看到的是"模型不可用"，而 provider 其实完全正常。

这里锁定两件事：文案字段的偏差会被收敛；真正残缺的响应依旧被挡在门外。
"""

from __future__ import annotations

import pytest

from app.services.deepseek_client import (
    _daily_provider_response_incomplete,
    _is_valid_daily_report_payload,
    normalize_daily_report_payload,
)

FUND_RECOMMENDATION = {
    "fund_code": "015788",
    "fund_name": "鹏扬中证数字经济主题ETF发起联接C",
    "action": "观察",
    "points": ["板块蓄势，等待更明确信号"],
    "confidence": "中",
    "risks": ["主题轮动可能带来回撤"],
}


def _payload(**overrides: object) -> dict:
    payload: dict = {
        "title": "每日基金操作日报",
        "summary": "组合加权收益率 -13.34%，风险等级较高。",
        "fund_recommendations": [FUND_RECOMMENDATION],
    }
    payload.update(overrides)
    return payload


class TestAdvisoryFieldCoercion:
    def test_caveats_as_bare_string_is_accepted(self) -> None:
        """报告的 case：模型写成字符串而不是单元素数组。"""
        payload = normalize_daily_report_payload(
            _payload(caveats="今日新闻预取为空，置信度受限。")
        )

        assert payload["caveats"] == ["今日新闻预取为空，置信度受限。"]
        assert _is_valid_daily_report_payload(payload) is True
        assert _daily_provider_response_incomplete(payload) is False

    def test_missing_caveats_falls_back_to_server_text(self) -> None:
        # 缺免责声明不是"分析残缺"；服务端本来就会补本地兜底文案。
        payload = normalize_daily_report_payload(_payload())

        assert payload["caveats"] == []
        assert _daily_provider_response_incomplete(payload) is False

    def test_recommendations_as_bare_string_is_accepted(self) -> None:
        payload = normalize_daily_report_payload(
            _payload(caveats=["仅供参考"], recommendations="组合整体维持观察。")
        )

        assert payload["recommendations"] == ["组合整体维持观察。"]
        assert _daily_provider_response_incomplete(payload) is False

    def test_numeric_entries_are_stringified(self) -> None:
        payload = normalize_daily_report_payload(_payload(caveats=["提示", 1, 2.5]))

        assert payload["caveats"] == ["提示", "1", "2.5"]
        assert _daily_provider_response_incomplete(payload) is False

    def test_blank_entries_are_dropped(self) -> None:
        payload = normalize_daily_report_payload(_payload(caveats=["  ", "有效提示", ""]))

        assert payload["caveats"] == ["有效提示"]

    def test_proper_list_is_left_untouched(self) -> None:
        payload = normalize_daily_report_payload(_payload(caveats=["甲", "乙"]))

        assert payload["caveats"] == ["甲", "乙"]

    def test_decision_fields_are_never_coerced(self) -> None:
        """只收敛文案字段，决策事实原样保留。"""
        payload = normalize_daily_report_payload(_payload(caveats="提示"))

        assert payload["title"] == "每日基金操作日报"
        assert payload["fund_recommendations"] == [FUND_RECOMMENDATION]


class TestGateStillFailsClosed:
    """收敛只针对文案；残缺响应必须继续走 provider 失败兜底。"""

    def test_nested_objects_are_not_guessed_at(self) -> None:
        # caveats 里是对象说明模型套用了另一套 schema，不做猜测式改写。
        payload = normalize_daily_report_payload(_payload(caveats=[{"text": "提示"}]))

        assert payload["caveats"] == [{"text": "提示"}]
        assert _daily_provider_response_incomplete(payload) is True

    @pytest.mark.parametrize(
        ("broken", "reason"),
        [
            ({"summary": "s", "fund_recommendations": [FUND_RECOMMENDATION]}, "缺 title"),
            ({"title": "t", "fund_recommendations": [FUND_RECOMMENDATION]}, "缺 summary"),
            ({"title": "t", "summary": "", "fund_recommendations": [FUND_RECOMMENDATION]}, "summary 为空"),
            ({"title": "t", "summary": "s", "fund_recommendations": []}, "没有逐只建议"),
            ({"title": "t", "summary": "s", "fund_recommendations": "观察"}, "逐只建议不是数组"),
            ({"title": "t", "summary": "s", "fund_recommendations": ["观察"]}, "逐只建议不是对象"),
        ],
    )
    def test_missing_decision_fields_still_rejected(self, broken: dict, reason: str) -> None:
        payload = normalize_daily_report_payload(dict(broken))

        assert _daily_provider_response_incomplete(payload) is True, reason

    def test_truncated_marker_still_rejected(self) -> None:
        payload = normalize_daily_report_payload(_payload(caveats="提示", _truncated=True))

        assert _daily_provider_response_incomplete(payload) is True

    def test_non_dict_payload_is_passed_through(self) -> None:
        assert normalize_daily_report_payload(None) is None  # type: ignore[arg-type]
        assert _is_valid_daily_report_payload(None) is False
