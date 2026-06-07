from __future__ import annotations

from typing import Any


def migrate_legacy_holding_payload(data: Any) -> Any:
    """旧版把官方净值写在 sector 字段；加载时迁到 daily 并清空 sector 待刷新。"""
    if not isinstance(data, dict):
        return data
    if data.get("sector_return_percent_source") != "official_nav":
        return data

    payload = dict(data)
    nav = payload.get("sector_return_percent")
    if payload.get("daily_return_percent") is None and nav is not None:
        payload["daily_return_percent"] = nav
    amount = float(payload.get("holding_amount") or 0)
    if payload.get("daily_profit") is None and nav is not None and amount > 0:
        payload["daily_profit"] = round(amount * nav / (100 + nav), 2)
    if payload.get("daily_return_percent_source") is None:
        payload["daily_return_percent_source"] = "official_nav"

    payload["sector_return_percent"] = None
    payload["sector_return_percent_source"] = None
    return payload
