import json

import httpx

from app.models import AnalysisRequest, Holding, InvestorProfile, NewsItem, RiskAssessment
from app.services.deepseek_client import DeepSeekClient, _execute_fetch_market_news


def test_execute_fetch_market_news(monkeypatch):
    def fake_search(self, topic: str, limit: int | None = None):
        return [
            NewsItem(
                topic=topic,
                title="测试新闻",
                published_at="2026-05-30",
                source="eastmoney",
            )
        ]

    from app.services import deepseek_client
    from app.services.news_service import NewsService

    monkeypatch.setattr(NewsService, "search", fake_search)
    collected: list[NewsItem] = []
    payload = _execute_fetch_market_news(
        {
            "id": "call_1",
            "function": {
                "name": "fetch_market_news",
                "arguments": json.dumps({"topic": "半导体", "limit": 3}),
            },
        },
        deepseek_client.DeepSeekClient().news_service,
        collected,
    )
    data = json.loads(payload)
    assert data["count"] == 1
    assert collected[0].title == "测试新闻"


def test_generate_with_tools_parses_final_json(monkeypatch):
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", "test-key")
    from app.config import refresh_settings

    refresh_settings()

    responses = [
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "fetch_market_news",
                                    "arguments": '{"topic":"电网设备","limit":2}',
                                },
                            }
                        ],
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "title": "带新闻的日报",
                                "summary": "已结合新闻分析",
                                "fund_recommendations": [
                                    {
                                        "fund_code": "015608",
                                        "fund_name": "测试基金",
                                        "action": "观察",
                                        "news_bullish": ["电网设备走强（2026-05-30）"],
                                        "news_bearish": [],
                                        "points": ["收盘前维持观察"],
                                    }
                                ],
                                "caveats": ["仅供参考"],
                            },
                            ensure_ascii=False,
                        ),
                    }
                }
            ]
        },
    ]

    def fake_post(url, **kwargs):
        payload = kwargs["json"]
        if payload.get("tools"):
            body = responses[0]
        else:
            body = responses[1]
        request = httpx.Request("POST", url)
        return httpx.Response(200, json=body, request=request)

    monkeypatch.setattr(httpx, "post", fake_post)

    def fake_search(self, topic: str, limit: int | None = None):
        return [NewsItem(topic=topic, title=f"{topic}新闻")]

    from app.services.news_service import NewsService

    monkeypatch.setattr(NewsService, "search", fake_search)

    prefetched = [NewsItem(topic="电网设备", title="电网设备新闻", is_today=True)]
    client = DeepSeekClient()
    parsed, news = client._generate_with_tools(
        AnalysisRequest(
            holdings=[
                Holding(
                    fund_code="015608",
                    fund_name="测试基金",
                    holding_amount=1000,
                    return_percent=1.0,
                )
            ],
            profile=InvestorProfile(),
        ),
        RiskAssessment(
            level="low",
            suggested_action="watch",
            weighted_return_percent=1.0,
            alerts=[],
        ),
        snapshots=[],
        prefetched_news=prefetched,
    )

    assert parsed["title"] == "带新闻的日报"
    assert len(news) >= 1
    assert any(item.title == "电网设备新闻" for item in news)
