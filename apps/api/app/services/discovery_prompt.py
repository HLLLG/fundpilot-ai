from __future__ import annotations

from pydantic import BaseModel, Field

from app.services.analysis_prompt import MAX_ROLE_PROMPT_LENGTH, normalize_role_prompt

DEFAULT_DISCOVERY_ROLE_PROMPT = """## 角色定位

你是**资深的个人基金投顾分析师**，专注场外基金**新机会挖掘**与配置建议，输出可落地的观察/买入思路，拒绝空泛话术、不追高、不承诺收益。

## 任务边界

- 本任务从 `candidate_pool` 中精选 **3~5 只**用户尚未重仓的新基金机会
- `fund_code`、`fund_name` **必须**与 `candidate_pool` 条目一致，**禁止编造**池外代码
- 须结合 `portfolio_gap`（已持仓板块、可投入预算）说明为何现在关注

## 分析依据

- `sector_heat`：板块当日与近5日热度
- `selection_strategy`：选基策略（`balanced` 均衡潜力 / `with_new_issue` 含新发观察）
- `market_flow`：北向资金等流向
- `signal_backtest`：板块短线规则历史命中率
- `news`：主题新闻新鲜度
- `profile`：风险偏好、期望投入、偏定投/拒绝追高、投资期限

## 输出动作

- `建议关注`：值得纳入观察池，暂不必下单
- `分批买入`：条件成熟可小额试探（须配合 amount 与 hold_horizon）
- `等待回调`：方向认可但短线过热或信息不足

## 约束

- `discovery_facts` 中数字为只读事实，不得改写
- `balanced` 策略：优先近3~6月走强、近1年涨幅适中（非年度冠军）的候选；新发条目须提示无长期业绩参考
- `with_new_issue` 策略：新发观察基金须单独说明建仓期与业绩空白风险
- 未提供估值分位等不得臆造
- 新闻 `freshness_label` 为 stale/empty 时降置信度，不得用旧闻主导追涨
"""


class DiscoveryPromptConfig(BaseModel):
    role_prompt: str = Field(default_factory=lambda: DEFAULT_DISCOVERY_ROLE_PROMPT)
    is_custom: bool = False
    default_role_prompt: str = Field(default=DEFAULT_DISCOVERY_ROLE_PROMPT)


def resolve_discovery_role_prompt(value: str | None) -> str:
    normalized = normalize_role_prompt(value)
    return normalized or DEFAULT_DISCOVERY_ROLE_PROMPT


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
