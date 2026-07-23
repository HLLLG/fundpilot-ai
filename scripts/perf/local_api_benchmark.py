#!/usr/bin/env python
"""Offline, isolated FundPilot API latency baseline.

This script never points at the configured application database and disables
provider/background refreshes.  It starts one local Uvicorn worker against a
temporary SQLite database, creates a benchmark-only user, seeds synthetic large
report payloads, and exercises authenticated read endpoints.
"""

from __future__ import annotations

import argparse
import concurrent.futures
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[2]
API_ROOT = ROOT / "apps" / "api"
ENDPOINTS = (
    "/health",
    "/api/auth/me",
    "/api/investor-profile",
    "/api/portfolio/holdings",
    "/api/portfolio/summary",
    "/api/fund-profiles",
    "/api/reports",
    "/api/fund-discovery/reports",
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 2)


def _request(
    client: httpx.Client,
    path: str,
) -> dict[str, float | int]:
    started = time.perf_counter()
    with client.stream("GET", path) as response:
        headers_at = time.perf_counter()
        body = response.read()
    finished = time.perf_counter()
    return {
        "status": response.status_code,
        "latency_ms": (finished - started) * 1000,
        "ttfb_ms": (headers_at - started) * 1000,
        "bytes": len(body),
    }


def _wait_until_ready(base_url: str, process: subprocess.Popen[Any]) -> None:
    deadline = time.monotonic() + 30
    last_error = ""
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"benchmark API exited early with code {process.returncode}"
            )
        try:
            response = httpx.get(f"{base_url}/health", timeout=1)
            if response.status_code == 200:
                return
            last_error = f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            last_error = type(exc).__name__
        time.sleep(0.1)
    raise RuntimeError(f"benchmark API did not become ready: {last_error}")


def _register(client: httpx.Client) -> tuple[str, int]:
    response = client.post(
        "/api/auth/register",
        json={
            "userAccount": "perf-local@example.com",
            "password": "PerfOnly1234!",
            "username": "Local performance fixture",
        },
    )
    response.raise_for_status()
    token = str(response.json()["accessToken"])
    client.headers["Authorization"] = f"Bearer {token}"
    principal = client.get("/api/auth/me")
    principal.raise_for_status()
    return token, int(principal.json()["id"])


