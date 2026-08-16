"""PIT 成员并集必须覆盖周度旋转后的真实规模，不能卡在旧的 5000 上限。"""

from __future__ import annotations

import pytest

from scripts.fetch_factor_ic_nav_observations import MAX_CODES, extract_pit_fund_codes


def _payload(codes: list[str]) -> dict:
    midpoint = max(1, len(codes) // 2)
    return {
        "snapshots": [
            {
                "members": [{"fund_code": code} for code in codes[:midpoint]],
            },
            {
                "members": [{"fund_code": code} for code in codes[midpoint // 2 :]],
            },
        ]
    }


def test_extract_pit_fund_codes_accepts_production_union_size() -> None:
    codes = [f"{index:06d}" for index in range(5_351)]
    result = extract_pit_fund_codes(_payload(codes))
    assert result == codes
    assert len(result) > 5_000


def test_extract_pit_fund_codes_accepts_catalogue_ceiling() -> None:
    codes = [f"{index:06d}" for index in range(MAX_CODES)]
    assert len(extract_pit_fund_codes(_payload(codes))) == MAX_CODES


def test_extract_pit_fund_codes_rejects_above_catalogue_ceiling() -> None:
    codes = [f"{index:06d}" for index in range(MAX_CODES + 1)]
    with pytest.raises(ValueError, match="maximum is 25000"):
        extract_pit_fund_codes(_payload(codes))
