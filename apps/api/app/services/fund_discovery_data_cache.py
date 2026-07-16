"""荐基全量横截面与研究档案的共享缓存。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import RLock

from app.services.sector_quote_cache import (
    get_spot_snapshot,
    get_spot_snapshot_any_age,
    save_spot_snapshot,
)

_UNIVERSE_CACHE_KEY = "fund:discovery_universe:v4:pit:20000"
_PROFILE_CACHE_KEY = "fund:discovery_profiles:v5:tracking-reference"
_UNIVERSE_TTL_SECONDS = 24 * 60 * 60
_PROFILE_TTL_SECONDS = 36 * 60 * 60
_INCOMPLETE_PROFILE_RETRY_SECONDS = 30 * 60
_PROFILE_REQUIRED_FIELDS = ("fund_scale_yi", "established_date", "fund_manager")
_PROFILE_REFRESH_LOCK = RLock()


def fetch_discovery_fund_universe_cached(*, limit: int = 20_000) -> list[dict]:
    """优先使用全量基金横截面；失败时由调用方回退到小排行榜。"""

    cached = get_spot_snapshot(
        _UNIVERSE_CACHE_KEY,
        ttl_seconds=_UNIVERSE_TTL_SECONDS,
    )
    if isinstance(cached, dict) and isinstance(cached.get("rows"), list):
        return _universe_rows_with_snapshot_contract(cached)

    from app.services.akshare_subprocess import fetch_open_fund_universe

    rows = fetch_open_fund_universe(limit=limit, timeout_seconds=55) or []
    if rows:
        snapshot = {
            "schema_version": "fund_universe_snapshot.v1",
            "snapshot_available_at": datetime.now(timezone.utc).isoformat(),
            "source": "eastmoney_open_fund_universe",
            "rows": rows,
        }
        save_spot_snapshot(_UNIVERSE_CACHE_KEY, snapshot)
        return _universe_rows_with_snapshot_contract(snapshot)

    stale = get_spot_snapshot_any_age(_UNIVERSE_CACHE_KEY)
    if isinstance(stale, dict) and isinstance(stale.get("rows"), list):
        return _universe_rows_with_snapshot_contract(stale)
    return []


def _universe_rows_with_snapshot_contract(payload: dict) -> list[dict]:
    """Expose one frozen availability instant for catalogue and rank fields."""

    available_at = payload.get("snapshot_available_at")
    source = str(payload.get("source") or "fund_universe_snapshot")
    result: list[dict] = []
    for raw in payload.get("rows") or []:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        if available_at:
            row.setdefault("membership_available_at", available_at)
            row.setdefault("snapshot_available_at", available_at)
            for field in (
                "return_3m_percent",
                "return_6m_percent",
                "return_1y_percent",
                "max_drawdown_1y_percent",
                "fund_scale_yi",
            ):
                if row.get(field) is not None:
                    row.setdefault(f"{field}_available_at", available_at)
                    row.setdefault(f"{field}_source", source)
        row.setdefault("source", source)
        result.append(row)
    return result


def fetch_fund_research_profiles_cached(fund_codes: list[str]) -> dict[str, dict]:
    """按代码返回候选准入字段，并把双源结果合并到跨用户共享缓存。

    完整缓存过期后必须重拉；不完整行按独立检查时点短周期重试。旧实现仅按
    ``fund_code`` 判断命中，会让过期或半空的行永久阻止刷新。
    """

    # 生产 Lighthouse 为单 worker；该锁同时防止同进程并发扫描重复拉源或以旧整包
    # 覆盖新整包。缓存仍保存在共享数据库中，重启后继续可用。
    with _PROFILE_REFRESH_LOCK:
        return _fetch_fund_research_profiles_cached_locked(fund_codes)


def _fetch_fund_research_profiles_cached_locked(fund_codes: list[str]) -> dict[str, dict]:

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
    cache_is_fresh = isinstance(fresh, dict)
    source = fresh if isinstance(fresh, dict) else stale
    cached_rows = {
        str(row.get("fund_code") or "").zfill(6): dict(row)
        for row in ((source or {}).get("rows") or [])
        if isinstance(row, dict) and row.get("fund_code")
    }
    now = datetime.now(timezone.utc)
    refresh_codes = sorted(
        code
        for code in codes
        if (
            not cache_is_fresh
            or code not in cached_rows
            or _profile_refresh_due(cached_rows[code], now=now)
        )
    )
    if refresh_codes:
        replace_existing_codes = {
            code
            for code in refresh_codes
            if (
                not cache_is_fresh
                or code not in cached_rows
                or (
                    not _missing_profile_fields(cached_rows[code])
                    and _profile_refresh_due(cached_rows[code], now=now)
                )
            )
        }
        from app.services.akshare_subprocess import (
            fetch_fund_basic_profiles_xq,
            fetch_open_fund_research_profiles,
        )

        # 两个源相互独立。并行拉取把冷缓存延迟限制在较慢的一方，同时避免
        # Sina 全表请求暂时失败时，整批候选都丢失规模和经理字段。
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="fund-profile-source") as executor:
            sina_future = executor.submit(
                fetch_open_fund_research_profiles,
                refresh_codes,
                timeout_seconds=35,
            )
            xq_future = executor.submit(
                fetch_fund_basic_profiles_xq,
                refresh_codes,
                timeout_seconds=35,
            )
            try:
                sina_rows = sina_future.result() or []
            except Exception:  # noqa: BLE001 - provider fallback is intentional
                sina_rows = []
            try:
                xq_rows = xq_future.result() or []
            except Exception:  # noqa: BLE001 - provider fallback is intentional
                xq_rows = []

        sina_by_code = _profile_rows_by_code(sina_rows, requested_codes=codes)
        xq_by_code = _profile_rows_by_code(xq_rows, requested_codes=codes)
        # A cold-start batch occasionally returns only the Sina scale row or no
        # XQ rows before the subprocess budget expires.  Retry only the codes
        # whose three decision fields are still incomplete.  This avoids
        # downgrading the whole candidate pool for a transient partial batch
        # while keeping a bounded fail-closed path when the provider is down.
        retry_codes = [
            code
            for code in refresh_codes
            if not _profile_sources_complete(
                code,
                sina_by_code=sina_by_code,
                xq_by_code=xq_by_code,
            )
        ]
        if retry_codes:
            try:
                retry_xq_rows = fetch_fund_basic_profiles_xq(
                    retry_codes,
                    timeout_seconds=20,
                ) or []
            except Exception:  # noqa: BLE001 - bounded provider retry is best-effort
                retry_xq_rows = []
            retry_xq_by_code = _profile_rows_by_code(
                retry_xq_rows,
                requested_codes=set(retry_codes),
            )
            for code, retry_row in retry_xq_by_code.items():
                xq_by_code[code] = _merge_profile_row(
                    xq_by_code.get(code),
                    retry_row,
                    prefer_incoming=True,
                )
        had_existing_profile = {code: code in cached_rows for code in refresh_codes}
        fresh_fields_by_code: dict[str, set[str]] = {}
        stale_fields_by_code: dict[str, list[str]] = {}

        for code in refresh_codes:
            # 先在空行上合成“本轮新鲜档案”：Sina 有效字段优先，XQ 逐字段补空。
            # 不能仅凭某源返回了 code 就认定成功，否则空壳行会把旧缓存误标 complete。
            fresh_profile: dict = {"fund_code": code}
            if code in sina_by_code:
                fresh_profile = _merge_profile_row(
                    fresh_profile,
                    sina_by_code[code],
                    prefer_incoming=True,
                )
            if code in xq_by_code:
                fresh_profile = _merge_profile_row(
                    fresh_profile,
                    xq_by_code[code],
                    prefer_incoming=False,
                )
            fresh_fields = _available_profile_fields(fresh_profile)
            fresh_fields_by_code[code] = fresh_fields
            if not fresh_fields:
                continue

            previous = dict(cached_rows.get(code) or {})
            merged = previous
            # Sina 本轮返回的字段始终可以更新旧值；此前 partial 行会对所有
            # 非空旧值一律拒绝覆盖，导致规模/经理即使已刷新也永久冻结。
            if code in sina_by_code:
                merged = _merge_profile_row(
                    merged,
                    sina_by_code[code],
                    prefer_incoming=True,
                )
            if code in xq_by_code:
                replace_xq_values = code in replace_existing_codes
                xq_row = dict(xq_by_code[code])
                current_sina = sina_by_code.get(code) or {}
                for primary_key in (
                    "fund_name",
                    "fund_category",
                    "fund_manager",
                    "established_date",
                    "fund_scale_yi",
                    "latest_nav",
                    "profile_updated_at",
                ):
                    if _has_value(current_sina.get(primary_key)):
                        xq_row.pop(primary_key, None)
                # XQ 本轮返回的份额是该源自己的最新观测，必须替换同源旧份额。
                # 不能沿用普通“只补空”合并，否则 partial 行补齐经理等字段时，
                # 旧份额会被新的 checked_at 一并续期并误标为完整新鲜档案。
                if _has_value(xq_row.get("fund_shares_yi")):
                    merged.pop("fund_shares_yi", None)
                    merged.pop("fund_shares_basis", None)
                # XQ 的 totshare 是份额而非 AUM。完整旧行已到期且 Sina
                # 本轮又没有规模时，清除旧规模，交给候选层用份额×最新净值
                # 重算；partial 行仍保留尚未到 36h 的已有 Sina 规模。
                if (
                    replace_xq_values
                    and not _has_value((sina_by_code.get(code) or {}).get("fund_scale_yi"))
                    and _has_value(xq_row.get("fund_shares_yi"))
                ):
                    merged.pop("fund_scale_yi", None)
                    merged.pop("fund_scale_basis", None)
                merged = _merge_profile_row(
                    merged,
                    xq_row,
                    prefer_incoming=replace_xq_values,
                )
            cached_rows[code] = merged
            if code in replace_existing_codes:
                stale_fields_by_code[code] = [
                    field
                    for field in _PROFILE_REQUIRED_FIELDS
                    if field not in fresh_fields
                    and (
                        _has_value(previous.get(field))
                        or (
                            field == "fund_scale_yi"
                            and _has_value(previous.get("fund_shares_yi"))
                        )
                    )
                ]

        checked_at = now.isoformat()
        for code in refresh_codes:
            row = dict(cached_rows.get(code) or {"fund_code": code})
            row["profile_checked_at"] = checked_at
            row["profile_missing_fields"] = _missing_profile_fields(row)
            stale_fields = stale_fields_by_code.get(code) or []
            if stale_fields:
                row["profile_stale_fields"] = stale_fields
            else:
                row.pop("profile_stale_fields", None)
            if not fresh_fields_by_code.get(code):
                row["profile_status"] = (
                    "stale_fallback"
                    if had_existing_profile[code] and any(
                        _has_value(row.get(field)) for field in _PROFILE_REQUIRED_FIELDS
                    )
                    else "unavailable"
                )
            elif row["profile_missing_fields"] or stale_fields:
                row["profile_status"] = "partial"
            else:
                row["profile_status"] = "complete"
            cached_rows[code] = row

        save_spot_snapshot(
            _PROFILE_CACHE_KEY,
            {"rows": list(cached_rows.values())},
        )
    return {code: cached_rows[code] for code in codes if code in cached_rows}


def _merge_profile_row(
    existing: dict | None,
    incoming: dict,
    *,
    prefer_incoming: bool = False,
) -> dict:
    merged = dict(existing or {})
    code = str(incoming.get("fund_code") or merged.get("fund_code") or "").zfill(6)
    merged["fund_code"] = code
    source = str(incoming.get("profile_source") or "").strip()
    used_source = False
    for key in (
        "fund_name",
        "fund_category",
        "fund_manager",
        "established_date",
        "fund_scale_yi",
        "fund_shares_yi",
        "fund_shares_basis",
        "tracking_reference_text",
        "benchmark_text",
        "benchmark_text_kind",
        "benchmark_text_source_kind",
        "latest_nav",
        "profile_updated_at",
    ):
        if _has_value(incoming.get(key)) and (
            prefer_incoming or not _has_value(merged.get(key))
        ):
            merged[key] = incoming[key]
            used_source = True
            if key == "fund_scale_yi" and _has_value(incoming.get("fund_scale_basis")):
                merged["fund_scale_basis"] = incoming["fund_scale_basis"]

    if used_source:
        sources = [str(item) for item in merged.get("profile_sources") or [] if str(item)]
        incoming_sources = [
            str(item) for item in incoming.get("profile_sources") or [] if str(item)
        ]
        if source:
            incoming_sources.insert(0, source)
        for item in incoming_sources:
            if item not in sources:
                sources.append(item)
        merged["profile_sources"] = sources
        if source and (prefer_incoming or not merged.get("profile_source")):
            merged["profile_source"] = source
    return merged


def _profile_rows_by_code(rows: list[dict], *, requested_codes: set[str]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("fund_code") or "").zfill(6)
        if code in requested_codes and code != "000000":
            result[code] = dict(row)
    return result


def _profile_sources_complete(
    code: str,
    *,
    sina_by_code: dict[str, dict],
    xq_by_code: dict[str, dict],
) -> bool:
    combined: dict = {"fund_code": code}
    if code in sina_by_code:
        combined = _merge_profile_row(
            combined,
            sina_by_code[code],
            prefer_incoming=True,
        )
    if code in xq_by_code:
        combined = _merge_profile_row(
            combined,
            xq_by_code[code],
            prefer_incoming=False,
        )
    return len(_available_profile_fields(combined)) == len(_PROFILE_REQUIRED_FIELDS)


def _missing_profile_fields(row: dict) -> list[str]:
    return [
        field
        for field in _PROFILE_REQUIRED_FIELDS
        if field not in _available_profile_fields(row)
    ]


def _available_profile_fields(row: dict) -> set[str]:
    available = {
        field
        for field in _PROFILE_REQUIRED_FIELDS
        if _has_value(row.get(field))
    }
    # 蛋卷 totshare 只有份额口径；它可以作为规模估算的输入，但绝不能
    # 直接当作亿元 AUM。候选层还需取得有效 latest_nav 才会生成规模值。
    if _has_value(row.get("fund_shares_yi")):
        available.add("fund_scale_yi")
    return available


def _profile_refresh_due(row: dict, *, now: datetime) -> bool:
    retryable_status = str(row.get("profile_status") or "") in {
        "partial",
        "stale_fallback",
        "unavailable",
    }
    checked_at = row.get("profile_checked_at")
    if not checked_at:
        return True
    try:
        parsed = datetime.fromisoformat(str(checked_at).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    ttl_seconds = (
        _INCOMPLETE_PROFILE_RETRY_SECONDS
        if retryable_status or _missing_profile_fields(row)
        else _PROFILE_TTL_SECONDS
    )
    return (now - parsed.astimezone(timezone.utc)).total_seconds() >= ttl_seconds


def _has_value(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in {"", "none", "null", "nan", "--", "—"}
    return True
