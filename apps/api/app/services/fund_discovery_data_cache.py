"""荐基全量横截面与研究档案的共享缓存。"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from threading import RLock, Thread

from app.database import (
    get_fund_daily_catalogue_meta,
    list_fund_daily_catalogue,
    replace_fund_daily_catalogue,
)
from app.services.cache_policy import jittered_ttl
from app.services.cross_process_lock import CrossProcessLockError, cross_process_lock
from app.services.sector_quote_cache import (
    get_spot_snapshot,
    get_spot_snapshot_any_age,
    save_spot_snapshot,
)
from app.services.shared_executors import get_shared_io_executor

_UNIVERSE_CACHE_KEY = "fund:discovery_universe:v4:pit:20000"
_PROFILE_CACHE_KEY = "fund:discovery_profiles:v5:tracking-reference"
_UNIVERSE_SNAPSHOT_SOURCE = "eastmoney_fund_catalogue_with_optional_rank_enrichment"
_UNIVERSE_STAMPED_METRIC_FIELDS = (
    "return_3m_percent",
    "return_6m_percent",
    "return_1y_percent",
    "return_3y_percent",
    "max_drawdown_1y_percent",
    "fund_scale_yi",
)
_UNIVERSE_TTL_SECONDS = 24 * 60 * 60
# 只读路径向日频表 meta 复验的间隔。目录是日粒度，60s 足够，避免每请求扫全表。
_UNIVERSE_MEMORY_REVALIDATE_SECONDS = 60.0
_PROFILE_TTL_SECONDS = 36 * 60 * 60
_INCOMPLETE_PROFILE_RETRY_SECONDS = 30 * 60
_PROFILE_REQUIRED_FIELDS = ("fund_scale_yi", "established_date", "fund_manager")
_PROFILE_REFRESH_LOCK = RLock()
_UNIVERSE_FETCH_LOCK = RLock()
_UNIVERSE_REFRESH_STATE_LOCK = RLock()
_UNIVERSE_REFRESH_IN_FLIGHT = False
_UNIVERSE_MEMORY_LOCK = RLock()
_UNIVERSE_MEMORY_PAYLOAD: dict | None = None
_UNIVERSE_MEMORY_LOADED_AT = 0.0

logger = logging.getLogger(__name__)


def fetch_discovery_fund_universe_cached(*, limit: int = 20_000) -> list[dict]:
    """Return one request-pinnable full-universe snapshot.

    ``fund_daily_catalogue`` is the authority. A fresh snapshot is preferred.
    Once it expires, keep serving the last frozen table snapshot and refresh
    it in the background. Discovery freezes its decision clock after this
    function returns, so one request must not swap to a snapshot captured
    later while it is already being evaluated.

    On a true cold start the table is empty. The first discovery caller
    therefore performs one bounded synchronous fetch; concurrent callers share
    the fetch lock and reuse the saved result. Daily-report callers must use
    ``fetch_discovery_fund_universe_cache_only`` instead.
    """

    payload = _read_catalogue_payload(revalidate=True)
    if payload is None:
        payload = _import_legacy_universe_blob_if_needed()
    if payload is not None:
        if not _universe_snapshot_is_fresh(payload):
            _schedule_discovery_universe_refresh(limit=limit)
        return _universe_rows_with_research_overlay(
            _universe_rows_with_snapshot_contract(payload)
        )
    return _universe_rows_with_research_overlay(
        _refresh_discovery_universe_blocking(limit=limit, force=False)
    )


def fetch_discovery_fund_universe_cache_only() -> list[dict]:
    """只读日频目录表；表空时最多导入一次旧 JSON blob，**绝不触发拉源**。

    日报给持仓算同类分位要用同一份目录，但不能走
    `fetch_discovery_fund_universe_cached`——冷启动时它会做一次有界但阻塞的全量拉取
    （`_refresh_discovery_universe_blocking`），那属于荐基扫描可以承受、日报请求路径
    不能承受的代价。缓存缺席时上层 fail closed 到"同类分位不可用"，不猜、不拉源。

    与 `theme_board_snapshot.get_theme_board_snapshot_cache_only` 同一约定：
    函数名里的 `cache_only` 就是"不会有网络副作用"的承诺。
    """

    payload = _read_catalogue_payload(revalidate=True)
    if payload is None:
        payload = _import_legacy_universe_blob_if_needed()
    if payload is None or not _valid_universe_snapshot(payload):
        return []
    return _universe_rows_with_research_overlay(
        _universe_rows_with_snapshot_contract(payload)
    )


def _valid_universe_snapshot(payload: object) -> bool:
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("rows"), list)
        and bool(payload.get("rows"))
    )


def _universe_snapshot_is_fresh(
    payload: object,
    *,
    now: datetime | None = None,
) -> bool:
    if not _valid_universe_snapshot(payload):
        return False
    try:
        captured_at = datetime.fromisoformat(
            str(payload.get("snapshot_available_at") or "").replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return False
    if captured_at.tzinfo is None:
        return False
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age_seconds = (current - captured_at.astimezone(timezone.utc)).total_seconds()
    # Snapshot freshness is part of the point-in-time evidence contract and
    # must stay deterministic. Jitter only controls cache refresh timing.
    return 0 <= age_seconds <= _UNIVERSE_TTL_SECONDS


def _build_universe_snapshot(rows: list[dict]) -> dict:
    available_at = datetime.now(timezone.utc).isoformat()
    source = _UNIVERSE_SNAPSHOT_SOURCE
    return {
        "schema_version": "fund_universe_snapshot.v1",
        "snapshot_available_at": available_at,
        "source": source,
        "rows": [
            _stamp_universe_row(row, available_at=available_at, source=source)
            for row in rows
            if isinstance(row, dict)
        ],
    }


def _reset_universe_memory_for_tests() -> None:
    global _UNIVERSE_MEMORY_PAYLOAD, _UNIVERSE_MEMORY_LOADED_AT
    with _UNIVERSE_MEMORY_LOCK:
        _UNIVERSE_MEMORY_PAYLOAD = None
        _UNIVERSE_MEMORY_LOADED_AT = 0.0


def _pin_universe_payload(payload: dict) -> None:
    global _UNIVERSE_MEMORY_PAYLOAD, _UNIVERSE_MEMORY_LOADED_AT
    with _UNIVERSE_MEMORY_LOCK:
        _UNIVERSE_MEMORY_PAYLOAD = payload
        _UNIVERSE_MEMORY_LOADED_AT = time.monotonic()


def _load_catalogue_payload() -> dict | None:
    meta = get_fund_daily_catalogue_meta()
    if meta is None:
        return None
    rows = list_fund_daily_catalogue()
    if not rows:
        return None
    available_at = str(meta["snapshot_available_at"])
    source = str(meta.get("source") or _UNIVERSE_SNAPSHOT_SOURCE)
    return {
        "schema_version": "fund_universe_snapshot.v1",
        "snapshot_available_at": available_at,
        "source": source,
        "rows": [
            _stamp_universe_row(row, available_at=available_at, source=source)
            for row in rows
            if isinstance(row, dict)
        ],
    }


def _read_catalogue_payload(*, revalidate: bool) -> dict | None:
    global _UNIVERSE_MEMORY_PAYLOAD, _UNIVERSE_MEMORY_LOADED_AT
    now = time.monotonic()
    with _UNIVERSE_MEMORY_LOCK:
        cached = _UNIVERSE_MEMORY_PAYLOAD
        if cached is not None and (
            not revalidate
            or now - _UNIVERSE_MEMORY_LOADED_AT < _UNIVERSE_MEMORY_REVALIDATE_SECONDS
        ):
            return cached

    meta = get_fund_daily_catalogue_meta()
    with _UNIVERSE_MEMORY_LOCK:
        cached = _UNIVERSE_MEMORY_PAYLOAD
        cached_stamp = str((cached or {}).get("snapshot_available_at") or "")
        meta_stamp = str((meta or {}).get("snapshot_available_at") or "")
        if cached is not None and meta is not None and cached_stamp == meta_stamp:
            _UNIVERSE_MEMORY_LOADED_AT = time.monotonic()
            return cached
        if meta is None:
            _UNIVERSE_MEMORY_PAYLOAD = None
            _UNIVERSE_MEMORY_LOADED_AT = 0.0
            return None

    payload = _load_catalogue_payload()
    if payload is None:
        return None
    _pin_universe_payload(payload)
    return payload


def _import_legacy_universe_blob_if_needed() -> dict | None:
    """One-time import of the old 20k JSON blob. Never hits Eastmoney."""

    if get_fund_daily_catalogue_meta() is not None:
        return _read_catalogue_payload(revalidate=False)
    blob = get_spot_snapshot_any_age(_UNIVERSE_CACHE_KEY)
    if not _valid_universe_snapshot(blob):
        return None
    rows = [row for row in blob.get("rows") or [] if isinstance(row, dict)]
    available_at = str(blob.get("snapshot_available_at") or "").strip()
    source = str(blob.get("source") or "legacy_sector_spot_cache").strip()
    if not rows or not available_at:
        return None
    replace_fund_daily_catalogue(
        rows,
        snapshot_available_at=available_at,
        source=source or "legacy_sector_spot_cache",
    )
    payload = _load_catalogue_payload()
    if payload is None:
        return None
    _pin_universe_payload(payload)
    return payload


def _refresh_discovery_universe_blocking(
    *,
    limit: int,
    force: bool,
) -> list[dict]:
    """Refresh the catalogue once and return the frozen row contract."""

    with _UNIVERSE_FETCH_LOCK:
        try:
            with cross_process_lock(
                f"discovery-universe-refresh:{_UNIVERSE_CACHE_KEY}",
                timeout_seconds=3.0,
            ):
                return _refresh_discovery_universe_under_lock(
                    limit=limit,
                    force=force,
                )
        except CrossProcessLockError:
            payload = _read_catalogue_payload(revalidate=True)
            if payload is None:
                payload = _import_legacy_universe_blob_if_needed()
            if payload is not None:
                return _universe_rows_with_snapshot_contract(payload)
            return []


def _refresh_discovery_universe_under_lock(
    *,
    limit: int,
    force: bool,
) -> list[dict]:
    # A different worker may have refreshed while this worker waited. Capture
    # time, rather than the in-memory promotion time, is the authority.
    cached = _read_catalogue_payload(revalidate=True)
    if not force and cached is not None and _universe_snapshot_is_fresh(cached):
        return _universe_rows_with_snapshot_contract(cached)

    from app.services.akshare_subprocess import fetch_open_fund_universe

    try:
        rows = fetch_open_fund_universe(limit=limit, timeout_seconds=55) or []
    except Exception:  # noqa: BLE001 - stale fallback is intentional here.
        logger.exception("discovery fund universe refresh failed")
        rows = []
    if rows:
        snapshot = _build_universe_snapshot(rows)
        replace_fund_daily_catalogue(
            snapshot["rows"],
            snapshot_available_at=str(snapshot["snapshot_available_at"]),
            source=str(snapshot.get("source") or _UNIVERSE_SNAPSHOT_SOURCE),
        )
        _pin_universe_payload(snapshot)
        _schedule_nav_series_maintenance()
        return _universe_rows_with_snapshot_contract(snapshot)

    if cached is not None:
        return _universe_rows_with_snapshot_contract(cached)
    imported = _import_legacy_universe_blob_if_needed()
    if imported is not None:
        return _universe_rows_with_snapshot_contract(imported)
    return []


def _schedule_nav_series_maintenance() -> None:
    from app.services.fund_nav_series import (
        schedule_daily_nav_series_sync,
        schedule_nav_series_backfill,
    )

    schedule_daily_nav_series_sync()
    schedule_nav_series_backfill()


def _schedule_risk_metrics_refresh() -> None:
    """兼容旧调用：目录刷新后改走全市场净值日更 + 回填。"""

    _schedule_nav_series_maintenance()


def _run_risk_metrics_sidecar() -> None:
    try:
        from app.services.fund_nav_series import run_daily_nav_series_and_risk

        run_daily_nav_series_and_risk()
    except Exception:  # noqa: BLE001
        logger.exception("scheduled fund nav series daily sync failed")


def _schedule_discovery_universe_refresh(*, limit: int) -> None:
    """Refresh an expired snapshot without replacing the current request's rows."""

    global _UNIVERSE_REFRESH_IN_FLIGHT
    with _UNIVERSE_REFRESH_STATE_LOCK:
        if _UNIVERSE_REFRESH_IN_FLIGHT:
            return
        _UNIVERSE_REFRESH_IN_FLIGHT = True
    try:
        Thread(
            target=_run_scheduled_discovery_universe_refresh,
            kwargs={"limit": limit},
            name="discovery-fund-universe-refresh",
            daemon=True,
        ).start()
    except Exception:  # noqa: BLE001 - leave the next request free to retry.
        with _UNIVERSE_REFRESH_STATE_LOCK:
            _UNIVERSE_REFRESH_IN_FLIGHT = False
        logger.exception("failed to start discovery universe refresh thread")


