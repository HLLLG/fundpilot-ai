#!/usr/bin/env python3
"""Probe sector spot and intraday paths (Eastmoney / relay / browser / AkShare)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.services.sector_quote_diagnostic import run_sector_quote_diagnostic  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnose sector spot and intraday provider paths"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=8.0,
        help="Per-request timeout hint in seconds (default: 8)",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON",
    )
    args = parser.parse_args()

    result = run_sector_quote_diagnostic(timeout_seconds=args.timeout)
    indent = 2 if args.pretty else None
    print(json.dumps(result, ensure_ascii=False, indent=indent))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
