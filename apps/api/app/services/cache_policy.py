from __future__ import annotations

import hashlib
import os
import time


def _stable_unit_interval(material: str) -> float:
    digest = hashlib.blake2s(material.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") / float((1 << 64) - 1)


def jittered_ttl(
    cache_key: str,
    ttl_seconds: float,
    *,
    spread: float = 0.1,
) -> float:
    """Return a process-stable TTL in ``base +/- spread``.

    Including the PID keeps two Uvicorn workers from expiring the same global
    key on the same clock edge, while stability within one process prevents a
    cache entry from alternately appearing fresh/stale across reads.
    """

    base = max(0.0, float(ttl_seconds))
    bounded_spread = max(0.0, min(float(spread), 0.5))
    unit = _stable_unit_interval(f"{os.getpid()}:{cache_key}")
    factor = (1.0 - bounded_spread) + (2.0 * bounded_spread * unit)
    return max(0.001, base * factor)


def jittered_time_bucket(
    cache_key: str,
    bucket_seconds: int,
    *,
    now: float | None = None,
    spread: float = 0.1,
) -> int:
    """Return a fixed-width bucket with a per-process/key shifted boundary."""

    width = max(1, int(bucket_seconds))
    bounded_spread = max(0.0, min(float(spread), 0.5))
    unit = _stable_unit_interval(f"{os.getpid()}:{cache_key}:bucket")
    offset = width * bounded_spread * unit
    return int(((time.time() if now is None else float(now)) + offset) // width)
