from __future__ import annotations

from datetime import date

from app.models import InvestorProfile

OUTPUT_DISCOVERY_REQUIREMENTS = """
你必须只输出一个 JSON 对象（不要 Markdown 代码块），字段：
- title: 报告标题
- summary: 2-4 句市场与配置总结
- market_view: 对大盘/板块的简短看法
- recommendations: 数组，3~5 项；每项含 fund_code, fund_name, sector_name, action,
  suggested_amount_yuan, amount_note, hold_horizon, confidence, points, risks, news_bullish
- caveats: 字符串数组，须含风险提示

约束：
- fund_code 必须来自 user 消息中 candidate_pool，禁止编造
- action 仅用：建议关注、分批买入、等待回调
- confidence 仅用：高、中、低
- hold_horizon 示例：2-4周、1-3个月、3-6个月
- 不得承诺收益；示意金额须结合 available_budget_yuan 与 concentration_limit_percent
"""


def build_user_payload(
    *,
    discovery_facts: dict,
    profile: InvestorProfile,
    focus_sectors: list[str],
) -> dict:
    pool = discovery_facts.get("candidate_pool") or []
    slim_pool = []
    for item in pool:
        slim_pool.append(
            {
                "fund_code": item.get("fund_code"),
                "fund_name": item.get("fund_name"),
                "sector_label": item.get("sector_label"),
                "return_1y_percent": item.get("return_1y_percent"),
                "max_drawdown_1y_percent": item.get("max_drawdown_1y_percent"),
                "fund_scale_yi": item.get("fund_scale_yi"),
                "selection_reason": item.get("selection_reason"),
            }
        )
    return {
        "today": date.today().isoformat(),
        "focus_sectors": focus_sectors,
        "profile": discovery_facts.get("profile") or profile.model_dump(mode="json"),
        "discovery_facts": {
            "portfolio_gap": discovery_facts.get("portfolio_gap"),
            "sector_heat": discovery_facts.get("sector_heat"),
            "market_flow": discovery_facts.get("market_flow"),
            "signal_backtest": discovery_facts.get("signal_backtest"),
            "news": discovery_facts.get("news"),
            "candidate_pool": slim_pool,
        },
        "requirements": [
            "仅从 candidate_pool 选 3~5 只",
            "结合 portfolio_gap 解释为何关注该板块/基金",
            "每只须含 hold_horizon 与 risks",
            "news_bullish 仅引用 news 中已有标题",
            "不得推荐用户已持有基金（见 portfolio_gap）",
        ],
    }


def append_output_requirements_to_system(system_prompt: str) -> str:
    return system_prompt.rstrip() + "\n\n" + OUTPUT_DISCOVERY_REQUIREMENTS.strip()
