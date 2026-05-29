from __future__ import annotations

from pathlib import Path


class OcrEngine:
    def extract_text(self, image_path: Path) -> str:
        try:
            from paddleocr import PaddleOCR  # type: ignore[import-not-found]
        except Exception as exc:
            raise RuntimeError(
                "PaddleOCR 未安装或无法加载，请先使用手动文本输入，或按 README 安装可选 OCR 依赖。"
            ) from exc

        ocr = PaddleOCR(use_angle_cls=True, lang="ch")
        result = ocr.ocr(str(image_path), cls=True)
        lines: list[str] = []
        for page in result or []:
            for item in page or []:
                if len(item) >= 2 and item[1]:
                    lines.append(str(item[1][0]))
        return "\n".join(lines)
