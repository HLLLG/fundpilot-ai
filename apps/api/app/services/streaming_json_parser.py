"""增量解析 LLM 流式 JSON 输出，在字段就绪时 emit partial 事件。"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

from app.services.deepseek_client import _extract_json_string_field, _parse_model_json

_PARTIAL_EVENT = "report_partial"


class StreamingReportParser:
    """累积 LLM 流式 chunks，在 title / summary / 数组字段 / caveats 就绪时 emit。"""

    def __init__(
        self,
        *,
        array_field: str = "fund_recommendations",
        item_partial_field: str = "fund_recommendation",
    ) -> None:
        self._array_field = array_field
        self._item_partial_field = item_partial_field
        self._buffer = ""
        self._title_emitted = False
        self._summary_emitted = False
        self._caveats_emitted = False
        self._emitted_item_ids: set[str] = set()
        self._array_scanner = _RecommendationArrayScanner(array_field=array_field)

    def feed(self, chunk: str) -> Iterator[dict]:
        if not chunk:
            return
        self._buffer += chunk

        if not self._title_emitted:
            title = _extract_json_string_field(self._buffer, "title")
            if title is not None:
                self._title_emitted = True
                yield _partial("title", title)

        if not self._summary_emitted:
            summary = _extract_json_string_field(self._buffer, "summary")
            if summary is not None:
                self._summary_emitted = True
                yield _partial("summary", summary)

        for obj in self._array_scanner.feed(chunk, self._buffer):
            code = obj.get("fund_code")
            if isinstance(code, str) and code not in self._emitted_item_ids:
                self._emitted_item_ids.add(code)
                yield _partial(self._item_partial_field, obj)

        if not self._caveats_emitted:
            caveats = _try_extract_caveats_array(self._buffer)
            if caveats is not None:
                self._caveats_emitted = True
                yield _partial("caveats", caveats)

    def finalize(self, full_text: str) -> dict:
        return _parse_model_json(full_text or self._buffer)


def _partial(field: str, value: Any) -> dict:
    return {"type": _PARTIAL_EVENT, "field": field, "value": value}


class _RecommendationArrayScanner:
    """在 JSON 数组字段内跟踪 brace 深度，输出已闭合的对象。"""

    def __init__(self, *, array_field: str) -> None:
        self._array_field = array_field
        self._marker_pattern = re.compile(
            rf'"{re.escape(array_field)}"\s*:\s*\[',
        )
        self._in_array = False
        self._rec_array_depth = 0
        self._obj_depth = 0
        self._obj_start: int | None = None
        self._scan_pos = 0
        self._in_string = False
        self._escaped = False
        self._marker_found = False

    def feed(self, chunk: str, full_buffer: str) -> list[dict]:
        del chunk
        emitted: list[dict] = []
        buf = full_buffer

        if not self._marker_found:
            match = self._marker_pattern.search(buf)
            if not match:
                return emitted
            self._marker_found = True
            self._in_array = True
            self._rec_array_depth = 1
            self._scan_pos = match.end()

        index = self._scan_pos
        while index < len(buf):
            char = buf[index]
            if self._in_string:
                if self._escaped:
                    self._escaped = False
                elif char == "\\":
                    self._escaped = True
                elif char == '"':
                    self._in_string = False
                index += 1
                continue

            if char == '"':
                self._in_string = True
            elif char == "[" and self._in_array and self._obj_depth == 0:
                self._rec_array_depth += 1
            elif char == "]" and self._in_array and self._obj_depth == 0:
                self._rec_array_depth -= 1
                if self._rec_array_depth == 0:
                    self._in_array = False
            elif char == "{" and self._in_array and self._rec_array_depth == 1:
                if self._obj_depth == 0:
                    self._obj_start = index
                self._obj_depth += 1
            elif char == "}" and self._in_array and self._obj_depth > 0:
                self._obj_depth -= 1
                if self._obj_depth == 0 and self._obj_start is not None:
                    obj_str = buf[self._obj_start : index + 1]
                    self._obj_start = None
                    try:
                        parsed = json.loads(obj_str)
                    except json.JSONDecodeError:
                        parsed = None
                    if isinstance(parsed, dict):
                        emitted.append(parsed)
            index += 1

        self._scan_pos = index
        return emitted


def _try_extract_caveats_array(buffer: str) -> list[str] | None:
    match = re.search(r'"caveats"\s*:\s*\[', buffer)
    if not match:
        return None

    start = match.end() - 1
    in_string = False
    escaped = False
    depth = 0
    for index in range(start, len(buffer)):
        char = buffer[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                array_str = buffer[start : index + 1]
                try:
                    parsed = json.loads(array_str)
                except json.JSONDecodeError:
                    return None
                if isinstance(parsed, list):
                    return [str(item) for item in parsed]
                return None
    return None
