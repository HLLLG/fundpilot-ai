import pytest

from app.services.vlm_holdings_provider import (
    extract_holdings_via_vlm,
    parse_vlm_response,
)


def test_parse_vlm_response_plain_json():
    content = (
        '{"holdings":[{"fund_name":"天弘全球高端制造混合(QDII)C",'
        '"fund_code":null,"holding_amount":100.0,"daily_profit":0.0,'
        '"holding_profit":0.0,"holding_return_percent":0.0,"weight_percent":0.29}]}'
    )
    holdings = parse_vlm_response(content)
    assert len(holdings) == 1
    h = holdings[0]
    assert h.fund_name == "天弘全球高端制造混合(QDII)C"
    assert h.fund_code == "000000"  # 无码 → 占位，交给下游查码
    assert h.holding_amount == 100.0
    assert h.holding_return_percent == 0.0


def test_parse_vlm_response_fenced_and_prose():
    content = (
        "好的，识别结果如下：\n```json\n"
        '{"holdings":[{"fund_name":"中航机遇领航混合C","holding_amount":9626.7,'
        '"holding_profit":-373.3,"holding_return_percent":-3.73}]}\n```\n'
    )
    holdings = parse_vlm_response(content)
    assert len(holdings) == 1
    assert holdings[0].fund_name == "中航机遇领航混合C"
    assert holdings[0].holding_profit == -373.3


def test_parse_vlm_response_uses_six_digit_code_when_present():
    content = '{"holdings":[{"fund_name":"X混合C","fund_code":"026790","holding_amount":1000.0}]}'
    holdings = parse_vlm_response(content)
    assert holdings[0].fund_code == "026790"


def test_parse_vlm_response_malformed_raises():
    with pytest.raises(ValueError):
        parse_vlm_response("抱歉我无法识别这张图片")


def test_extract_holdings_via_vlm_injects_completion():
    captured = {}

    def fake_completion(messages, settings):
        captured["messages"] = messages
        return '{"holdings":[{"fund_name":"某基金C","holding_amount":500.0}]}'

    holdings = extract_holdings_via_vlm(b"\x89PNG_fake", completion=fake_completion)
    assert holdings[0].fund_name == "某基金C"
    # 图片以 base64 data URL 形式进入 messages
    blob = str(captured["messages"])
    assert "image_url" in blob and "base64" in blob