def _run_scheduled_discovery_universe_refresh(*, limit: int) -> None:
    global _UNIVERSE_REFRESH_IN_FLIGHT
    try:
        # ``get_spot_snapshot_any_age`` promotes the stale value into the
        # process cache. ``force=True`` prevents that promoted value from
        # short-circuiting the actual refresh.
        _refresh_discovery_universe_blocking(limit=limit, force=True)
    except Exception:  # noqa: BLE001 - the pinned stale snapshot stays usable.
        logger.exception("scheduled discovery universe refresh failed")
    finally:
        with _UNIVERSE_REFRESH_STATE_LOCK:
            _UNIVERSE_REFRESH_IN_FLIGHT = False


def _stamp_universe_row(
    raw: dict,
    *,
    available_at: str,
    source: str,
) -> dict:
    """Copy one catalogue row and freeze its PIT timestamps."""

    row = dict(raw)
    row["membership_available_at"] = available_at
    row["snapshot_available_at"] = available_at
    for field in _UNIVERSE_STAMPED_METRIC_FIELDS:
        if row.get(field) is not None:
            row[f"{field}_available_at"] = available_at
            row.setdefault(f"{field}_source", source)
    row.setdefault("source", source)
    return row


def _universe_rows_already_stamped(payload: dict) -> bool:
    available_at = payload.get("snapshot_available_at")
    if not available_at:
        return False
    for raw in payload.get("rows") or []:
        if not isinstance(raw, dict):
            continue
        return (
            raw.get("membership_available_at") == available_at
            and raw.get("snapshot_available_at") == available_at
        )
    return False


