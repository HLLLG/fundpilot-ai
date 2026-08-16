from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.models import AnalysisMode
from app.services.chat_agent_tools import CHAT_AGENT_TOOL_MAX_ROUNDS


@dataclass(frozen=True)
class ReportChatRuntime:
    mode: AnalysisMode
    model: str
    news_tool_max_rounds: int
    agent_tool_max_rounds: int = 0


def resolve_report_chat_runtime(
    settings: Settings,
    mode: AnalysisMode = "fast",
) -> ReportChatRuntime:
    if mode == "fast":
        return ReportChatRuntime(
            mode="fast",
            model=settings.deepseek_model_fast,
            news_tool_max_rounds=0,
            agent_tool_max_rounds=0,
        )
    tool_rounds = settings.news_tool_max_rounds if settings.news_enabled else 0
    return ReportChatRuntime(
        mode="deep",
        model=settings.deepseek_model,
        news_tool_max_rounds=tool_rounds,
        agent_tool_max_rounds=CHAT_AGENT_TOOL_MAX_ROUNDS,
    )
