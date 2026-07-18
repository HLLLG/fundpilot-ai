#!/usr/bin/env python3
"""Publish one validated NAV first-observation batch to the authoritative API."""

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

from app.services.factor_ic_nav_observation import (  # noqa: E402
    validate_nav_observation_publish_request,
)

RETRY_DELAYS = (5, 15, 45)
PUBLISH_TIMEOUT_SECONDS = 120.0


def publish_nav_observations(
    *,
    artifact_path: Path,
    url: str,
    token: str,
    client,
    sleep=time.sleep,
    now: datetime | None = None,
) -> dict:
    raw = json.loads(artifact_path.read_text(encoding="utf-8"))
    body = validate_nav_observation_publish_request(raw, now=now).model_dump(
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
        payload = response.json()
        if payload.get("observation_count") != len(body["observations"]):
            raise RuntimeError("NAV observation publish receipt count mismatch")
        return payload
    raise RuntimeError("NAV observation publish retry state is invalid")


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"missing environment variable {name}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publish a Factor IC NAV first-observation batch"
    )
    parser.add_argument("artifact_path", type=Path)
    args = parser.parse_args()
    with httpx.Client() as client:
        result = publish_nav_observations(
            artifact_path=args.artifact_path,
            url=_required_env("FACTOR_IC_NAV_OBSERVATION_PUBLISH_URL"),
            token=_required_env("FACTOR_IC_PUBLISH_TOKEN"),
            client=client,
        )
    print(
        "factor IC NAV observation publish result: "
        f"created={result['created_count']} "
        f"duplicate={result['duplicate_count']} "
        f"observations={result['observation_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
