from __future__ import annotations

from starlette.requests import Request

from app import main


def _request(*, if_none_match: str | None = None) -> Request:
    headers = []
    if if_none_match is not None:
        headers.append((b"if-none-match", if_none_match.encode("ascii")))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/reports/report-1",
            "headers": headers,
        }
    )


def test_immutable_private_report_response_supports_etag_revalidation() -> None:
    payload = {"id": "report-1", "title": "日报"}

    first = main._immutable_json_response(_request(), payload)
    etag = first.headers["etag"]
    second = main._immutable_json_response(
        _request(if_none_match=etag),
        payload,
    )

    assert first.status_code == 200
    assert first.headers["cache-control"] == "private, max-age=0, must-revalidate"
    assert first.headers["vary"] == "Authorization"
    assert second.status_code == 304
    assert second.body == b""