def _universe_rows_with_snapshot_contract(payload: dict) -> list[dict]:
    """Expose one frozen availability instant for catalogue and rank fields.

    Fresh snapshots are stamped once at write time. Callers must treat the
    returned rows as read-only: they may be the same dicts held by the
    process cache.
    """

    raw_rows = payload.get("rows") or []
    if _universe_rows_already_stamped(payload):
        return [row for row in raw_rows if isinstance(row, dict)]

    available_at = str(payload.get("snapshot_available_at") or "")
    source = str(payload.get("source") or "fund_universe_snapshot")
    result: list[dict] = []
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        result.append(
            _stamp_universe_row(raw, available_at=available_at, source=source)
            if available_at
            else dict(raw)
        )
    return result


def _universe_rows_with_research_overlay(rows: list[dict]) -> list[dict]:
    from app.services.fund_research_profile_store import overlay_research_on_universe_rows

    return overlay_research_on_universe_rows(rows)


def fetch_fund_research_profiles_cached(fund_codes: list[str]) -> dict[str, dict]:
    """按代码返回候选准入字段。规模/经理只读研究档案表，缺经理/成立日再走雪球。

    新浪四张全表由后台循环/定时任务整包刷新，荐基请求路径不再拉规模源。
    """

    from app.services.fund_research_profile_store import (
        ensure_fund_research_profiles,
        list_research_profiles_for_codes,
        profile_row_for_candidate,
    )

    codes = {
        str(code).strip().zfill(6)
        for code in fund_codes
        if str(code).strip().isdigit()
    }
    if not codes:
        return {}

    ensure_fund_research_profiles()
    from app.services.fund_manager_roster import list_manager_roster_by_codes

    roster = list_manager_roster_by_codes(codes)
    result = {
        code: profile_row_for_candidate(row, managers=roster.get(code))
        for code, row in list_research_profiles_for_codes(codes).items()
    }
    missing = [
        code
        for code in codes
        if _missing_profile_fields(result.get(code) or {})
    ]
    if not missing:
        return {code: result[code] for code in codes if code in result}

    with _PROFILE_REFRESH_LOCK:
        try:
            with cross_process_lock(
                f"discovery-profile-refresh:{_PROFILE_CACHE_KEY}",
                timeout_seconds=3.0,
            ):
                hole_rows = _fetch_fund_research_profiles_cached_locked(
                    missing,
                    skip_sina=True,
                )
        except CrossProcessLockError:
            hole_rows = _cached_profile_rows(missing)
    for code, hole in hole_rows.items():
        existing = result.get(code)
        result[code] = (
            _merge_table_and_hole_profile(existing, hole)
            if existing
            else hole
        )
    from app.services.fund_manager_roster import attach_manager_roster_to_rows

    attach_manager_roster_to_rows(result)
    return {code: result[code] for code in codes if code in result}


