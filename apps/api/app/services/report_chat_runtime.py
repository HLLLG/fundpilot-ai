from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.models import AnalysisMode


@dataclass(frozen=True)
class ReportChatRuntime:
    mode: AnalysisMode
    model: str
    news_tool_max_rounds: int


def resolve_report_chat_runtime(
    settings: Settings,
    mode: AnalysisMode = "fast",
) -> ReportChatRuntime:
    if mode == "fast":
        return ReportChatRuntime(
            mode="fast",
            model=settings.deepseek_model_fast,
            news_tool_max_rounds=0,
        )
    tool_rounds = settings.news_tool_max_rounds if settings.news_enabled else 0
    return ReportChatRuntime(
        mode="deep",
        model=settings.deepseek_model,
        news_tool_max_rounds=tool_rounds,
    )
