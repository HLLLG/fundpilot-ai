from __future__ import annotations

from app.models import Holding, MarketItem


class MarketContextService:
    def collect(self, holdings: list[Holding]) -> list[MarketItem]:
        seen: set[str] = set()
        items: list[MarketItem] = []

        for holding in holdings:
            topics = [holding.sector_name, _keyword_from_name(holding.fund_name)]
            for topic in topics:
                if not topic or topic in seen:
                    continue
                seen.add(topic)
                items.append(
                    MarketItem(
                        topic=topic,
                        query=f"{topic} 最新 政策 资金流 估值 风险",
                        source="search-plan",
                        note="请结合近期公开消息、政策变化、资金面和板块走势复核该主题。",
                    )
                )

        return items


def _keyword_from_name(name: str) -> str | None:
    cleaned = name.replace("...", "").replace(".", "").strip()
    for token in ("人工智能", "电网设备", "半导体", "国防军工", "商业航天"):
        if token in cleaned:
            return token
    return cleaned or None
