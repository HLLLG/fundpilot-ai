"""阶段 2：StreamingReportParser 增量 JSON 解析单测。"""

from __future__ import annotations

import pytest

from app.services.streaming_json_parser import StreamingReportParser


def _collect_feed(parser: StreamingReportParser, chunks: list[str]) -> list[dict]:
    events: list[dict] = []
    for chunk in chunks:
        events.extend(parser.feed(chunk))
    return events


def test_parser_emits_fund_rec_when_object_closes():
    parser = StreamingReportParser()
    chunks = [
        '{"title":"t","fund_recommendations":[',
        '{"fund_code":"519674","fund_name":"x","action":"观察"',
        ',"points":["p1"]},{"fund_code":"015945"',
        ',"fund_name":"y","action":"减仓评估","points":["p2"]}]}',
    ]
    events = _collect_feed(parser, chunks)
    fund_events = [e for e in events if e.get("field") == "fund_recommendation"]
    assert len(fund_events) == 2
    assert fund_events[0]["value"]["fund_code"] == "519674"
    assert fund_events[1]["value"]["fund_code"] == "015945"


def test_parser_emits_title_and_summary():
    parser = StreamingReportParser()
    chunks = [
        '{"title":"持仓盘点","summary":"',
        '今日观望","fund_recommendations":[]}',
    ]
    events = _collect_feed(parser, chunks)
    title_events = [e for e in events if e.get("field") == "title"]
    summary_events = [e for e in events if e.get("field") == "summary"]
    assert len(title_events) == 1
    assert title_events[0]["value"] == "持仓盘点"
    assert len(summary_events) == 1
    assert summary_events[0]["value"] == "今日观望"


def test_parser_handles_string_escapes():
    """chunk 切在转义符中间不应误判 string 闭合。"""
    parser = StreamingReportParser()
    chunks = [
        '{"fund_recommendations":[{"fund_code":"x","fund_name":"a\\',
        '"b","action":"观察","points":[]}]}',
    ]
    events = _collect_feed(parser, chunks)
    fund_events = [e for e in events if e.get("field") == "fund_recommendation"]
    assert len(fund_events) == 1
    assert fund_events[0]["value"]["fund_name"] == 'a"b'


def test_parser_partial_chunks_no_premature_emit():
    parser = StreamingReportParser()
    events = list(parser.feed('{"fund_recommendations":[{'))
    assert not [e for e in events if e.get("field") == "fund_recommendation"]


def test_parser_single_chunk_full_array():
    parser = StreamingReportParser()
    payload = (
        '{"title":"t","fund_recommendations":['
        '{"fund_code":"519674","fund_name":"x","action":"观察","points":["p"]}'
        '],"caveats":["注意风险"]}'
    )
    events = list(parser.feed(payload))
    fund_events = [e for e in events if e.get("field") == "fund_recommendation"]
    caveat_events = [e for e in events if e.get("field") == "caveats"]
    assert len(fund_events) == 1
    assert len(caveat_events) == 1
    assert caveat_events[0]["value"] == ["注意风险"]


def test_parser_finalize_fallback():
    parser = StreamingReportParser()
    parser.feed('{"title":"t","summary":"s"')
    result = parser.finalize('{"title":"t","summary":"s","fund_recommendations":[],"caveats":[]}')
    assert result["title"] == "t"
    assert result["summary"] == "s"


def test_parser_invalid_json_does_not_raise():
    parser = StreamingReportParser()
    events = _collect_feed(parser, ['not json at all {{{'])
    assert events == []
