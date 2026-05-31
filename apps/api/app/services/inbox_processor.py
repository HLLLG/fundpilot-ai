from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from app.config import get_settings
from app.database import get_ocr_text_cache, save_ocr_text_cache
from app.services.fund_profile import FundProfileService
from app.services.inbox_store import create_inbox_event
from app.services.ocr_engine import OcrEngine
from app.services.ocr_parser import parse_holdings_from_text

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def process_inbox_file(path: Path) -> dict | None:
    settings = get_settings()
    if path.suffix.lower() not in _IMAGE_SUFFIXES:
        return None

    file_bytes = path.read_bytes()
    cache_key = hashlib.sha256(file_bytes).hexdigest()
    text = get_ocr_text_cache(cache_key)
    error: str | None = None

    if text is None:
        try:
            text = OcrEngine().extract_text(path)
            save_ocr_text_cache(cache_key, text)
        except Exception as exc:
            error = f"OCR 识别失败：{exc}"
            text = ""

    holdings = []
    if not error:
        holdings = [
            holding.model_dump()
            for holding in FundProfileService().resolve_holdings(
                parse_holdings_from_text(text)
            )
        ]

    event = create_inbox_event(
        kind="ocr_ready",
        file_name=path.name,
        file_path=str(path),
        payload={
            "raw_text": text,
            "holdings": holdings,
            "error": error,
        },
        status="failed" if error and not holdings else "pending",
        error=error,
    )
    _archive_file(path, settings)
    return event


def _archive_file(path: Path, settings) -> None:
    processed_dir = settings.inbox_dir / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    target = processed_dir / path.name
    if target.exists():
        target = processed_dir / f"{path.stem}_{hashlib.sha256(path.read_bytes()).hexdigest()[:8]}{path.suffix}"
    shutil.move(str(path), str(target))
