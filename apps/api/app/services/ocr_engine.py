from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


class OcrEngine:
    def extract_text(self, image_path: Path) -> str:
        os.environ.setdefault("DISABLE_MODEL_SOURCE_CHECK", "True")
        result = _ocr_instance().predict(str(image_path))
        return _extract_lines(result)


@lru_cache(maxsize=1)
def _ocr_instance():
    try:
        from paddleocr import PaddleOCR  # type: ignore[import-not-found]
    except Exception as exc:
        raise RuntimeError(
            "PaddleOCR 未安装或无法加载，请先使用手动文本输入，或按 README 安装可选 OCR 依赖。"
        ) from exc

    return PaddleOCR(
        lang="ch",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )


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

    # Backward-compatible fallback for PaddleOCR 2.x-style nested results.
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
