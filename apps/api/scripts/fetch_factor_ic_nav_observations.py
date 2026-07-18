#!/usr/bin/env python3
"""Fetch bounded first-observed NAV history for the offline Factor IC runner."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

SCHEMA_VERSION = "factor_ic_nav_observation_history.v1"
AVAILABILITY_BASIS = "collector_first_observed_at"
REVISION_POLICY = "first_observed_value"
QUERY_CHUNK_SIZE = 100
MAX_CODES = 5_000


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def extract_pit_fund_codes(payload: dict[str, Any]) -> list[str]:
    snapshots = payload.get("snapshots")
    if not isinstance(snapshots, list):
        raise ValueError("PIT history does not contain snapshots")
    codes: set[str] = set()
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            raise ValueError("PIT snapshot is not an object")
        members = snapshot.get("members")
        if not isinstance(members, list):
            raise ValueError("PIT history must include snapshot members")
        for member in members:
            if not isinstance(member, dict):
                raise ValueError("PIT member is not an object")
            code = str(member.get("fund_code") or "").strip()
            if len(code) != 6 or not code.isdigit():
                raise ValueError("PIT member fund_code is invalid")
            codes.add(code)
    result = sorted(codes)
    if not result:
        raise ValueError("PIT history contains no fund codes")
    if len(result) > MAX_CODES:
        raise ValueError(
            f"PIT history has {len(result)} unique funds; maximum is {MAX_CODES}"
        )
    return result


def fetch_nav_observation_history(
    *,
    url: str,
    token: str,
    fund_codes: list[str],
    start_date: date,
    end_date: date,
    as_of: datetime,
    client,
) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    revision_rows_excluded = 0
    normalized_as_of = as_of.astimezone(timezone.utc).isoformat()
    for index in range(0, len(fund_codes), QUERY_CHUNK_SIZE):
        chunk = fund_codes[index : index + QUERY_CHUNK_SIZE]
        response = client.post(
            url,
            json={
                "fund_codes": chunk,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "as_of": normalized_as_of,
            },
            headers={"X-Factor-IC-Publish-Token": token},
            timeout=120.0,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("observations")
        if (
            payload.get("schema_version") != SCHEMA_VERSION
            or payload.get("point_in_time_scope") != "nav_observation_pit"
            or payload.get("nav_revision_pit") is not True
            or payload.get("availability_basis") != AVAILABILITY_BASIS
            or payload.get("revision_policy") != REVISION_POLICY
            or not isinstance(rows, list)
            or payload.get("content_hash") != _hash(rows)
        ):
            raise ValueError("NAV observation response contract is invalid")
        revision_rows_excluded += int(payload.get("revision_rows_excluded") or 0)
        chunk_set = set(chunk)
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("NAV observation row is not an object")
            code = str(row.get("fund_code") or "")
            nav_date = str(row.get("nav_date") or "")
            first_observed_at = str(row.get("first_observed_at") or "")
            if code not in chunk_set or len(nav_date) != 10 or not first_observed_at:
                raise ValueError("NAV observation row identity is invalid")
            key = (code, nav_date)
            if key in seen:
                raise ValueError("NAV observation response contains duplicate dates")
            seen.add(key)
            observations.append(row)
    observations.sort(
        key=lambda row: (
            str(row.get("fund_code") or ""),
            str(row.get("nav_date") or ""),
        )
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "point_in_time_scope": "nav_observation_pit",
        "nav_revision_pit": True,
        "availability_basis": AVAILABILITY_BASIS,
        "revision_policy": REVISION_POLICY,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "as_of": normalized_as_of,
        "fund_code_count": len(fund_codes),
        "observation_count": len(observations),
        "revision_rows_excluded": revision_rows_excluded,
        "content_hash": _hash(observations),
        "observations": observations,
    }


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"missing environment variable {name}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch bounded Factor IC NAV first-observation history"
    )
    parser.add_argument("--pit-history", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--days", type=int, default=1_600)
    args = parser.parse_args()
    if not 1 <= args.days <= 1_800:
        raise ValueError("days must be in 1..1800")
    pit_payload = json.loads(args.pit_history.read_text(encoding="utf-8"))
    codes = extract_pit_fund_codes(pit_payload)
    now = datetime.now(timezone.utc)
    end = now.date()
    start = end - timedelta(days=args.days - 1)
    with httpx.Client() as client:
        payload = fetch_nav_observation_history(
            url=_required_env("FACTOR_IC_NAV_OBSERVATION_FETCH_URL"),
            token=_required_env("FACTOR_IC_PUBLISH_TOKEN"),
            fund_codes=codes,
            start_date=start,
            end_date=end,
            as_of=now,
            client=client,
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        "fetched NAV first-observations: "
        f"funds={payload['fund_code_count']} "
        f"observations={payload['observation_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
