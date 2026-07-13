#!/usr/bin/env python3
"""Publish a validated PIT universe artifact to the authoritative API."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.services.factor_ic_universe_snapshot import (  # noqa: E402
    validate_factor_ic_universe_publish_request,
)

RETRY_DELAYS = (5, 15, 45)
PUBLISH_TIMEOUT_SECONDS = 120.0


def publish_universe(
    *,
    artifact_path: Path,
    url: str,
    token: str,
    client,
    sleep=time.sleep,
    now: datetime | None = None,
) -> str:
    raw = json.loads(artifact_path.read_text(encoding="utf-8"))
    body = validate_factor_ic_universe_publish_request(raw, now=now).model_dump(
        mode="json"
    )
    headers = {"X-Factor-IC-Publish-Token": token}
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            response = client.post(
                url,
                json=body,
                headers=headers,
                timeout=PUBLISH_TIMEOUT_SECONDS,
            )
        except httpx.RequestError:
            if attempt == len(RETRY_DELAYS):
                raise
            sleep(RETRY_DELAYS[attempt])
            continue
        if 500 <= response.status_code < 600:
            if attempt == len(RETRY_DELAYS):
                response.raise_for_status()
            sleep(RETRY_DELAYS[attempt])
            continue
        response.raise_for_status()
        return "created" if response.json().get("created") else "duplicate"
    raise RuntimeError("PIT 基金池发布重试状态异常")


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少环境变量 {name}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="发布 factor IC PIT 基金池")
    parser.add_argument("artifact_path", type=Path)
    args = parser.parse_args()
    with httpx.Client() as client:
        result = publish_universe(
            artifact_path=args.artifact_path,
            url=_required_env("FACTOR_IC_UNIVERSE_PUBLISH_URL"),
            token=_required_env("FACTOR_IC_PUBLISH_TOKEN"),
            client=client,
        )
    print(f"factor IC PIT universe publish result: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
