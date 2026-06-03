from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT, get_settings

logger = logging.getLogger(__name__)


def fetch_boards_via_browser_command(
    *,
    timeout_seconds: float | None = None,
) -> dict[str, dict[str, float]]:
    settings = get_settings()
    command = str(settings.sector_quotes_browser_command or "").strip()
    if not settings.sector_quotes_browser_enabled or not command:
        return _empty_boards()

    timeout = _browser_timeout(settings.sector_quotes_browser_timeout_seconds, timeout_seconds)
    env = os.environ.copy()
    env["FUND_AI_SECTOR_QUOTES_TIMEOUT_SECONDS"] = str(timeout)

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(_browser_workdir()),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except Exception as exc:
        logger.info("browser sector command failed to start: %s", exc)
        return _empty_boards()

    if result.returncode != 0:
        logger.info(
            "browser sector command exited with %s: %s",
            result.returncode,
            (result.stderr or result.stdout).strip()[:300],
        )
        return _empty_boards()

    payload = _parse_stdout_json(result.stdout)
    if payload is None:
        logger.info("browser sector command returned non-json output")
        return _empty_boards()

    return _coerce_boards(payload)


def _browser_timeout(default_timeout: float, timeout_seconds: float | None) -> float:
    if timeout_seconds is None:
        return default_timeout
    return round(max(1.0, min(default_timeout, timeout_seconds * 0.7)), 3)


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


def _coerce_boards(payload: Any) -> dict[str, dict[str, float]]:
    if isinstance(payload, dict):
        for key in ("boards", "data", "result"):
            nested = payload.get(key)
            if isinstance(nested, dict) and any(name in nested for name in ("index", "concept", "industry")):
                return _coerce_boards(nested)

        return {
            "index": _coerce_board_map(payload.get("index")),
            "concept": _coerce_board_map(payload.get("concept")),
            "industry": _coerce_board_map(payload.get("industry")),
        }

    return _empty_boards()


def _coerce_board_map(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}

    result: dict[str, float] = {}
    for name, value in raw.items():
        cleaned = str(name).strip()
        if not cleaned:
            continue
        try:
            result[cleaned] = round(float(value), 4)
        except (TypeError, ValueError):
            continue
    return result


def _empty_boards() -> dict[str, dict[str, float]]:
    return {"index": {}, "concept": {}, "industry": {}}
