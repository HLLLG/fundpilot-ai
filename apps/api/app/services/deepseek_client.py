from __future__ import annotations

import json

import httpx

from app.config import get_settings
from app.models import AnalysisRequest, FundSnapshot, Report, RiskAssessment


class DeepSeekClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    def generate_report(
        self,
        request: AnalysisRequest,
        risk: RiskAssessment,
        snapshots: list[FundSnapshot],
    ) -> Report:
        if not self.settings.deepseek_api_key:
            return _offline_report(request, risk, snapshots)

        payload = _build_payload(request, risk, snapshots, self.settings.deepseek_model)
        try:
            response = httpx.post(
                f"{self.settings.deepseek_base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.settings.deepseek_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=60,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            parsed = _parse_model_json(content)
            fallback = _offline_report(request, risk, snapshots)
            return Report(
                title=parsed.get("title", "每日基金操作日报"),
                risk=risk,
                holdings=request.holdings,
                snapshots=snapshots,
                summary=parsed.get("summary") or fallback.summary,
                recommendations=_non_empty_list(
                    parsed.get("recommendations"),
                    fallback.recommendations,
                ),
                caveats=_non_empty_list(parsed.get("caveats"), fallback.caveats),
                provider=self.settings.deepseek_model,
            )
        except Exception as exc:
            fallback = _offline_report(request, risk, snapshots)
            fallback.summary = f"{fallback.summary}\n\nDeepSeek 调用失败，已使用本地规则生成报告：{exc}"
            fallback.provider = "offline-fallback"
            return fallback


def _build_payload(
    request: AnalysisRequest,
    risk: RiskAssessment,
    snapshots: list[FundSnapshot],
    model: str,
) -> dict:
    system = (
        "你是个人基金投研助手，只能提供个人研究和风险提示，不能承诺收益。"
        "输出必须是 JSON，不要 Markdown。"
    )
    user = {
        "profile": request.profile.model_dump(),
        "holdings": [holding.model_dump() for holding in request.holdings],
        "risk": risk.model_dump(),
        "fund_snapshots": [snapshot.model_dump() for snapshot in snapshots],
        "requirements": [
            "给出观察、暂停加仓、分批加仓、减仓评估之一或组合建议",
            "必须说明触发规则和不确定性",
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
) -> Report:
    recommendations = []
    if risk.suggested_action == "risk_review":
        recommendations.append("组合已触发风险复核线，今日不建议新增加仓，先检查亏损来源和持仓集中度。")
    else:
        recommendations.append("未触发硬性止损线，建议保持观察，只有在仓位低于计划上限时考虑小额定投。")

    for alert in risk.alerts:
        recommendations.append(alert.message)

    if not recommendations:
        recommendations.append("当前信息不足以支持新增买入，建议等待净值、公告和市场信息更新。")

    return Report(
        title="每日基金操作日报",
        risk=risk,
        holdings=request.holdings,
        snapshots=snapshots,
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