def _merge_table_and_hole_profile(existing: dict, hole: dict) -> dict:
    merged = _merge_profile_row(existing, hole, prefer_incoming=False)
    merged["profile_missing_fields"] = _missing_profile_fields(merged)
    stale_fields = [
        str(field)
        for field in hole.get("profile_stale_fields") or []
        if str(field)
    ]
    if stale_fields:
        merged["profile_stale_fields"] = stale_fields
    else:
        merged.pop("profile_stale_fields", None)
    if hole.get("profile_checked_at"):
        merged["profile_checked_at"] = hole["profile_checked_at"]
    if hole.get("profile_source") and not merged.get("profile_source"):
        merged["profile_source"] = hole["profile_source"]
    if hole.get("profile_sources"):
        merged["profile_sources"] = hole["profile_sources"]
    if merged["profile_missing_fields"] or stale_fields:
        merged["profile_status"] = str(hole.get("profile_status") or "partial")
    else:
        merged["profile_status"] = "complete"
    return merged


def _cached_profile_rows(fund_codes: list[str]) -> dict[str, dict]:
    requested = {
        str(code).strip().zfill(6)
        for code in fund_codes
        if str(code).strip().isdigit()
    }
    payload = get_spot_snapshot_any_age(_PROFILE_CACHE_KEY)
    return {
        code: dict(row)
        for row in ((payload or {}).get("rows") or [])
        if isinstance(row, dict)
        and (code := str(row.get("fund_code") or "").zfill(6)) in requested
    }


