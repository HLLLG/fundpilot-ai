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
    # Compatibility field for the dormant autonomous tool helper.  Main report
    # generation deliberately keeps this at zero until citation validation can
    # prove every tool-sourced claim.  The configured value is recorded
    # separately so observability never reports configuration as execution.
    news_tool_max_rounds: int
    news_tool_rounds_configured: int = 0
    news_retrieval_policy: str = "bounded_prefetch.v1"

    @property
    def news_tool_rounds_executed(self) -> int:
        """Return autonomous news rounds executed by the main report path."""

        return 0


def limit_news_topics_for_runtime(
    topics: list[str],
    runtime: AnalysisRuntime,
) -> list[str]:
    """Apply the selected mode's truthful topic budget to every runner."""

    unique = [topic for topic in dict.fromkeys(topics) if str(topic).strip()]
    return unique[: max(0, int(runtime.news_max_topics))]


def resolve_analysis_runtime(settings: Settings, mode: AnalysisMode = "deep") -> AnalysisRuntime:
    if mode == "fast":
        return AnalysisRuntime(
            mode="fast",
            model=settings.deepseek_model_fast,
            news_enabled=settings.news_enabled,
            news_max_topics=min(3, settings.news_max_topics),
            news_tool_max_rounds=0,
            news_tool_rounds_configured=0,
        )
    return AnalysisRuntime(
        mode="deep",
        model=settings.deepseek_model,
        news_enabled=settings.news_enabled,
        news_max_topics=settings.news_max_topics,
        news_tool_max_rounds=0,
        news_tool_rounds_configured=(
            settings.news_tool_max_rounds if settings.news_enabled else 0
        ),
    )
