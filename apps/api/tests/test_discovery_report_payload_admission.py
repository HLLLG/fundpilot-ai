"""荐基输出的准入校验：文案字段的类型偏差不得否决整份报告。

日报已经修过：deepseek-v4-pro 在 HTTP 200、finish_reason=stop 时把 `caveats`
写成字符串，整份报告被判 `invalid_json`。荐基实测同一模型还会在白名单为空时
省略 `recommendations`，或把它写成一句叙事。服务端随后会补免责声明，解析层
对非数组推荐本来就按空列表处理，这两处偏差不该退化成规则兜底。
"""

from __future__ import annotations

import pytest

from app.services.deepseek_client import (
    _discovery_provider_response_incomplete,
    _is_valid_discovery_report_payload,
    normalize_discovery_report_payload,
)

RECOMMENDATION = {
    "fund_code": "021362",
    "fund_name": "易方达黄金股指数发起式A",
    "sector_name": "黄金股",
    "action": "等待回调",
}


def _payload(**overrides: object) -> dict:
    payload: dict = {
        "title": "空仓观望：无候选基金通过白名单门槛",
        "summary": "黄金是唯一可启动方向，但载体未过门，本次输出 0 只推荐。",
        "recommendations": [],
        "caveats": ["仅供参考"],
    }
    payload.update(overrides)
    return payload


class TestAdvisoryFieldCoercion:
    def test_caveats_as_bare_string_is_accepted(self) -> None:
        payload = normalize_discovery_report_payload(
            _payload(caveats="今日新闻预取为空，置信度受限。")
        )

        assert payload["caveats"] == ["今日新闻预取为空，置信度受限。"]
        assert _is_valid_discovery_report_payload(payload) is True
        assert _discovery_provider_response_incomplete(payload) is False

    def test_missing_caveats_falls_back_to_empty_list(self) -> None:
        raw = {
            "title": "空仓观望",
            "summary": "没有基金同时通过门槛。",
            "recommendations": [],
        }
        payload = normalize_discovery_report_payload(raw)

        assert payload["caveats"] == []
        assert _discovery_provider_response_incomplete(payload) is False

    def test_missing_recommendations_is_zero_item_report(self) -> None:
        raw = {
            "title": "空仓观望",
            "summary": "没有基金同时通过门槛。",
            "caveats": ["仅供参考"],
        }
        payload = normalize_discovery_report_payload(raw)

        assert payload["recommendations"] == []
        assert _discovery_provider_response_incomplete(payload) is False

    def test_narrative_recommendations_string_is_zero_item_report(self) -> None:
        payload = normalize_discovery_report_payload(
            _payload(recommendations="暂不新增买入，维持观察。")
        )

        assert payload["recommendations"] == []
        assert _discovery_provider_response_incomplete(payload) is False

    def test_single_recommendation_object_is_wrapped(self) -> None:
        payload = normalize_discovery_report_payload(
            _payload(recommendations=RECOMMENDATION)
        )

        assert payload["recommendations"] == [RECOMMENDATION]
        assert _discovery_provider_response_incomplete(payload) is False

    def test_numeric_caveats_are_stringified(self) -> None:
        payload = normalize_discovery_report_payload(_payload(caveats=["提示", 1, 2.5]))

        assert payload["caveats"] == ["提示", "1", "2.5"]

    def test_proper_lists_are_left_untouched(self) -> None:
        payload = normalize_discovery_report_payload(
            _payload(recommendations=[RECOMMENDATION], caveats=["甲", "乙"])
        )

        assert payload["recommendations"] == [RECOMMENDATION]
        assert payload["caveats"] == ["甲", "乙"]


class TestGateStillFailsClosed:
    def test_nested_caveat_objects_are_not_guessed_at(self) -> None:
        payload = normalize_discovery_report_payload(
            _payload(caveats=[{"text": "提示"}])
        )

        assert payload["caveats"] == [{"text": "提示"}]
        assert _discovery_provider_response_incomplete(payload) is True

    @pytest.mark.parametrize(
        ("broken", "reason"),
        [
            ({"summary": "s", "recommendations": [], "caveats": []}, "缺 title"),
            ({"title": "t", "recommendations": [], "caveats": []}, "缺 summary"),
            ({"title": "t", "summary": "", "recommendations": [], "caveats": []}, "summary 为空"),
            (
                {
                    "title": "t",
                    "summary": "s",
                    "recommendations": ["观察"],
                    "caveats": [],
                },
                "推荐项不是对象",
            ),
        ],
    )
    def test_missing_decision_fields_still_rejected(self, broken: dict, reason: str) -> None:
        payload = normalize_discovery_report_payload(dict(broken))

        assert _discovery_provider_response_incomplete(payload) is True, reason

    def test_truncated_marker_still_rejected(self) -> None:
        payload = normalize_discovery_report_payload(_payload(_truncated=True))

        assert _discovery_provider_response_incomplete(payload) is True

    def test_non_dict_payload_is_passed_through(self) -> None:
        assert normalize_discovery_report_payload(None) is None  # type: ignore[arg-type]
        assert _is_valid_discovery_report_payload(None) is False
