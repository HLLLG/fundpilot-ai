#!/usr/bin/env python3
"""Capture today's full fund catalogue as a validated PIT universe artifact."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.services.akshare_subprocess import fetch_open_fund_universe  # noqa: E402
from app.services.factor_ic_nav_observation import (  # noqa: E402
    build_nav_observation_batch_from_universe,
)
from app.services.factor_ic_universe_snapshot import (  # noqa: E402
    build_factor_ic_universe_payload,
    validate_factor_ic_universe_publish_request,
)


def capture_universe(
    *,
    source_commit: str,
    source_run_id: str,
    fetch_universe=fetch_open_fund_universe,
    now: datetime | None = None,
) -> dict:
    captured_at = now or datetime.now(timezone.utc)
    rows = fetch_universe(limit=25_000, timeout_seconds=90)
    if not rows:
        raise RuntimeError("开放式基金全目录获取失败")
    payload = build_factor_ic_universe_payload(
        rows,
        source_commit=source_commit,
        source_run_id=source_run_id,
        captured_at=captured_at,
    )
    return validate_factor_ic_universe_publish_request(
        payload,
        now=captured_at,
    ).model_dump(mode="json")


def _required(value: str | None, name: str) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise RuntimeError(f"缺少 {name}")
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(description="捕获当日 factor IC PIT 基金池")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--nav-observations-out", type=Path)
    parser.add_argument("--source-commit", default=os.getenv("GITHUB_SHA"))
    parser.add_argument("--source-run-id", default=os.getenv("GITHUB_RUN_ID"))
    args = parser.parse_args()
    payload = capture_universe(
        source_commit=_required(args.source_commit, "source commit"),
        source_run_id=_required(args.source_run_id, "source run id"),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    nav_payload = None
    if args.nav_observations_out is not None:
        args.nav_observations_out.unlink(missing_ok=True)
        try:
            nav_payload = build_nav_observation_batch_from_universe(payload)
        except ValueError as exc:
            print(
                "optional NAV observation batch skipped because rank enrichment "
                f"did not meet its quality gate: {exc}",
                file=sys.stderr,
            )
        if nav_payload is not None:
            args.nav_observations_out.parent.mkdir(parents=True, exist_ok=True)
            args.nav_observations_out.write_text(
                json.dumps(nav_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    print(
        "captured PIT universe: "
        f"source={payload['snapshot']['source_share_count']} "
        f"sampled={payload['snapshot']['sampled_fund_count']}"
    )
    if nav_payload is not None:
        print(
            "captured first-observation NAV batch: "
            f"observations={len(nav_payload['observations'])} "
            f"missing={nav_payload['missing_observation_count']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
