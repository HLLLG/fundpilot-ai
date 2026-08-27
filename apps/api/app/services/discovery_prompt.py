from __future__ import annotations

from pydantic import BaseModel, Field

from app.services.analysis_prompt import (
    PromptContract,
    build_prompt_contract,
    normalize_role_prompt,
)

DEFAULT_DISCOVERY_ROLE_PROMPT = """你是个人基金投顾分析师，从 `discovery_facts.candidate_pool` 白名单精选 **0~4 只**未持有基金，每个板块最多 1 只。没有同时通过方向动作、基金质量、载体质量与板块身份门槛的候选时必须输出 0 只，禁止凑数、禁止编造池外代码。

## 扫描与口径

- `full_market`：先看 `sector_opportunities` / `target_sector_context` 定方向，再在方向内比基金；持仓只作去重与集中度背景
- `portfolio_gap`：优先补未重仓且热度靠前的缺口板块
- 只使用近 3/6 月收益、20/60 日净值趋势、`fund_entry_signal` 与研究用夏普；禁止使用近 1 年收益或近 1 年回撤
- `quality_gate=eligible` 才可分批买入；`watch_only` 只能观察/等待；`excluded` 禁止推荐
- `sector_identity_status=verified` 且 `sector_identity_eligible=true` 才可执行；`sector_fit_score` 只是排序分
- `sharpe_1y` / `sharpe_3y` 按天天基金特色数据口径自算，仅研究描述；缺失表示样本不足，不得当买入或否决门
- 同类分位与基准对比不在本轮输入，不要编造；载体质量以池内已有字段为准，缺失时不要据此否决
- 金额一律输出 `suggested_amount_yuan=null`，由服务端分配；不要编造申购状态、费率或平台可买性
- 今日涨跌：`official_nav` 可作主论据；`holdings_estimate` 写成「（重仓估算）」；`sector_estimate` 必须写成「（板块估算，截至 HH:MM）」

## 决策顺序

1. 看方向 `entry_state`：`ready_to_start` 且基金门槛通过 → 分批买入；`ready_on_pullback` 通常等待，除非基金 `entry_ready` 可替代结构项，或资金改善通道可缩小本次投入；`forming` 仅当 `probability_early_probe_eligible` 且基金早期信号通过时按 `first_tranche_scale` 试仓
2. 门内按 `opportunity_score_20_60d`、20 日波动弹性和 `fund_entry_signal` 排序，不要用更高质量分压过更强机会分
3. 买入必须写出可核验的失效/退出条件；只有结构化 `overheat_flags` 非空才能写追高

## 动作

- 建议关注 / 分批买入 / 等待回调（按 `waiting_reason_code` 区分资金、基金信号或结构修复）
"""

DISCOVERY_PROMPT_TEMPLATE_VERSION = "discovery_prompt.2026-08.v17"

DISCOVERY_FACTS_INSTRUCTION = (
    "数字只读。只从 candidate_pool 白名单选基金；等待/研究方向不得占推荐名额。"
    "suggested_amount_yuan 必须为 null。不要使用近1年收益或回撤，不要编造平台申购信息。"
    "sharpe_1y/sharpe_3y 仅研究描述，不得当买入或否决门。"
)


class DiscoveryPromptConfig(BaseModel):
    role_prompt: str = Field(default_factory=lambda: DEFAULT_DISCOVERY_ROLE_PROMPT)
    is_custom: bool = False
    default_role_prompt: str = Field(default=DEFAULT_DISCOVERY_ROLE_PROMPT)


def build_discovery_prompt_contract(value: str | None) -> PromptContract:
    # The immutable system template can be longer than the user-appendix limit.
    # Treat an exact round-trip of that template as the default before the
    # legacy field normalizer truncates user-provided text.
    normalized_value = value
    if value is not None and value.strip() == DEFAULT_DISCOVERY_ROLE_PROMPT.strip():
        normalized_value = None
    return build_prompt_contract(
        template_version=DISCOVERY_PROMPT_TEMPLATE_VERSION,
        template_snapshot=DEFAULT_DISCOVERY_ROLE_PROMPT,
        value=normalized_value,
    )


def resolve_discovery_role_prompt(value: str | None) -> str:
    return build_discovery_prompt_contract(value).effective_prompt


def build_prompt_config(stored_role_prompt: str | None) -> DiscoveryPromptConfig:
    normalized = normalize_role_prompt(stored_role_prompt)
    if normalized is None:
        return DiscoveryPromptConfig(
            role_prompt=DEFAULT_DISCOVERY_ROLE_PROMPT,
            is_custom=False,
            default_role_prompt=DEFAULT_DISCOVERY_ROLE_PROMPT,
        )
    return DiscoveryPromptConfig(
        role_prompt=normalized,
        is_custom=normalized != DEFAULT_DISCOVERY_ROLE_PROMPT,
        default_role_prompt=DEFAULT_DISCOVERY_ROLE_PROMPT,
    )
