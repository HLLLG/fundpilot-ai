#!/usr/bin/env python3
"""Fetch bounded PIT universe history for an offline research runner."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import httpx


def fetch_universe_history(
    *,
    url: str,
    token: str,
    days: int,
    max_snapshots: int,
    stride_days: int,
    client,
) -> dict:
    response = client.get(
        url,
        params={
            "days": days,
            "max_snapshots": max_snapshots,
            "stride_days": stride_days,
            "include_members": "true",
        },
        headers={"X-Factor-IC-Publish-Token": token},
        timeout=120.0,
    )
    response.raise_for_status()
    payload = response.json()
    snapshots = payload.get("snapshots")
    if not isinstance(snapshots, list) or len(snapshots) > max_snapshots:
        raise ValueError("PIT 基金池历史响应不符合有界契约")
    return payload


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少环境变量 {name}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="拉取有界 factor IC PIT 基金池历史")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--days", type=int, default=1_800)
    parser.add_argument("--max-snapshots", type=int, default=180)
    parser.add_argument("--stride-days", type=int, default=7)
    args = parser.parse_args()
    with httpx.Client() as client:
        payload = fetch_universe_history(
            url=_required_env("FACTOR_IC_UNIVERSE_FETCH_URL"),
            token=_required_env("FACTOR_IC_PUBLISH_TOKEN"),
            days=args.days,
            max_snapshots=args.max_snapshots,
            stride_days=args.stride_days,
            client=client,
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"fetched PIT universe snapshots: {payload['snapshot_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
