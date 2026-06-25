import io
from pathlib import Path

from PIL import Image

from app.config import Settings
from app.services.vlm_holdings_provider import (
    build_vlm_messages,
    compress_image_for_vlm,
    extract_holdings_via_vlm,
    extract_text_via_vlm,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _png_bytes(width: int, height: int, color=(200, 120, 60)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def test_extract_text_via_vlm_injects_completion():
    captured = {}

    def fake_completion(messages, settings):
        captured["messages"] = messages
        return "名称/金额\n某基金C\n1000.00"

    text = extract_text_via_vlm(_png_bytes(40, 40), completion=fake_completion)
    assert "某基金C" in text
    # 图片以 base64 data URL 形式进入 messages（纯 OCR，无 text prompt）
    blob = str(captured["messages"])
    assert "image_url" in blob and "base64" in blob


def test_extract_holdings_via_vlm_parses_real_ocr_text():
    """qwen-vl-ocr 文本 → 本地 parse_holdings_from_text 结构化（用真实 OCR 文本 fixture）。"""
    top_text = (FIXTURES / "alipay_holdings_top6_vlm_ocr.txt").read_text(encoding="utf-8")
    holdings, raw_text = extract_holdings_via_vlm(
        _png_bytes(40, 40), completion=lambda messages, settings: top_text
    )
    assert raw_text == top_text
    names = [h.fund_name for h in holdings]
    assert len(holdings) == 6, names
    grid = next(h for h in holdings if "电网设备" in h.fund_name)
    assert grid.holding_amount == 2000.01
    assert grid.holding_profit == 0.01  # 持有收益列对位正确（非累计 -5.71）
    assert any("(QDII)" in n for n in names)


def test_extract_holdings_via_vlm_skips_yuebao_in_bottom_fixture():
    bottom_text = (FIXTURES / "alipay_holdings_bottom2_vlm_ocr.txt").read_text(encoding="utf-8")
    holdings, _ = extract_holdings_via_vlm(
        _png_bytes(40, 40), completion=lambda messages, settings: bottom_text
    )
    names = [h.fund_name for h in holdings]
    assert len(holdings) == 2, names
    assert all("余额" not in n for n in names)
    zhonghang = next(h for h in holdings if "中航机遇" in h.fund_name)
    assert zhonghang.holding_amount == 9626.70
    assert zhonghang.holding_profit == -373.30  # 持有收益（非日收益 -391.90）


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


def test_build_vlm_messages_pixel_bounds_jpeg_and_no_text_prompt():
    s = Settings(vlm_ocr_min_pixels=3072, vlm_ocr_max_pixels=8388608)
    messages = build_vlm_messages(_png_bytes(800, 600), s)
    content = messages[0]["content"]
    image_part = next(p for p in content if p.get("type") == "image_url")
    assert image_part["min_pixels"] == 3072
    assert image_part["max_pixels"] == 8388608
    assert image_part["image_url"]["url"].startswith("data:image/jpeg;base64,")
    # 纯 OCR：不带 text prompt（避免触发文字定位/坐标输出）
    assert all(p.get("type") != "text" for p in content)
