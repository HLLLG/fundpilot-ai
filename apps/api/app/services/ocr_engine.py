from __future__ import annotations

import os
import threading
from functools import lru_cache
from pathlib import Path

from app.config import get_settings


class OcrEngine:
    def extract_text(self, image_path: Path) -> str:
        os.environ.setdefault("DISABLE_MODEL_SOURCE_CHECK", "True")
        prepared = _prepare_image_for_ocr(image_path)
        result = _ocr_instance().predict(str(prepared))
        return _extract_lines(result)


def preload_ocr_engine() -> None:
    """后台预热 PaddleOCR，避免用户首次上传等待模型加载。"""
    os.environ.setdefault("DISABLE_MODEL_SOURCE_CHECK", "True")
    _ocr_instance()


def _prepare_image_for_ocr(image_path: Path) -> Path:
    """缩小超长边，加速检测且不影响列表截图识别率。"""
    settings = get_settings()
    max_side = max(640, settings.ocr_max_image_side)
    try:
        from PIL import Image
    except Exception:
        return image_path

    try:
        with Image.open(image_path) as image:
            width, height = image.size
            if max(width, height) <= max_side:
                return image_path
            scale = max_side / max(width, height)
            resized = image.resize(
                (max(1, int(width * scale)), max(1, int(height * scale))),
                Image.Resampling.LANCZOS,
            )
            if resized.mode not in ("RGB", "L"):
                resized = resized.convert("RGB")
            prepared = image_path.with_name(f"{image_path.stem}.ocr-prepared.jpg")
            resized.save(prepared, format="JPEG", quality=88, optimize=True)
            return prepared
    except Exception:
        return image_path


@lru_cache(maxsize=1)
def _ocr_instance():
    try:
        from paddleocr import PaddleOCR  # type: ignore[import-not-found]
    except Exception as exc:
        raise RuntimeError(
            "PaddleOCR 未安装或无法加载，请先使用手动文本输入，或按 README 安装可选 OCR 依赖。"
        ) from exc

    settings = get_settings()
    kwargs: dict = {
        "lang": "ch",
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_textline_orientation": False,
        "text_det_limit_side_len": min(settings.ocr_max_image_side, 1280),
    }
    if settings.ocr_use_mobile_models:
        kwargs.update(
            {
                "text_detection_model_name": "PP-OCRv4_mobile_det",
                "text_recognition_model_name": "PP-OCRv4_mobile_rec",
            }
        )
    return PaddleOCR(**kwargs)


_ocr_preload_lock = threading.Lock()
_ocr_preload_started = False


def schedule_ocr_preload() -> None:
    global _ocr_preload_started
    settings = get_settings()
    if not settings.ocr_preload:
        return
    with _ocr_preload_lock:
        if _ocr_preload_started:
            return
        _ocr_preload_started = True

    def _run() -> None:
        try:
            # 错开 AkShare 子进程预热，降低与 py_mini_racer 同进程竞态概率
            import time

            time.sleep(8)
            preload_ocr_engine()
        except Exception:
            pass

    threading.Thread(target=_run, name="ocr-preload", daemon=True).start()


def _extract_lines(result: object) -> str:
    lines: list[str] = []
    for page in result or []:  # type: ignore[operator]
        lines.extend(_extract_page_lines(page))
    return "\n".join(line for line in lines if line.strip())


def _extract_page_lines(page: object) -> list[str]:
    data = _page_to_mapping(page)
    if data:
        payload = data.get("res", data)
        rec_texts = payload.get("rec_texts") or payload.get("texts") or []
        return [str(text) for text in rec_texts]

    lines: list[str] = []
    if isinstance(page, list):
        for item in page:
            if isinstance(item, list | tuple) and len(item) >= 2 and item[1]:
                lines.append(str(item[1][0]))
    return lines


def _page_to_mapping(page: object) -> dict | None:
    if isinstance(page, dict):
        return page
    json_value = getattr(page, "json", None)
    if callable(json_value):
        json_value = json_value()
    if isinstance(json_value, dict):
        return json_value
    return None
