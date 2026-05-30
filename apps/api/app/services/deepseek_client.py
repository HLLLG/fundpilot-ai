from __future__ import annotations

import json
from datetime import datetime

import httpx

from app.config import get_settings
from app.models import AnalysisRequest, FundSnapshot, MarketItem, Report, RiskAssessment


class DeepSeekClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    def generate_report(
        self,
        request: AnalysisRequest,
        risk: RiskAssessment,
        snapshots: list[FundSnapshot],
        market_context: list[MarketItem] | None = None,
    ) -> Report:
        if not self.settings.deepseek_api_key:
            return _offline_report(request, risk, snapshots, market_context or [])

        market_context = market_context or []
        payload = _build_payload(
            request,
            risk,
            snapshots,
            market_context,
            self.settings.deepseek_model,
            self.settings.deepseek_max_tokens,
        )
        try:
            response = httpx.post(
                f"{self.settings.deepseek_base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.settings.deepseek_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=httpx.Timeout(
                    connect=10,
                    read=self.settings.deepseek_timeout_seconds,
                    write=30,
                    pool=10,
                ),
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            parsed = _parse_model_json(content)
            fallback = _offline_report(request, risk, snapshots, market_context)
            return Report(
                title=parsed.get("title", "每日基金操作日报"),
                risk=risk,
                holdings=request.holdings,
                snapshots=snapshots,
                market_context=market_context,
                summary=parsed.get("summary") or fallback.summary,
                recommendations=_non_empty_list(
                    parsed.get("recommendations"),
                    fallback.recommendations,
                ),
                caveats=_non_empty_list(parsed.get("caveats"), fallback.caveats),
                provider=self.settings.deepseek_model,
            )
        except httpx.TimeoutException as exc:
            fallback = _offline_report(request, risk, snapshots, market_context or [])
            fallback.summary = (
                f"{fallback.summary}\n\nDeepSeek 调用超时：{exc}。"
                f"当前 read timeout 为 {self.settings.deepseek_timeout_seconds:.0f} 秒。"
                "可以调大 FUND_AI_DEEPSEEK_TIMEOUT_SECONDS，或将模型切换为 deepseek-v4-flash 提升速度。"
            )
            fallback.provider = "offline-fallback"
            return fallback
        except httpx.HTTPStatusError as exc:
            fallback = _offline_report(request, risk, snapshots, market_context or [])
            fallback.summary = (
                f"{fallback.summary}\n\nDeepSeek HTTP 错误：{exc.response.status_code} "
                f"{exc.response.text[:300]}"
            )
            fallback.provider = "offline-fallback"
            return fallback
        except Exception as exc:
            fallback = _offline_report(request, risk, snapshots, market_context or [])
            fallback.summary = f"{fallback.summary}\n\nDeepSeek 调用失败，已使用本地规则生成报告：{exc}"
            fallback.provider = "offline-fallback"
            return fallback


def _build_payload(
    request: AnalysisRequest,
    risk: RiskAssessment,
    snapshots: list[FundSnapshot],
    market_context: list[MarketItem],
    model: str,
    max_tokens: int,
) -> dict:
    system = (
        "你是个人基金投研助手，只能提供个人研究和风险提示，不能承诺收益。"
        "你必须结合持仓、当日收益、关联板块涨跌、组合集中度、基金净值快照和近期行业/市场消息做分析。"
        "如果没有实时新闻工具或基金代码缺失，必须明确说明信息缺口，并基于已知数据给出条件化操作方案。"
        "输出必须是 JSON，不要 Markdown。"
    )
    user = {
        "today": datetime.now().date().isoformat(),
        "profile": request.profile.model_dump(),
        "holdings": [holding.model_dump() for holding in request.holdings],
        "risk": risk.model_dump(),
        "fund_snapshots": [snapshot.model_dump() for snapshot in snapshots],
        "ocr_text": request.ocr_text,
        "market_context": [item.model_dump() for item in market_context],
        "requirements": [
            "输出 title、summary、recommendations、caveats 四个字段",
            "recommendations 至少 6 条，至少包含每只基金的动作建议",
            "recommendations 每条不超过 120 个中文字符，避免冗长",
            "每条建议必须包含：动作（观察/暂停加仓/分批加仓/减仓评估）、理由、触发条件、风险点",
            "重点使用养基宝指标：daily_profit、sector_name、sector_return_percent、return_percent、holding_amount",
            "如果 sector_return_percent 当日大涨但基金仍亏损，提示不要追涨，建议等待回落或分批",
            "如果单只持仓集中度超过阈值，优先提示仓位风险",
            "如果基金代码为 000000，说明需要补全代码才能获取净值和公告",
            "market_context 是需要你围绕近期公开消息重点核查的主题，请在建议中体现这些主题的消息面不确定性",
            "不要只给组合级结论，必须逐只基金输出",
            "偏稳健，避免追涨，不做实盘交易指令",
        ],
    }
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ],
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }


def _parse_model_json(content: str) -> dict:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {
            "title": "每日基金操作日报",
            "summary": content,
            "recommendations": [],
            "caveats": ["模型返回了非 JSON 内容，建议人工复核。"],
        }


def _non_empty_list(value: object, default: list[str]) -> list[str]:
    if isinstance(value, list) and value:
        return [str(item) for item in value]
    return default


def _offline_report(
    request: AnalysisRequest,
    risk: RiskAssessment,
    snapshots: list[FundSnapshot],
    market_context: list[MarketItem] | None = None,
) -> Report:
    recommendations = []
    if risk.suggested_action == "risk_review":
        recommendations.append("组合已触发风险复核线，今日不建议新增加仓，先检查亏损来源和持仓集中度。")
    else:
        recommendations.append("未触发硬性止损线，建议保持观察，只有在仓位低于计划上限时考虑小额定投。")

    for alert in risk.alerts:
        recommendations.append(alert.message)

    total_amount = sum(holding.holding_amount for holding in request.holdings) or 1
    for holding in request.holdings:
        weight = holding.holding_amount / total_amount * 100
        action = "观察"
        if weight > request.profile.concentration_limit_percent:
            action = "暂停加仓/减仓评估"
        elif holding.sector_return_percent is not None and holding.sector_return_percent > 5:
            action = "暂停追涨，等待回落后再分批"
        elif holding.return_percent < -5 and request.profile.prefer_dca:
            action = "小额分批观察，不一次性加仓"

        sector = holding.sector_name or "未知板块"
        daily = "-" if holding.daily_profit is None else f"{holding.daily_profit:.2f}"
        sector_change = (
            "-"
            if holding.sector_return_percent is None
            else f"{holding.sector_return_percent:.2f}%"
        )
        recommendations.append(
            f"{holding.fund_name}：{action}。当前占比 {weight:.1f}%，持有收益率 {holding.return_percent:.2f}%，"
            f"当日收益 {daily}，关联板块 {sector} 当日涨跌 {sector_change}。"
            "若后续补全基金代码，可结合净值、公告和同类基金表现再复核。"
        )

    if not recommendations:
        recommendations.append("当前信息不足以支持新增买入，建议等待净值、公告和市场信息更新。")

    return Report(
        title="每日基金操作日报",
        risk=risk,
        holdings=request.holdings,
        snapshots=snapshots,
        market_context=market_context or [],
        summary=(
            f"本地规则评估：组合加权收益率 {risk.weighted_return_percent:.2f}%，"
            f"风险等级为 {risk.level}。"
        ),
        recommendations=recommendations,
        caveats=[
            "本报告仅用于个人投研辅助，不构成投资建议。",
            "OCR、第三方数据和模型分析都可能出错，实际操作前请人工核对。",
        ],
    )
