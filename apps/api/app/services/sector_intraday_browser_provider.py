from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT, get_settings

logger = logging.getLogger(__name__)

IntradayPoint = dict[str, str | float]

_DEFAULT_INTRADAY_BROWSER_COMMAND = "node scripts/sector-intraday-browser-command.mjs"


def fetch_intraday_via_browser_command(
    secid: str,
    *,
    source_code: str | None = None,
    trade_date: str | None = None,
    timeout_seconds: float | None = None,
) -> list[IntradayPoint]:
    """经 Playwright 在东财页面上下文拉分钟分时（09:30–15:00），绕过直连 push2 被掐。"""
    settings = get_settings()
    if not settings.sector_quotes_browser_enabled:
        return []

    command = (
        str(settings.sector_intraday_browser_command or "").strip()
        or _DEFAULT_INTRADAY_BROWSER_COMMAND
    )
    cleaned_secid = str(secid).strip()
    if not cleaned_secid:
        return []

    timeout = _browser_timeout(settings.sector_quotes_browser_timeout_seconds, timeout_seconds)
    env = os.environ.copy()
    env["FUND_AI_SECTOR_QUOTES_TIMEOUT_SECONDS"] = str(timeout)
    env["FUND_AI_INTRADAY_SECID"] = cleaned_secid
    env["FUND_AI_INTRADAY_SOURCE_CODE"] = (source_code or "").strip()
    if trade_date:
        env["FUND_AI_INTRADAY_TRADE_DATE"] = trade_date
    env.setdefault("PYTHONIOENCODING", "utf-8")

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(_browser_workdir()),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            check=False,
        )
    except Exception as exc:
        logger.debug("browser intraday command failed to start: %s", exc)
        return []

    if result.returncode != 0:
        logger.debug(
            "browser intraday exited %s: %s",
            result.returncode,
            (result.stderr or result.stdout).strip()[:300],
        )
        return []

    payload = _parse_stdout_json(result.stdout)
    if not isinstance(payload, dict):
        return []

    points = payload.get("points")
    if not isinstance(points, list):
        return []

    normalized: list[IntradayPoint] = []
    for item in points:
        if not isinstance(item, dict):
            continue
        time_value = item.get("time")
        percent = item.get("percent")
        if time_value is None or percent is None:
            continue
        try:
            normalized.append(
                {"time": str(time_value)[:5], "percent": round(float(percent), 4)}
            )
        except (TypeError, ValueError):
            continue
    return normalized if len(normalized) >= 2 else []


def _browser_timeout(default_timeout: float, timeout_seconds: float | None) -> float:
    if timeout_seconds is None:
        return max(default_timeout, 12.0)
    return round(max(12.0, min(max(default_timeout, 12.0), timeout_seconds)), 3)


def _browser_workdir() -> Path:
    web_dir = PROJECT_ROOT / "apps" / "web"
    return web_dir if web_dir.exists() else PROJECT_ROOT


def _parse_stdout_json(stdout: str) -> Any | None:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        return None
    for line in reversed(lines):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None
