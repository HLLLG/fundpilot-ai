from datetime import date

from app.services.cls_news_client import search_cls_news


def test_search_cls_news_filters_by_topic(monkeypatch):
    today = date.today().isoformat()
    monkeypatch.setattr(
        "app.services.cls_news_client.fetch_cls_headlines",
        lambda limit=40: [
            {
                "标题": "半导体板块午后拉升",
                "内容": "多只芯片股走强",
                "发布时间": f"{today} 10:30:00",
            },            {
                "标题": "白酒行业动态",
                "内容": "消费板块",
                "发布时间": "2026-06-10 09:00:00",
            },
        ],
    )
    items = search_cls_news("半导体", limit=3)
    assert len(items) == 1
    assert items[0].source == "cls"
    assert items[0].is_today is True
