from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from app.config import Settings, get_settings
from app.models import Holding

logger = logging.getLogger(__name__)

VlmFn = Callable[[bytes, Settings], list[Holding]]
LocalFn = Callable[[bytes | None, str], tuple[list[Holding], str]]


@dataclass
class ExtractionResult:
    holdings: list[Holding] = field(default_factory=list)
    ocr_source: str = "unknown"
    raw_text: str = ""
    provider: str = "local"


def _default_local_fn(file_bytes: bytes | None, text: str) -> tuple[list[Holding], str]:
    from app.services.ocr_engine import OcrEngine
    from app.services.ocr_parser import parse_holdings_from_text

    raw_text = text
    if not raw_text and file_bytes is not None:
        from app.config import get_settings as _gs

        upload_dir = _gs().upload_dir
        upload_dir.mkdir(parents=True, exist_ok=True)
        tmp = upload_dir / "vlm-local-tmp.png"
        tmp.write_bytes(file_bytes)
        try:
            raw_text = OcrEngine().extract_text(tmp)
        finally:
            for p in (tmp, tmp.with_name(f"{tmp.stem}.ocr-prepared.jpg")):
                try:
                    p.unlink()
                except OSError:
                    pass
    return parse_holdings_from_text(raw_text), raw_text


def extract_holdings(
    *,
    file_bytes: bytes | None,
    text: str,
    settings: Settings | None = None,
    vlm_fn: VlmFn | None = None,
    local_fn: LocalFn | None = None,
) -> ExtractionResult:
    resolved = settings or get_settings()
    local = local_fn or _default_local_fn

    def run_local() -> ExtractionResult:
        holdings, raw_text = local(file_bytes, text)
        return ExtractionResult(
            holdings=holdings,
            ocr_source="alipay_holdings" if holdings else "unknown",
            raw_text=raw_text,
            provider="local",
        )

    # 手动文本 / 无图片 / 强制本地 / 无 key → 本地
    use_vlm = (
        file_bytes is not None
        and not text
        and resolved.ocr_provider in ("auto", "vlm")
        and bool(resolved.vlm_ocr_api_key)
    )
    if not use_vlm:
        return run_local()

    vlm = vlm_fn or _default_vlm_fn
    try:
        holdings = vlm(file_bytes, resolved)
        if not holdings:
            raise ValueError("VLM 返回空持仓")
        return ExtractionResult(
            holdings=holdings,
            ocr_source="alipay_holdings",
            raw_text="",
            provider="vlm",
        )
    except Exception:  # noqa: BLE001 — 云端失败软回退本地，绝不冒泡
        logger.warning("VLM 识别失败，回退本地 OCR", exc_info=True)
        return run_local()


def _default_vlm_fn(file_bytes: bytes, settings: Settings) -> list[Holding]:
    from app.services.vlm_holdings_provider import extract_holdings_via_vlm

    return extract_holdings_via_vlm(file_bytes, settings=settings)
