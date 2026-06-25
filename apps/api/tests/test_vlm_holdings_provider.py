import io

import pytest
from PIL import Image

from app.config import Settings
from app.services.vlm_holdings_provider import (
    build_vlm_messages,
    compress_image_for_vlm,
    extract_holdings_via_vlm,
    parse_vlm_response,
)


def _png_bytes(width: int, height: int, color=(200, 120, 60)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


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


def test_compress_image_converts_to_jpeg_and_downscales():
    s = Settings(vlm_ocr_compress_enabled=True, vlm_ocr_max_image_side=2000)
    data, mime = compress_image_for_vlm(_png_bytes(3000, 100), s)
    assert mime == "image/jpeg"
    img = Image.open(io.BytesIO(data))
    assert img.format == "JPEG"
    assert max(img.size) <= 2000  # 最长边 3000 → 缩到 ≤2000


def test_compress_image_keeps_small_image_dimensions():
    s = Settings(vlm_ocr_compress_enabled=True, vlm_ocr_max_image_side=2000)
    data, mime = compress_image_for_vlm(_png_bytes(800, 600), s)
    assert mime == "image/jpeg"
    img = Image.open(io.BytesIO(data))
    assert img.size == (800, 600)  # 未超阈值不缩放


def test_compress_image_disabled_returns_original_bytes():
    raw = _png_bytes(800, 600)
    s = Settings(vlm_ocr_compress_enabled=False)
    data, mime = compress_image_for_vlm(raw, s)
    assert data == raw
    assert mime == "image/png"


def test_compress_image_invalid_bytes_falls_back_to_original():
    s = Settings(vlm_ocr_compress_enabled=True)
    data, mime = compress_image_for_vlm(b"\x89PNG_fake", s)
    assert data == b"\x89PNG_fake"
    assert mime == "image/png"


def test_build_vlm_messages_includes_pixel_bounds_and_jpeg_mime():
    s = Settings(vlm_ocr_min_pixels=3072, vlm_ocr_max_pixels=8388608)
    messages = build_vlm_messages(_png_bytes(800, 600), s)
    content = messages[0]["content"]
    image_part = next(p for p in content if p.get("type") == "image_url")
    assert image_part["min_pixels"] == 3072
    assert image_part["max_pixels"] == 8388608
    assert image_part["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert any(p.get("type") == "text" for p in content)
