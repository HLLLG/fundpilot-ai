from __future__ import annotations

import httpx

from app.config import Settings, get_settings


def deepseek_chat_url(settings: Settings | None = None) -> str:
    resolved = settings or get_settings()
    return f"{resolved.deepseek_base_url.rstrip('/')}/chat/completions"


def deepseek_request_headers(settings: Settings | None = None) -> dict[str, str]:
    resolved = settings or get_settings()
    api_key = resolved.deepseek_api_key
    if not api_key:
        raise RuntimeError("DeepSeek API key is not configured")
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def deepseek_timeout(settings: Settings | None = None) -> httpx.Timeout:
    resolved = settings or get_settings()
    return httpx.Timeout(
        connect=10,
        read=resolved.deepseek_timeout_seconds,
        write=30,
        pool=10,
    )


def format_deepseek_http_error(exc: httpx.HTTPStatusError) -> str:
    status = exc.response.status_code
    if status == 401:
        return (
            "DeepSeek API 认证失败（401）：请检查项目根目录 `.env` 中的 "
            "`FUND_AI_DEEPSEEK_API_KEY` 是否为控制台复制的真实 Key（不是 "
            "`.env.example` 里的 sk-your-deepseek-key 占位符）。修改后需重启 API。"
        )
    if status == 402:
        return "DeepSeek 账户余额不足（402），请充值后再试。"
    if status == 429:
        return "DeepSeek 请求过于频繁（429），请稍后再试。"
    body = exc.response.text[:200].strip()
    suffix = f" 响应：{body}" if body else ""
    return f"DeepSeek 请求失败（HTTP {status}）{suffix}"
