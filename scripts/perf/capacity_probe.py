#!/usr/bin/env python
"""Bounded FundPilot HTTP capacity probe.

Remote hosts are read-only by default and require ``--production-safe``. The
probe never prints the bearer token and refuses write/SSE scenarios unless the
caller explicitly enables them.
"""

from __future__ import annotations

import argparse
import concurrent.futures
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

DEFAULT_PUBLIC_ENDPOINTS = (
    "/health",
    "/api/trading-session",
)
DEFAULT_AUTHENTICATED_ENDPOINTS = (
    "/api/auth/me",
    "/api/portfolio/refresh-and-hydrate",
    "/api/portfolio/summary",
    "/api/reports",
    "/api/fund-discovery/reports",
)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return round(ordered[index], 3)


def _is_local(base_url: str) -> bool:
    host = (urlsplit(base_url).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def _validate_scope(args: argparse.Namespace) -> None:
    local = _is_local(args.base_url)
    if not local and not args.production_safe:
        raise SystemExit("remote targets require --production-safe")
    if not local and max(args.concurrency) > 25:
        raise SystemExit("production-safe probes are capped at concurrency 25")
    if not local and args.requests_per_endpoint > 100:
        raise SystemExit("production-safe probes are capped at 100 requests per endpoint")
    if args.method != "GET" and not args.allow_mutation:
        raise SystemExit("non-GET probes require --allow-mutation")
    if args.sse_disconnect and not args.allow_expensive:
        raise SystemExit("SSE/provider scenarios require --allow-expensive")


def _one_request(
    client: httpx.Client,
    *,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        with client.stream(method, path, json=body) as response:
            headers_at = time.perf_counter()
            payload = response.read()
        ended = time.perf_counter()
        return {
            "status": response.status_code,
            "latency_ms": (ended - started) * 1000.0,
            "ttfb_ms": (headers_at - started) * 1000.0,
            "bytes": len(payload),
            "error": None,
        }
    except httpx.HTTPError as exc:
        ended = time.perf_counter()
        return {
            "status": 0,
            "latency_ms": (ended - started) * 1000.0,
            "ttfb_ms": None,
            "bytes": 0,
            "error": exc.__class__.__name__,
        }


def _sse_disconnect(
    client: httpx.Client,
    *,
    path: str,
    body: dict[str, Any],
    disconnect_after_seconds: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    first_event_at: float | None = None
    status = 0
    try:
        with client.stream("POST", path, json=body) as response:
            status = response.status_code
            for line in response.iter_lines():
                if line and first_event_at is None:
                    first_event_at = time.perf_counter()
                if time.perf_counter() - started >= disconnect_after_seconds:
                    break
        ended = time.perf_counter()
        return {
            "status": status,
            "latency_ms": (ended - started) * 1000.0,
            "ttfb_ms": (
                (first_event_at - started) * 1000.0
                if first_event_at is not None
                else None
            ),
            "bytes": 0,
            "error": None,
        }
    except httpx.HTTPError as exc:
        ended = time.perf_counter()
        return {
            "status": status,
            "latency_ms": (ended - started) * 1000.0,
            "ttfb_ms": None,
            "bytes": 0,
            "error": exc.__class__.__name__,
        }


def _summarize(rows: list[dict[str, Any]], elapsed: float) -> dict[str, Any]:
    latencies = [float(row["latency_ms"]) for row in rows]
    ttfb = [
        float(row["ttfb_ms"])
        for row in rows
        if row.get("ttfb_ms") is not None
    ]
    errors = [
        row
        for row in rows
        if row.get("error") or not 200 <= int(row.get("status") or 0) < 400
    ]
    statuses: dict[str, int] = {}
    categories: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or 0)
        statuses[status] = statuses.get(status, 0) + 1
        if row.get("error"):
            category = str(row["error"])
            categories[category] = categories.get(category, 0) + 1
    return {
        "requests": len(rows),
        "elapsed_seconds": round(elapsed, 3),
        "rps": round(len(rows) / elapsed, 3) if elapsed > 0 else None,
        "error_rate_percent": round(len(errors) / len(rows) * 100.0, 3) if rows else 0,
        "latency_ms": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "p99": _percentile(latencies, 0.99),
            "max": round(max(latencies), 3) if latencies else None,
        },
        "ttfb_ms": {
            "p50": _percentile(ttfb, 0.50),
            "p95": _percentile(ttfb, 0.95),
            "p99": _percentile(ttfb, 0.99),
        },
        "response_bytes": sum(int(row.get("bytes") or 0) for row in rows),
        "statuses": statuses,
        "error_categories": categories,
    }


def _run_stage(
    client: httpx.Client,
    *,
    concurrency: int,
    requests: int,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    sse_disconnect: bool,
    disconnect_after_seconds: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        if sse_disconnect:
            assert body is not None
            futures = [
                executor.submit(
                    _sse_disconnect,
                    client,
                    path=path,
                    body=body,
                    disconnect_after_seconds=disconnect_after_seconds,
                )
                for _ in range(requests)
            ]
        else:
            futures = [
                executor.submit(
                    _one_request,
                    client,
                    method=method,
                    path=path,
                    body=body,
                )
                for _ in range(requests)
            ]
        rows = [future.result() for future in futures]
    return _summarize(rows, time.perf_counter() - started)


def run(args: argparse.Namespace) -> dict[str, Any]:
    _validate_scope(args)
    token = os.getenv(args.token_env, "").strip()
    endpoints = args.endpoint or (
        DEFAULT_AUTHENTICATED_ENDPOINTS if token else DEFAULT_PUBLIC_ENDPOINTS
    )
    body = (
        json.loads(args.body_file.read_text(encoding="utf-8"))
        if args.body_file
        else None
    )
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    limits = httpx.Limits(
        max_connections=max(20, max(args.concurrency) * 2),
        max_keepalive_connections=max(10, max(args.concurrency)),
        keepalive_expiry=30,
    )
    results: dict[str, dict[str, Any]] = {}
    with httpx.Client(
        base_url=args.base_url.rstrip("/"),
        headers=headers,
        timeout=httpx.Timeout(args.timeout_seconds),
        limits=limits,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        for concurrency in args.concurrency:
            level: dict[str, Any] = {}
            for path in endpoints:
                level[path] = _run_stage(
                    client,
                    concurrency=concurrency,
                    requests=args.requests_per_endpoint,
                    method=args.method,
                    path=path,
                    body=body,
                    sse_disconnect=args.sse_disconnect,
                    disconnect_after_seconds=args.disconnect_after_seconds,
                )
            results[str(concurrency)] = level
    return {
        "schema": "fundpilot.capacity_probe.v1",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "target": {
            "scheme": urlsplit(args.base_url).scheme,
            "host": urlsplit(args.base_url).hostname,
            "production_safe": args.production_safe,
            "authenticated": bool(token),
        },
        "scope": {
            "method": args.method,
            "endpoints": list(endpoints),
            "concurrency": args.concurrency,
            "requests_per_endpoint": args.requests_per_endpoint,
            "sse_disconnect": args.sse_disconnect,
        },
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--endpoint", action="append")
    parser.add_argument("--method", default="GET", choices=("GET", "POST"))
    parser.add_argument("--body-file", type=Path)
    parser.add_argument("--token-env", default="FUNDPILOT_PERF_TOKEN")
    parser.add_argument("--concurrency", nargs="+", type=int, default=[1, 10, 25, 50])
    parser.add_argument("--requests-per-endpoint", type=int, default=80)
    parser.add_argument("--timeout-seconds", type=float, default=30)
    parser.add_argument("--production-safe", action="store_true")
    parser.add_argument("--allow-mutation", action="store_true")
    parser.add_argument("--allow-expensive", action="store_true")
    parser.add_argument("--sse-disconnect", action="store_true")
    parser.add_argument("--disconnect-after-seconds", type=float, default=2)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(args)
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