def _summary(payload: dict[str, Any], fields: tuple[str, ...]) -> str:
    return json.dumps(
        {key: payload[key] for key in fields if key in payload},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _seed_reports(
    db_path: Path,
    *,
    user_id: int,
    report_count: int,
    daily_payload_bytes: int,
    discovery_payload_bytes: int,
    summaries: bool,
) -> None:
    now = datetime.now(timezone.utc)
    daily_fields = (
        "id",
        "created_at",
        "title",
        "summary",
        "provider",
        "analysis_mode",
        "risk",
        "caveats",
        "target_sectors",
        "focus_sectors",
        "market_context",
    )
    discovery_fields = (
        "id",
        "created_at",
        "title",
        "summary",
        "market_view",
        "target_sectors",
        "focus_sectors",
        "analysis_mode",
        "provider",
        "caveats",
    )
    connection = sqlite3.connect(db_path, timeout=30)
    try:
        for index in range(report_count):
            created = (now - timedelta(days=index)).isoformat()
            daily = {
                "id": f"perf-daily-{index:03d}",
                "created_at": created,
                "title": f"Synthetic daily report {index}",
                "summary": "Synthetic benchmark data; not investment evidence.",
                "provider": "offline-fixture",
                "analysis_mode": "deep",
                "risk": {
                    "level": "medium",
                    "suggested_action": "hold",
                    "weighted_return_percent": 0,
                },
                "caveats": ["offline synthetic payload"],
                "large_fixture": "d" * daily_payload_bytes,
            }
            discovery = {
                "id": f"perf-discovery-{index:03d}",
                "created_at": created,
                "title": f"Synthetic discovery report {index}",
                "summary": "Synthetic benchmark data; not fund research.",
                "market_view": "offline",
                "target_sectors": [],
                "focus_sectors": [],
                "analysis_mode": "deep",
                "provider": "offline-fixture",
                "caveats": ["offline synthetic payload"],
                "large_fixture": "x" * discovery_payload_bytes,
            }
            connection.execute(
                """
                INSERT INTO reports (
                    id, created_at, payload, summary_payload, userId
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    daily["id"],
                    created,
                    json.dumps(daily, ensure_ascii=False),
                    _summary(daily, daily_fields) if summaries else None,
                    user_id,
                ),
            )
            if summaries:
                connection.execute(
                    """
                    INSERT INTO report_summaries (
                        userId, report_id, created_at, summary_payload
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        daily["id"],
                        created,
                        _summary(daily, daily_fields),
                    ),
                )
            connection.execute(
                """
                INSERT INTO fund_discovery_reports (
                    id, created_at, payload, summary_payload, userId
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    discovery["id"],
                    created,
                    json.dumps(discovery, ensure_ascii=False),
                    _summary(discovery, discovery_fields)
                    if summaries
                    else None,
                    user_id,
                ),
            )
            if summaries:
                connection.execute(
                    """
                    INSERT INTO fund_discovery_report_summaries (
                        userId, report_id, created_at, summary_payload
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        discovery["id"],
                        created,
                        _summary(discovery, discovery_fields),
                    ),
                )
        connection.commit()
    finally:
        connection.close()


def _load_scenario(
    client: httpx.Client,
    *,
    path: str,
    requests: int,
    concurrency: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=concurrency
    ) as executor:
        results = list(
            executor.map(lambda _index: _request(client, path), range(requests))
        )
    elapsed = time.perf_counter() - started
    latencies = [float(item["latency_ms"]) for item in results]
    successes = sum(1 for item in results if int(item["status"]) == 200)
    return {
        "requests": requests,
        "concurrency": concurrency,
        "successes": successes,
        "success_rate": round(successes / max(1, requests), 4),
        "p50_ms": _percentile(latencies, 0.50),
        "p95_ms": _percentile(latencies, 0.95),
        "p99_ms": _percentile(latencies, 0.99),
        "max_ms": round(max(latencies, default=0), 2),
        "rps": round(requests / max(0.001, elapsed), 2),
    }


def _process_snapshot(process: subprocess.Popen[Any]) -> dict[str, Any]:
    try:
        import psutil
    except ImportError:
        if os.name != "nt":
            return {"available": False}
        command = (
            f"$p = Get-Process -Id {process.pid}; "
            "$tcp = @(Get-NetTCPConnection -OwningProcess $p.Id "
            "-ErrorAction SilentlyContinue); "
            "[ordered]@{available=$true;scope='root_process_only';"
            "processes=1;rss_mb=[math]::Round($p.WorkingSet64/1MB,2);"
            "threads=$p.Threads.Count;tcp_connections=$tcp.Count} "
            "| ConvertTo-Json -Compress"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return {"available": False}
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"available": False}
    root = psutil.Process(process.pid)
    processes = [root, *root.children(recursive=True)]
    live = [item for item in processes if item.is_running()]
    rss = 0
    threads = 0
    tcp_connections = 0
    for item in live:
        try:
            rss += item.memory_info().rss
            threads += item.num_threads()
            tcp_connections += len(
                [
                    conn
                    for conn in item.net_connections(kind="tcp")
                    if conn.status != psutil.CONN_NONE
                ]
            )
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    return {
        "available": True,
        "processes": len(live),
        "rss_mb": round(rss / 1024 / 1024, 2),
        "threads": threads,
        "tcp_connections": tcp_connections,
    }


def _revision() -> dict[str, Any]:
    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip()

    return {
        "commit": git("rev-parse", "--short", "HEAD"),
        "dirty": bool(git("status", "--porcelain")),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    port = args.port or _free_port()
    base_url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="fundpilot-perf-") as temp:
        temp_path = Path(temp)
        db_path = temp_path / "benchmark.db"
        env = os.environ.copy()
        env.update(
            {
                "FUND_AI_DATABASE_URL": "",
                "FUND_AI_DB_PATH": str(db_path),
                "FUND_AI_JWT_SECRET": "local-perf-only-secret-32-characters!!",
                "FUND_AI_RUNTIME_ROLE": "api",
                "FUND_AI_OCR_PRELOAD": "false",
                "FUND_AI_FUND_NAME_PRELOAD_ENABLED": "false",
                "FUND_AI_THEME_BOARD_REFRESH_ENABLED": "false",
                "FUND_AI_FUND_PRIMARY_SECTOR_PRECOMPUTE_ENABLED": "false",
                "FUND_AI_FUND_PRIMARY_SECTOR_BACKFILL_ENABLED": "false",
                "FUND_AI_NEWS_ENABLED": "false",
                "FUND_AI_SECTOR_QUOTES_ENABLED": "false",
                "FUND_AI_DEEPSEEK_API_KEY": "",
                "PYTHONUNBUFFERED": "1",
            }
        )
        log_path = temp_path / "uvicorn.log"
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--workers",
                    "1",
                    "--log-level",
                    "warning",
                ],
                cwd=API_ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                _wait_until_ready(base_url, process)
                limits = httpx.Limits(
                    max_connections=max(100, max(args.concurrency)),
                    max_keepalive_connections=max(50, max(args.concurrency)),
                )
                with httpx.Client(
                    base_url=base_url,
                    timeout=30,
                    limits=limits,
                ) as client:
                    _token, user_id = _register(client)
                    _seed_reports(
                        db_path,
                        user_id=user_id,
                        report_count=args.report_count,
                        daily_payload_bytes=args.daily_payload_bytes,
                        discovery_payload_bytes=args.discovery_payload_bytes,
                        summaries=args.summary_mode == "present",
                    )
                    cold = {
                        path: {
                            key: round(value, 2)
                            if isinstance(value, float)
                            else value
                            for key, value in _request(client, path).items()
                        }
                        for path in ENDPOINTS
                    }
                    load: dict[str, dict[str, Any]] = {}
                    for concurrency in args.concurrency:
                        load[str(concurrency)] = {
                            path: _load_scenario(
                                client,
                                path=path,
                                requests=args.requests,
                                concurrency=concurrency,
                            )
                            for path in ENDPOINTS
                        }
                    resources = _process_snapshot(process)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        return {
            "schema": "fundpilot.local_api_benchmark.v1",
            "label": args.label,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "scope": {
                "server": "one local Uvicorn worker",
                "database": "isolated temporary SQLite",
                "external_provider_calls": "disabled",
                "report_count_each": args.report_count,
                "daily_payload_bytes_each": args.daily_payload_bytes,
                "discovery_payload_bytes_each": args.discovery_payload_bytes,
                "summary_mode": args.summary_mode,
                "requests_per_endpoint_per_level": args.requests,
                "concurrency": args.concurrency,
            },
            "revision": _revision(),
            "cold": cold,
            "load": load,
            "resources_end_snapshot": resources,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="local")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--requests", type=int, default=80)
    parser.add_argument(
        "--concurrency",
        type=int,
        nargs="+",
        default=[1, 10, 25, 50],
    )
    parser.add_argument("--report-count", type=int, default=20)
    parser.add_argument("--daily-payload-bytes", type=int, default=120_000)
    parser.add_argument(
        "--discovery-payload-bytes",
        type=int,
        default=900_000,
    )
    parser.add_argument(
        "--summary-mode",
        choices=("present", "missing"),
        default="present",
    )
    args = parser.parse_args()
    result = run(args)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
