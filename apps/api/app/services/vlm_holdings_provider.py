from __future__ import annotations

import base64
import io
import logging
from typing import Callable

import httpx

from app.config import Settings, get_settings
from app.models import Holding

logger = logging.getLogger(__name__)

# qwen-vl-ocr 是文字识别专用模型：擅长「图→文本」，但做不了支付宝多列+纵向错位的字段归属推理
# （让它直接吐结构化 JSON 会把金额/收益/占比错位、把数字当基金名）。因此本 provider 只让它做
# 高质量纯文本识别（不传 prompt 用模型默认 OCR；传自定义「阅读顺序」prompt 反而会触发文字定位/坐标
# 输出），再交给久经测试的本地 `parse_holdings_from_text` 做结构化——与本地 PaddleOCR 路径同一解析器。

CompletionFn = Callable[[list[dict], Settings], str]


def _guess_image_mime(image_bytes: bytes) -> str:
    """按 magic bytes 粗判图片 MIME；无法判别回退 image/png（与历史行为一致）。"""
    if image_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def compress_image_for_vlm(image_bytes: bytes, settings: Settings) -> tuple[bytes, str]:
    """上传前压缩：转 JPEG，仅当最长边超阈值才等比缩小。

    返回 (处理后字节, MIME)。任何异常（非法图片字节 / Pillow 缺失等）都回退原图，绝不抛出。
    注意：压缩只减小上传体积/延迟，不改变 token 消耗（token 由 max_pixels 控制）。
    """
    if not settings.vlm_ocr_compress_enabled:
        return image_bytes, _guess_image_mime(image_bytes)
    try:
        from PIL import Image

        with Image.open(io.BytesIO(image_bytes)) as img:
            img = img.convert("RGB")
            max_side = settings.vlm_ocr_max_image_side
            if max_side and max_side > 0 and max(img.size) > max_side:
                img.thumbnail((max_side, max_side), Image.LANCZOS)
            out = io.BytesIO()
            img.save(
                out,
                format="JPEG",
                quality=settings.vlm_ocr_jpeg_quality,
                optimize=True,
            )
            return out.getvalue(), "image/jpeg"
    except Exception:  # noqa: BLE001 — 压缩失败不应影响识别，回退原图
        logger.warning("VLM 图片压缩失败，使用原图", exc_info=True)
        return image_bytes, _guess_image_mime(image_bytes)


def _image_data_url(image_bytes: bytes, mime: str) -> str:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{b64}"


def build_vlm_messages(image_bytes: bytes, settings: Settings) -> list[dict]:
    """仅图片、不带文本 prompt → qwen-vl-ocr 走默认纯文本识别（最稳，输出干净文本）。"""
    data, mime = compress_image_for_vlm(image_bytes, settings)
    return [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": _image_data_url(data, mime)},
                    # qwen-vl-ocr：min/max_pixels 作为 image_url 同级字段控制缩放与 token 上限
                    "min_pixels": settings.vlm_ocr_min_pixels,
                    "max_pixels": settings.vlm_ocr_max_pixels,
                },
            ],
        }
    ]


def _dashscope_completion(messages: list[dict], settings: Settings) -> str:
    if not settings.vlm_ocr_api_key:
        raise RuntimeError("VLM OCR API key 未配置")
    url = f"{settings.vlm_ocr_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.vlm_ocr_api_key}",
        "Content-Type": "application/json",
    }
    payload = {"model": settings.vlm_ocr_model, "messages": messages}
    timeout = httpx.Timeout(
        connect=10, read=settings.vlm_ocr_timeout_seconds, write=30, pool=10
    )
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    return data["choices"][0]["message"]["content"]


def extract_text_via_vlm(
    image_bytes: bytes,
    *,
    settings: Settings | None = None,
    completion: CompletionFn | None = None,
) -> str:
    """图片 → qwen-vl-ocr 默认纯文本识别，返回 OCR 文本。"""
    resolved = settings or get_settings()
    run = completion or _dashscope_completion
    messages = build_vlm_messages(image_bytes, resolved)
    content = run(messages, resolved)
    if not content or not content.strip():
        raise ValueError("VLM 返回为空文本")
    return content


def extract_holdings_via_vlm(
    image_bytes: bytes,
    *,
    settings: Settings | None = None,
    completion: CompletionFn | None = None,
) -> tuple[list[Holding], str]:
    """图片 → qwen-vl-ocr 文本 → 本地解析器结构化。返回 (holdings, ocr_text)。"""
    from app.services.ocr_parser import parse_holdings_from_text

    text = extract_text_via_vlm(image_bytes, settings=settings, completion=completion)
    holdings = parse_holdings_from_text(text)
    return holdings, text
