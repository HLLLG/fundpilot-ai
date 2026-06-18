#!/usr/bin/env python3
"""校验 `us_qdii_seeds` 种子表完整性（本地/CI 快速冒烟）。

用法（在 apps/api 目录）::

    python scripts/verify_qdii_seeds.py
"""

from __future__ import annotations

import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.services import us_qdii_seeds as s

seeds = s.get_qdii_seeds()
assert seeds, "seeds must be non-empty"
assert all(x.tracking_symbol in s.VALID_TRACKING_SYMBOLS for x in seeds), "invalid symbol"
assert all(x.estimate_basis for x in seeds), "missing estimate_basis"
assert all(x.tracking_factor == 1.0 for x in seeds), "default factor must be 1.0"
targets = {x.tracking_symbol for x in seeds}
assert {s.NASDAQ_FUT, s.SP500_FUT, s.DOW_FUT} <= targets, "must cover nasdaq/sp500/dow"
print("OK", len(seeds), "seeds; symbols:", sorted(targets))
