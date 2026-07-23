#!/usr/bin/env python
"""Run FundPilot's deterministic outage/backpressure regression matrix."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
API_ROOT = ROOT / "apps" / "api"
DEFAULT_TESTS = (
    "tests/test_async_sse.py",
    "tests/test_deepseek_budget.py",
    "tests/test_eastmoney_http.py",
    "tests/test_job_store_resilience.py",
    "tests/test_background_worker.py",
    "tests/test_cross_process_lock.py",
    "tests/test_sector_quote_provider_performance.py",
    "tests/test_shared_executor_cancellation.py",
    "tests/test_performance_observability.py",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    python = API_ROOT / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        python = Path(sys.executable)
    command = [
        str(python),
        "-m",
        "pytest",
        "-q" if not args.verbose else "-vv",
        *DEFAULT_TESTS,
    ]
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=API_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    result = {
        "schema": "fundpilot.fault_matrix.v1",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "passed": completed.returncode == 0,
        "exit_code": completed.returncode,
        "tests": list(DEFAULT_TESTS),
        "stdout": completed.stdout[-12_000:],
        "stderr": completed.stderr[-4_000:],
    }
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
