from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from app.config import Settings, get_settings
from app.models import Holding
from app.services.ocr_text_service import OcrUnavailable, extract_text_from_image

logger = logging.getLogger(__name__)

TextFn = Callable[[bytes, Settings], tuple[str, bool]]


@dataclass
class ExtractionResult:
    holdings: list[Holding] = field(default_factory=list)
    ocr_source: str = "unknown"
    raw_text: str = ""
    provider: str = "vlm"
    cache_hit: bool = False


def extract_holdings(
    *,
    file_bytes: bytes | None,
    text: str,
    settings: Settings | None = None,
    text_fn: TextFn | None = None,
) -> ExtractionResult:
    """截图或文本 → 结构化持仓。

    识别只有云端 VLM 一条路：模型只做「图 → 纯文本」，结构化仍由
    `parse_holdings_from_text` 完成，所以粘贴文本和上传截图共享同一个解析器。
    之前的本地 PaddleOCR 兜底已删除——它比云端慢一个数量级（冷加载后仍 6~9s CPU 推理，
    生产未预热时更久），却在云端失败时静默接管，把一次「云端出错」变成一次超时。
    """
    resolved = settings or get_settings()

    if text:
        return _parse_text(text, provider="manual_text")

    if file_bytes is None:
        return ExtractionResult(provider="none")

    read_text = text_fn or _default_text_fn
    raw_text, cache_hit = read_text(file_bytes, resolved)
    result = _parse_text(raw_text, provider="vlm")
    return ExtractionResult(
        holdings=result.holdings,
        ocr_source=result.ocr_source,
        raw_text=result.raw_text,
        provider="vlm",
        cache_hit=cache_hit,
    )


def _parse_text(raw_text: str, *, provider: str) -> ExtractionResult:
    from app.services.ocr_parser import parse_holdings_from_text

    holdings = parse_holdings_from_text(raw_text)
    return ExtractionResult(
        holdings=holdings,
        ocr_source="alipay_holdings" if holdings else "unknown",
        raw_text=raw_text,
        provider=provider,
    )


def _default_text_fn(file_bytes: bytes, settings: Settings) -> tuple[str, bool]:
    return extract_text_from_image(file_bytes, settings=settings)


__all__ = ["ExtractionResult", "OcrUnavailable", "extract_holdings"]
