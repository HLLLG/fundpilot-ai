"""截图 → OCR 文本的唯一入口：云端 VLM + 按图内容哈希的文本缓存。

持仓截图（`/api/ocr`）和交易记录截图（`/api/transactions/ocr`）以前各走一套：前者
VLM 优先、本地 PaddleOCR 兜底，后者**只**走本地 PaddleOCR。生产镜像里本地引擎又是
冷启动（`FUND_AI_OCR_PRELOAD=false`），于是交易记录识别要先加载模型再 CPU 推理，
稳定超过前端 60s 超时，用户看到的就是 TimeoutError。

现在两条链路共用这里：同一个云端识别、同一份缓存、同一套错误语义。识别不可用时抛
`OcrUnavailable`，由端点转成用户能看懂的提示，而不是静默退化成一个更慢的引擎。
"""
from __future__ import annotations

import hashlib
import logging

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class OcrUnavailable(RuntimeError):
    """云端截图识别不可用（未配置 API key）。"""


def ocr_cache_key(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()


def cloud_ocr_configured(settings: Settings | None = None) -> bool:
    resolved = settings or get_settings()
    return bool(resolved.vlm_ocr_api_key)


def extract_text_from_image(
    file_bytes: bytes,
    *,
    settings: Settings | None = None,
    use_cache: bool = True,
) -> tuple[str, bool]:
    """返回 ``(OCR 文本, 是否命中缓存)``。

    缓存键是图片字节的 sha256，所以同一张截图重复上传（用户重试、确认页返回重传）
    直接命中，不再花钱也不再等网络。缓存读写失败只记日志：它是加速手段，不是识别前提。
    """
    resolved = settings or get_settings()
    if not cloud_ocr_configured(resolved):
        raise OcrUnavailable(
            "云端截图识别未配置（FUND_AI_VLM_OCR_API_KEY 为空），请改用手动输入持仓。"
        )

    key = ocr_cache_key(file_bytes)
    if use_cache:
        cached = _read_cache(key)
        if cached:
            return cached, True

    from app.services.vlm_holdings_provider import extract_text_via_vlm

    text = extract_text_via_vlm(file_bytes, settings=resolved)
    if use_cache and text:
        _write_cache(key, text)
    return text, False


def _read_cache(key: str) -> str | None:
    from app.database import get_ocr_text_cache

    try:
        return get_ocr_text_cache(key)
    except Exception:  # noqa: BLE001 — 缓存不可用不该挡住识别
        logger.warning("读取 OCR 文本缓存失败", exc_info=True)
        return None


def _write_cache(key: str, text: str) -> None:
    from app.database import save_ocr_text_cache

    try:
        save_ocr_text_cache(key, text)
    except Exception:  # noqa: BLE001
        logger.warning("写入 OCR 文本缓存失败", exc_info=True)
