#!/usr/bin/env python3
"""校验并发布 runner 已生成的 factor IC summary。"""

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

from app.services.factor_ic_snapshot import validate_publish_request  # noqa: E402

RETRY_DELAYS = (5, 15, 45)
PUBLISH_TIMEOUT_SECONDS = 90.0


def publish_summary(
    *,
    summary_path: Path,
    url: str,
    token: str,
    source_commit: str,
    source_run_id: str,
    client,
    sleep=time.sleep,
    now: datetime | None = None,
) -> str:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    request = validate_publish_request(
        {
            "summary": summary,
            "source_commit": source_commit,
            "source_run_id": source_run_id,
        },
        now=now,
    )
    body = request.model_dump(mode="json")
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

        if response.status_code == 409:
            return "newer_exists"
        if 500 <= response.status_code < 600:
            if attempt == len(RETRY_DELAYS):
                response.raise_for_status()
            sleep(RETRY_DELAYS[attempt])
            continue
        response.raise_for_status()
        return "created" if response.json().get("created") else "duplicate"

    raise RuntimeError("factor IC 发布重试状态异常")


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少环境变量 {name}")
    return value


def _write_actions_summary(summary: dict, result: str) -> None:
    target = os.getenv("GITHUB_STEP_SUMMARY", "").strip()
    if not target:
        return
    factor_lines = [
        f"- {row['factor']}: IC {row['mean_ic']:+.4f}, n={row['n_periods']}"
        for row in summary.get("factors") or []
    ]
    coverage = summary.get("coverage") or {}
    segment_lines = []
    model = summary.get("research_model") or {}
    for segment in (model.get("segments") or {}).values():
        primary = (segment.get("horizons") or {}).get("20") or {}
        qualified = [
            key for key, value in (primary.get("qualified") or {}).items() if value
        ]
        segment_lines.append(
            f"- {segment.get('label')}: n={primary.get('universe_size', 0)}, "
            f"qualified={','.join(qualified) or 'none'}"
        )
    text = "\n".join(
        [
            "## Factor IC Refresh",
            "",
            f"- result: {result}",
            f"- generated_at: {summary['generated_at']}",
            f"- universe_size: {summary['universe_size']}",
            f"- rebalance_count: {summary['rebalance_count']}",
            f"- source_share_classes: {coverage.get('source_share_classes', 'n/a')}",
            f"- unique_portfolios: {coverage.get('unique_portfolios', 'n/a')}",
            *factor_lines,
            *segment_lines,
            "",
        ]
    )
    with Path(target).open("a", encoding="utf-8") as stream:
        stream.write(text)


def main() -> int:
    parser = argparse.ArgumentParser(description="发布已校验的 factor IC summary")
    parser.add_argument("summary_path", type=Path)
    args = parser.parse_args()

    url = _required_env("FACTOR_IC_PUBLISH_URL")
    token = _required_env("FACTOR_IC_PUBLISH_TOKEN")
    source_commit = _required_env("GITHUB_SHA")
    source_run_id = _required_env("GITHUB_RUN_ID")
    summary = json.loads(args.summary_path.read_text(encoding="utf-8"))
    with httpx.Client() as client:
        result = publish_summary(
            summary_path=args.summary_path,
            url=url,
            token=token,
            source_commit=source_commit,
            source_run_id=source_run_id,
            client=client,
        )
    _write_actions_summary(summary, result)
    print(f"factor IC publish result: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
