from app.models import NewsItem
from app.services.news_service import NewsService, _dedupe_news


def test_dedupe_news_by_url():
    items = [
        NewsItem(topic="电网", title="A", url="http://a"),
        NewsItem(topic="电网", title="B", url="http://a"),
    ]
    assert len(_dedupe_news(items)) == 1


def test_search_maps_akshare_frame(monkeypatch):
    pytest = __import__("pytest")
    pd = pytest.importorskip("pandas")


    class FakeRow:
        def __init__(self, data: dict):
            self._data = data

        @property
        def index(self):
            return list(self._data.keys())

        def __getitem__(self, key):
            return self._data[key]

    frame = pd.DataFrame(
        [
            {
                "新闻标题": "电网设备板块走强",
                "新闻内容": "测试摘要内容",
                "发布时间": "2026-05-30 10:00:00",
                "文章来源": "东方财富",
                "新闻链接": "http://finance.eastmoney.com/a/1.html",
            }
        ]
    )

    def fake_stock_news_em(symbol: str):
        assert symbol == "电网设备"
        return frame

    monkeypatch.setattr(
        "akshare.stock_news_em",
        fake_stock_news_em,
        raising=False,
    )
    import sys

    fake_ak = type(sys)("akshare")
    fake_ak.stock_news_em = fake_stock_news_em
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)

    items = NewsService().search("电网设备", limit=3)
    assert len(items) == 1
    assert items[0].title == "电网设备板块走强"
    assert items[0].snippet == "测试摘要内容"
    assert items[0].url.endswith("1.html")


def test_prefetch_topics_respects_limit(monkeypatch):
    calls: list[str] = []

    def fake_search(self, topic: str, limit: int | None = None):
        calls.append(topic)
        return [NewsItem(topic=topic, title=f"标题-{topic}")]

    monkeypatch.setattr(NewsService, "search", fake_search)
    monkeypatch.setenv("FUND_AI_NEWS_MAX_TOPICS", "2")
    from app.config import refresh_settings

    refresh_settings()

    items = NewsService().prefetch_topics(["A", "B", "C"])
    assert calls == ["A", "B"]
    assert len(items) == 2
