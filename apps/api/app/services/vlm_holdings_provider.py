from __future__ import annotations

import base64
import io
import json
import logging
import re
from typing import Callable

import httpx

from app.config import Settings, get_settings
from app.models import Holding

logger = logging.getLogger(__name__)

VLM_EXTRACTION_PROMPT = (
    "你是支付宝/养基宝「持有」截图的基金持仓识别器。请逐只提取截图里每一只**基金**的持仓数据，"
    "只输出 JSON，不要任何解释、不要 markdown 代码块。JSON 格式："
    '{"holdings":[{"fund_name":"完整基金名","fund_code":"6位代码或null",'
    '"holding_amount":金额数字,"daily_profit":日收益或null,'
    '"holding_profit":持有收益或null,"cumulative_profit":累计收益或null,'
    '"holding_return_percent":持有收益率数字或null,"weight_percent":占比数字或null}]}\n'
    "列布局（务必按列对位，不要错位）：每只基金一个区块——\n"
    "· 第一行是基金名称；其下一行是『基金/进阶理财/定投』等标签，忽略标签；\n"
    "· 数值行从左到右依次为：金额(holding_amount) → 日收益(daily_profit) → 持有收益(holding_profit) → 累计收益(cumulative_profit)；\n"
    "· 金额正下方的『占比 X.XX%』是 weight_percent；『持有收益』列正下方的百分比是 holding_return_percent。\n"
    "规则：\n"
    "1) 只提取带「基金」标签的行；必须跳过『余额宝/余额/现金/灵活取用』等货币基金或现金行；\n"
    "2) 跳过顶部页签(全部持有/收益明细/交易记录)、功能图标(清仓分析/收益地图/基金定投/专项计划)、排序控件(持有收益排序)、底部法律声明(本页面非任何法律文件…/该页面由蚂蚁财富…/以上按照持有收益排序)；\n"
    "3) 保留完整基金名（含 (QDII)/（QDII）、ETF联接、份额字母 A/B/C 等），不要拆词、缩写或翻译；\n"
    "4) 亏损（绿色减号）必须保留负号；金额/收益去掉千分位逗号转成数字；看不到或不确定的字段填 null，绝不编造；\n"
    "5) 截图可能截不全（无表头或只有底部片段），仍尽量提取所有可见基金行。"
)

CompletionFn = Callable[[list[dict], Settings], str]

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


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
                {"type": "text", "text": VLM_EXTRACTION_PROMPT},
            ],
        }
    ]


def parse_vlm_response(content: str) -> list[Holding]:
    if not content or not content.strip():
        raise ValueError("VLM 返回为空")
    text = content.strip()
    match = _JSON_OBJECT_RE.search(text)
    if not match:
        raise ValueError(f"VLM 返回非 JSON：{text[:120]}")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ValueError(f"VLM JSON 解析失败：{exc}") from exc

    rows = data.get("holdings") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("VLM 返回缺少 holdings 数组")

    holdings: list[Holding] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("fund_name") or "").strip()
        amount = row.get("holding_amount")
        if not name or amount is None:
            continue
        code = str(row.get("fund_code") or "").strip()
        if not re.fullmatch(r"\d{6}", code):
            code = "000000"
        ret = row.get("holding_return_percent")
        holdings.append(
            Holding(
                fund_code=code,
                fund_name=name,
                holding_amount=float(amount),
                return_percent=float(ret) if ret is not None else 0,
                daily_profit=_opt_float(row.get("daily_profit")),
                holding_profit=_opt_float(row.get("holding_profit")),
                holding_return_percent=_opt_float(ret),
            )
        )
    if not holdings:
        raise ValueError("VLM 未提取到任何基金持仓")
    return holdings


def _opt_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


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


def extract_holdings_via_vlm(
    image_bytes: bytes,
    *,
    settings: Settings | None = None,
    completion: CompletionFn | None = None,
) -> list[Holding]:
    resolved = settings or get_settings()
    run = completion or _dashscope_completion
    messages = build_vlm_messages(image_bytes, resolved)
    content = run(messages, resolved)
    return parse_vlm_response(content)
