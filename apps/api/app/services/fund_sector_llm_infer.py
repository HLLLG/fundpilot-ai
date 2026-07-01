"""LLM 兜底：规则/白名单都推不出主题标签时，用 DeepSeek 给基金一个规范化短标签。

设计目的：新上传/生僻基金不必靠持续扩容白名单才能匹配到关联板块——只在所有规则
路径（缓存/语义名称/业绩基准/持仓穿透）都失败后才触发，且只在"愿意花网络时间"的
慢路径（如手动精确刷新、全市场离线预计算）里调用；结果按基金代码全局缓存
（fund_primary_sectors_global，source="llm_infer"/"precompute_llm"），每只基金
理论上只需成功分类一次。任何异常（网络、超时、JSON 解析）都静默返回 None，
绝不抛出到调用方，避免把整条持仓加载流程拖垮。
"""

from __future__ import annotations

import json
import logging

import httpx

from app.config import get_settings
from app.services.deepseek_http import deepseek_chat_url, deepseek_request_headers
from app.services.sector_labels import is_generic_style_phrase, normalize_sector_label

logger = logging.getLogger(__name__)

_LLM_READ_TIMEOUT_SECONDS = 25.0
_MIN_LABEL_LEN = 2
_MAX_LABEL_LEN = 10
_MIN_CONFIDENCE = 0.3
_MAX_CONFIDENCE = 0.7
_MAX_HOLDINGS_IN_PROMPT = 8
# deepseek_model_fast 是带思维链的推理模型：先输出 reasoning_content 再输出最终 JSON，
# max_tokens 太小会导致思考过程本身就把配额耗完，content 变成空字符串（曾实测 128 会截断）。
_MAX_OUTPUT_TOKENS = 700

_SYSTEM_PROMPT = (
    "你是基金分类助手。给定一只基金的名称（可能附带业绩比较基准原文、前几大重仓股名称），"
    "判断它最贴切的「关联板块/主题」短标签，用于展示在基金持仓列表的关联板块一列。\n"
    "要求：\n"
    "1. sector_name 必须是 2-10 个汉字的具体主题/行业/赛道短语，例如"
    "「半导体」「全球高端制造」「科创芯片设计」「机器人」「CPO」「光通信」。\n"
    "2. 不能是投资风格/策略描述词，也不能是基金公司名或基金自身的营销短语"
    "（例如「机遇领航」「稳健回报」「精选成长」都不是主题）。\n"
    "3. 如果基金名称/业绩基准本身没有主题线索，优先结合前几大重仓股名称判断它们所属的"
    "共同行业/赛道（例如重仓股都是光模块厂商，就应该判断为「CPO」或「光通信」，而不是"
    "返回 null 或瞎编基金名称里的词）。\n"
    "4. 如果基金投向确实宽泛、没有明确主题（全市场混合基金、纯债基金、货币基金等，且"
    "重仓股行业也很分散），sector_name 返回 null，不要勉强编造。\n"
    "5. 只输出合法 JSON：{\"sector_name\": string|null, \"confidence\": number}，"
    "confidence 取 0~1 之间小数，不要输出任何多余文字或 Markdown。"
)


def infer_sector_via_llm(
    fund_code: str,
    fund_name: str | None,
    *,
    benchmark_text: str | None = None,
    top_holdings: list[str] | None = None,
) -> tuple[str, float] | None:
    """返回 (sector_name, confidence)；任何失败都静默返回 None。

    ``top_holdings`` 传入前几大重仓股名称时，能在基金名称本身没有主题线索的情况下
    （如"中航机遇领航混合发起C"实际重仓光模块股），借助 LLM 对上市公司的常识判断出
    合理主题，而不必依赖脆弱的东财个股行业接口或不断扩容的关键词白名单。
    """
    settings = get_settings()
    if not settings.fund_primary_sector_llm_infer_enabled:
        return None
    if not settings.deepseek_configured:
        return None
    name = (fund_name or "").strip()
    holdings = [h.strip() for h in (top_holdings or []) if h and h.strip()][:_MAX_HOLDINGS_IN_PROMPT]
    if not name and not holdings:
        return None

    user_payload = {
        "fund_code": fund_code,
        "fund_name": name or None,
        "benchmark_text": ((benchmark_text or "").strip()[:200] or None),
        "top_holdings": holdings or None,
    }
    request_payload = {
        "model": settings.deepseek_model_fast,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        "temperature": 0.1,
        "max_tokens": _MAX_OUTPUT_TOKENS,
        "response_format": {"type": "json_object"},
    }
    timeout = httpx.Timeout(connect=8, read=_LLM_READ_TIMEOUT_SECONDS, write=10, pool=8)

    try:
        response = httpx.post(
            deepseek_chat_url(settings),
            headers=deepseek_request_headers(settings),
            json=request_payload,
            timeout=timeout,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"].get("content") or "{}"
        parsed = json.loads(content)
    except Exception:
        logger.info("llm sector infer failed for %s (%s)", fund_code, name, exc_info=True)
        return None

    if not isinstance(parsed, dict):
        return None
    return _parse_llm_result(parsed)


def _parse_llm_result(parsed: dict) -> tuple[str, float] | None:
    raw_label = parsed.get("sector_name")
    if not raw_label or not isinstance(raw_label, str):
        return None
    label = normalize_sector_label(raw_label)
    if not (_MIN_LABEL_LEN <= len(label) <= _MAX_LABEL_LEN):
        return None
    if is_generic_style_phrase(label):
        return None

    try:
        confidence = float(parsed.get("confidence") if parsed.get("confidence") is not None else 0.5)
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(_MIN_CONFIDENCE, min(_MAX_CONFIDENCE, confidence))
    return label, confidence
