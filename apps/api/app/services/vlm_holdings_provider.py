from __future__ import annotations

import base64
import json
import re
from typing import Callable

import httpx

from app.config import Settings, get_settings
from app.models import Holding

VLM_EXTRACTION_PROMPT = (
    "你是基金持仓截图识别器。请从这张支付宝/养基宝「持有」截图中提取每一只**基金**的持仓。"
    "只输出 JSON，不要任何解释。格式："
    '{"holdings":[{"fund_name":"完整基金名","fund_code":"6位代码或null",'
    '"holding_amount":金额数字,"daily_profit":日收益或null,'
    '"holding_profit":持有收益或null,"cumulative_profit":累计收益或null,'
    '"holding_return_percent":持有收益率数字或null,"weight_percent":占比数字或null}]} 。'
    "规则：1)只提取带「基金」标签的行；跳过『余额宝/余额/现金/灵活取用』等货币基金行与底部法律声明、页眉/Tab。"
    "2)保留完整基金名（含 (QDII)、ETF联接、份额字母 A/B/C 等），不要拆词或翻译。"
    "3)亏损金额/收益率保留负号；看不到的字段填 null，不要编造。"
    "4)截图可能截不全（无表头或只有底部），仍尽量提取所有可见基金行。"
)

CompletionFn = Callable[[list[dict], Settings], str]

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _image_data_url(image_bytes: bytes) -> str:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:image/png;base64,{b64}"


def build_vlm_messages(image_bytes: bytes) -> list[dict]:
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": VLM_EXTRACTION_PROMPT},
                {"type": "image_url", "image_url": {"url": _image_data_url(image_bytes)}},
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
    messages = build_vlm_messages(image_bytes)
    content = run(messages, resolved)
    return parse_vlm_response(content)
