from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.config import Settings


AnalysisMode = Literal["fast", "deep"]


@dataclass(frozen=True)
class AnalysisRuntime:
    mode: AnalysisMode
    model: str
    news_enabled: bool
    news_max_topics: int
    news_tool_max_rounds: int


def resolve_analysis_runtime(settings: Settings, mode: AnalysisMode = "deep") -> AnalysisRuntime:
    if mode == "fast":
        return AnalysisRuntime(
            mode="fast",
            model="deepseek-v4-flash",
            news_enabled=settings.news_enabled,
            news_max_topics=min(3, settings.news_max_topics),
            news_tool_max_rounds=0,
        )
    return AnalysisRuntime(
        mode="deep",
        model=settings.deepseek_model,
        news_enabled=settings.news_enabled,
        news_max_topics=settings.news_max_topics,
        news_tool_max_rounds=settings.news_tool_max_rounds,
    )