def _fetch_fund_research_profiles_cached_locked(
    fund_codes: list[str],
    *,
    skip_sina: bool = True,
) -> dict[str, dict]:

    codes = {
        str(code).strip().zfill(6)
        for code in fund_codes
        if str(code).strip().isdigit()
    }
    if not codes:
        return {}

    fresh = get_spot_snapshot(
        _PROFILE_CACHE_KEY,
        ttl_seconds=jittered_ttl(
            _PROFILE_CACHE_KEY,
            _PROFILE_TTL_SECONDS,
        ),
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

        # 规模整包只由后台/定时任务写表。请求路径永远 skip_sina，只走雪球
        # 补经理/成立日；Sina 全表失败不得拖住荐基。
        executor = get_shared_io_executor()
        sina_future = (
            None
            if skip_sina
            else executor.submit(
                fetch_open_fund_research_profiles,
                refresh_codes,
                timeout_seconds=35,
            )
        )
        xq_future = executor.submit(
            fetch_fund_basic_profiles_xq,
            refresh_codes,
            timeout_seconds=35,
        )
        try:
            sina_rows = (sina_future.result() or []) if sina_future is not None else []
        except Exception:  # noqa: BLE001 - provider fallback is intentional
            sina_rows = []
        try:
            xq_rows = xq_future.result() or []
        except Exception:  # noqa: BLE001 - provider fallback is intentional
            xq_rows = []
        finally:
            if sina_future is not None:
                sina_future.cancel()
            xq_future.cancel()

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
                # 本轮又没有规模时，清除旧规模，交给候选层用份额重算；
                # partial 行仍保留尚未到 36h 的已有 Sina 规模。
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
    # 直接当作亿元 AUM。候选层用报告期单位净值才能生成季报净资产。
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
