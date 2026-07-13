"""荐基全量横截面与研究档案的共享缓存。"""

from __future__ import annotations

from app.services.sector_quote_cache import (
    get_spot_snapshot,
    get_spot_snapshot_any_age,
    save_spot_snapshot,
)

_UNIVERSE_CACHE_KEY = "fund:discovery_universe:v3:utf8:20000"
_PROFILE_CACHE_KEY = "fund:discovery_profiles:v2:utf8"
_UNIVERSE_TTL_SECONDS = 24 * 60 * 60
_PROFILE_TTL_SECONDS = 36 * 60 * 60


def fetch_discovery_fund_universe_cached(*, limit: int = 20_000) -> list[dict]:
    """优先使用全量基金横截面；失败时由调用方回退到小排行榜。"""

    cached = get_spot_snapshot(
        _UNIVERSE_CACHE_KEY,
        ttl_seconds=_UNIVERSE_TTL_SECONDS,
    )
    if isinstance(cached, dict) and isinstance(cached.get("rows"), list):
        return list(cached["rows"])

    from app.services.akshare_subprocess import fetch_open_fund_universe

    rows = fetch_open_fund_universe(limit=limit, timeout_seconds=55) or []
    if rows:
        save_spot_snapshot(_UNIVERSE_CACHE_KEY, {"rows": rows})
        return rows

    stale = get_spot_snapshot_any_age(_UNIVERSE_CACHE_KEY)
    if isinstance(stale, dict) and isinstance(stale.get("rows"), list):
        return list(stale["rows"])
    return []


def fetch_fund_research_profiles_cached(fund_codes: list[str]) -> dict[str, dict]:
    """按代码返回候选准入字段，并把历次命中合并到跨用户共享缓存。"""

    codes = {
        str(code).strip().zfill(6)
        for code in fund_codes
        if str(code).strip().isdigit()
    }
    if not codes:
        return {}

    fresh = get_spot_snapshot(
        _PROFILE_CACHE_KEY,
        ttl_seconds=_PROFILE_TTL_SECONDS,
    )
    stale = get_spot_snapshot_any_age(_PROFILE_CACHE_KEY)
    source = fresh if isinstance(fresh, dict) else stale
    cached_rows = {
        str(row.get("fund_code") or "").zfill(6): dict(row)
        for row in ((source or {}).get("rows") or [])
        if isinstance(row, dict) and row.get("fund_code")
    }
    missing = sorted(code for code in codes if code not in cached_rows)
    if missing:
        from app.services.akshare_subprocess import fetch_open_fund_research_profiles

        fetched = fetch_open_fund_research_profiles(missing) or []
        for row in fetched:
            if not isinstance(row, dict):
                continue
            code = str(row.get("fund_code") or "").zfill(6)
            if code and code != "000000":
                cached_rows[code] = dict(row)
        if fetched:
            save_spot_snapshot(
                _PROFILE_CACHE_KEY,
                {"rows": list(cached_rows.values())},
            )
    return {code: cached_rows[code] for code in codes if code in cached_rows}
