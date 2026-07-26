# 基金涨跌分布盘中实时口径 + 15 分钟缓存 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交易日连续交易时段用东方财富实时估值（`fund_value_estimation_em`）按估算增长率分桶给出当日盘中实时基金涨跌分布；其余时段沿用官方净值口径；前端加 15 分钟定时 + visibility + localStorage/sessionStorage 缓存，按 `is_continuous_trading` 闸门避免空跑。

**Architecture:** 后端单端点 `GET /api/diagnostics/fund-return-distribution` 按 `build_trading_session()` 的 `is_continuous_trading` 在两个数据源间切换：盘中→`fund_value_estimation_em` 估算增长率分桶（`source_mode="intraday_estimate"`，服务端 10 分钟 TTL）；其余→现有官方净值（`source_mode="official_nav"`，30 分钟 TTL）。两条分支共用三级回退（缓存→拉取→stale 回退→unavailable）。前端 `FundReturnDistributionPanel` 用 `useCachedFetch(storage="session", bootstrap=localStorage)` 秒开，15 分钟 `setInterval` + `visibilitychange`，每 tick 拉 `fetchTradingSession` 闸门。

**Tech Stack:** Python/FastAPI（后端）、akshare 子进程（`run_akshare_json_script`）、`sector_quote_cache`（spot 快照缓存）、React 19/Next 16、`useCachedFetch`、vitest + jsdom。

**Spec:** `docs/superpowers/specs/2026-07-26-fund-return-distribution-intraday-cache-design.md`

---

## File Structure

**Backend (apps/api):**
- Modify: `app/services/fund_return_distribution.py` — 抽取共享校验/构建辅助、新增 `_fetch_intraday_estimate_distribution`、按 session 路由。
- Untouched: `app/routes/market_diagnostics.py`（路由签名不变；前端 fetcher 已用 `cache:"no-store"`，无需服务端 Cache-Control 头）。
- Test: `apps/api/tests/test_market_breadth_distribution.py` — 新增盘中分支测试，更新既有 official 测试钉住 session。

**Frontend (apps/web):**
- Modify: `src/lib/api/marketDiagnostics.ts` — `FundReturnDistribution.source_mode` 联合类型 + `as_of_datetime`。
- Modify: `src/lib/api.ts` — `TradingSession` 补 `is_continuous_trading`。
- Modify: `src/lib/storage.ts` — 新增 `load/saveFundReturnDistributionCache`。
- Modify: `src/components/FundReturnDistributionPanel.tsx` — session 存储 + bootstrap + 15min 定时 + visibility + 闸门 + 副标题口径。
- Test: `src/components/FundReturnDistributionPanel.test.tsx` — 闸门/定时/bootstrap/口径测试。

---

## Task 1: 抽取共享分布校验辅助（refactor，行为不变）

**Why:** 官方净值与盘中估值两条 fetcher 的"bins 合计 == valid_count / advance+decline+flat == valid_count / 数值规范化"逻辑完全相同，先抽出来供 Task 2 复用，避免重复。既有 official 测试是安全网。

**Files:**
- Modify: `apps/api/app/services/fund_return_distribution.py`
- Test: `apps/api/tests/test_market_breadth_distribution.py`（既有测试不变，作回归）

- [ ] **Step 1: 读现有 `_fetch_official_distribution` 的校验段（165–206 行），确认要抽取的边界**

Run: `grep -n "def _fetch_official_distribution\|def _as_non_negative_int\|def _as_float" apps/api/app/services/fund_return_distribution.py`

- [ ] **Step 2: 抽取 `_normalize_distribution_counts`**

在 `fund_return_distribution.py` 中 `_fetch_official_distribution` 之前新增（把 165–206 行的校验逻辑原样搬入，返回 dict 或 None）：

```python
_DISTRIBUTION_BIN_KEYS = (
    "le_neg5", "neg5_neg3", "neg3_neg1", "neg1_zero",
    "zero", "zero_one", "one_three", "three_five", "ge_five",
)


def _normalize_distribution_counts(payload: dict) -> dict | None:
    """校验 akshare 子进程回传的分布计数，失败返回 None。

    官方净值与盘中估值两条 fetcher 共用：bins 合计必须等于 valid_count，
    advance+decline+flat 也必须等于 valid_count，任一不符即视为本次拉取失败。
    """
    bins = payload.get("bins")
    valid_count = _as_non_negative_int(payload.get("valid_count"))
    if not isinstance(bins, dict) or valid_count is None or valid_count <= 0:
        return None
    normalized_bins = {
        key: _as_non_negative_int(bins.get(key)) or 0 for key in _DISTRIBUTION_BIN_KEYS
    }
    if sum(normalized_bins.values()) != valid_count:
        return None
    advance_count = _as_non_negative_int(payload.get("advance_count")) or 0
    decline_count = _as_non_negative_int(payload.get("decline_count")) or 0
    flat_count = _as_non_negative_int(payload.get("flat_count")) or 0
    if advance_count + decline_count + flat_count != valid_count:
        return None
    source_row_count = _as_non_negative_int(payload.get("source_row_count")) or valid_count
    missing_count = _as_non_negative_int(payload.get("missing_count")) or 0
    coverage_percent = _as_float(payload.get("coverage_percent"))
    return {
        "as_of_date": str(payload.get("as_of_date") or "")[:10] or None,
        "source_row_count": source_row_count,
        "valid_count": valid_count,
        "missing_count": missing_count,
        "coverage_percent": coverage_percent,
        "advance_count": advance_count,
        "decline_count": decline_count,
        "flat_count": flat_count,
        "bins": normalized_bins,
    }
```

- [ ] **Step 3: 让 `_fetch_official_distribution` 复用它**

把 `_fetch_official_distribution` 末尾（165–206 行的 `bins = payload.get("bins")` 起到 `return {...}` 止）替换为：

```python
    return _normalize_distribution_counts(payload)
```

删除原 165–206 行内被替换的重复逻辑。保留 `payload = run_akshare_json_script(...)` 与 `if not isinstance(payload, dict) or payload.get("error"): return None` 两段不变。

- [ ] **Step 4: 跑既有 official 测试，确认行为不变**

Run: `cd apps/api && python -m pytest tests/test_market_breadth_distribution.py -v`
Expected: PASS（`test_official_fund_distribution_requires_conservation_and_records_scope`、`test_official_fund_distribution_rejects_non_conserving_payload` 等全绿）。

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/fund_return_distribution.py
git commit -m "refactor(api): 抽取共享分布计数校验辅助"
```

---

## Task 2: 新增 `_fetch_intraday_estimate_distribution`（TDD）

**Files:**
- Modify: `apps/api/app/services/fund_return_distribution.py`
- Test: `apps/api/tests/test_market_breadth_distribution.py`

- [ ] **Step 1: 写失败测试**

在 `test_market_breadth_distribution.py` 末尾追加：

```python
def test_intraday_estimate_fetcher_bins_estimated_growth(monkeypatch):
    monkeypatch.setattr(
        fund_return_distribution,
        "run_akshare_json_script",
        lambda *a, **k: {
            "as_of_date": "2026-07-26",
            "source_row_count": 12,
            "valid_count": 9,
            "missing_count": 3,
            "coverage_percent": 75.0,
            "advance_count": 4,
            "decline_count": 4,
            "flat_count": 1,
            "bins": {
                "le_neg5": 1, "neg5_neg3": 0, "neg3_neg1": 1, "neg1_zero": 2,
                "zero": 1, "zero_one": 2, "one_three": 1, "three_five": 0, "ge_five": 1,
            },
        },
    )
    result = fund_return_distribution._fetch_intraday_estimate_distribution(timeout=1.0)
    assert result is not None
    assert result["as_of_date"] == "2026-07-26"
    assert result["valid_count"] == 9
    assert sum(result["bins"].values()) == 9


def test_intraday_estimate_fetcher_rejects_non_conserving_payload(monkeypatch):
    monkeypatch.setattr(
        fund_return_distribution,
        "run_akshare_json_script",
        lambda *a, **k: {
            "as_of_date": "2026-07-26",
            "valid_count": 9,
            "advance_count": 4, "decline_count": 4, "flat_count": 1,
            "bins": {"zero": 1},  # 合计 != valid_count
        },
    )
    result = fund_return_distribution._fetch_intraday_estimate_distribution(timeout=1.0)
    assert result is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd apps/api && python -m pytest tests/test_market_breadth_distribution.py::test_intraday_estimate_fetcher_bins_estimated_growth -v`
Expected: FAIL with `AttributeError: module 'app.services.fund_return_distribution' has no attribute '_fetch_intraday_estimate_distribution'`

- [ ] **Step 3: 实现 `_fetch_intraday_estimate_distribution`**

在 `fund_return_distribution.py` 的 `_fetch_official_distribution` 之后新增（akshare 子进程脚本调 `ak.fund_value_estimation_em("全部")`，按估算增长率列分桶，复用 `_normalize_distribution_counts`）：

```python
def _fetch_intraday_estimate_distribution(*, timeout: float) -> dict | None:
    # 盘中实时估值：ak.fund_value_estimation_em 返回的估算增长率列名形如
    # "YYYY-MM-DD-估算数据-估算增长率"（日期动态），子进程内按列名后缀定位。
    # 在子进程内聚合 2 万行，只回传小 JSON，主进程不接大表（对齐官方净值分支）。
    script = r'''
import json
import akshare as ak

try:
    frame = ak.fund_value_estimation_em(symbol="全部")
    if frame is None or frame.empty:
        print(json.dumps({"error": "empty"}))
    else:
        growth_col = None
        for col in frame.columns:
            if str(col).endswith("-估算数据-估算增长率"):
                growth_col = col
                break
        estimate_date = None
        for col in frame.columns:
            if str(col) == "估算日期":
                estimate_date = col
                break
        as_of_date = None
        if estimate_date is not None:
            for value in frame[estimate_date]:
                if value is not None and str(value).strip():
                    as_of_date = str(value)[:10]
                    break

        bins = {
            "le_neg5": 0, "neg5_neg3": 0, "neg3_neg1": 0, "neg1_zero": 0,
            "zero": 0, "zero_one": 0, "one_three": 0, "three_five": 0, "ge_five": 0,
        }
        valid_count = 0
        missing_count = 0
        advance_count = 0
        decline_count = 0
        flat_count = 0

        if growth_col is None:
            print(json.dumps({"error": "no estimate growth column"}))
        else:
            for raw in frame[growth_col]:
                if raw is None or str(raw).strip().lower() in ("", "nan", "--"):
                    missing_count += 1
                    continue
                try:
                    value = float(raw)
                except (TypeError, ValueError):
                    missing_count += 1
                    continue
                valid_count += 1
                if value < 0:
                    decline_count += 1
                elif value > 0:
                    advance_count += 1
                else:
                    flat_count += 1
                if value <= -5:
                    bins["le_neg5"] += 1
                elif value <= -3:
                    bins["neg5_neg3"] += 1
                elif value <= -1:
                    bins["neg3_neg1"] += 1
                elif value < 0:
                    bins["neg1_zero"] += 1
                elif value == 0:
                    bins["zero"] += 1
                elif value < 1:
                    bins["zero_one"] += 1
                elif value < 3:
                    bins["one_three"] += 1
                elif value < 5:
                    bins["three_five"] += 1
                else:
                    bins["ge_five"] += 1
            source_row_count = int(len(frame))
            coverage_percent = (
                round(valid_count / source_row_count * 100, 2) if source_row_count else 0.0
            )
            print(json.dumps({
                "as_of_date": as_of_date,
                "source_row_count": source_row_count,
                "valid_count": valid_count,
                "missing_count": missing_count,
                "coverage_percent": coverage_percent,
                "advance_count": advance_count,
                "decline_count": decline_count,
                "flat_count": flat_count,
                "bins": bins,
            }, ensure_ascii=True))
except Exception as exc:
    print(json.dumps({"error": str(exc)}, ensure_ascii=True))
'''
    payload = run_akshare_json_script(
        script,
        label="fund_return_distribution_intraday_estimate",
        timeout=timeout,
    )
    if not isinstance(payload, dict) or payload.get("error"):
        return None
    return _normalize_distribution_counts(payload)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd apps/api && python -m pytest tests/test_market_breadth_distribution.py::test_intraday_estimate_fetcher_bins_estimated_growth tests/test_market_breadth_distribution.py::test_intraday_estimate_fetcher_rejects_non_conserving_payload -v`
Expected: PASS（两个都过）

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/fund_return_distribution.py apps/api/tests/test_market_breadth_distribution.py
git commit -m "feat(api): 新增盘中实时估值涨跌分布拉取器"
```

---

## Task 3: 按 session 路由 + 盘中三级回退（TDD）

**Files:**
- Modify: `apps/api/app/services/fund_return_distribution.py`
- Test: `apps/api/tests/test_market_breadth_distribution.py`

- [ ] **Step 1: 写失败测试（盘中路由 + intraday 缓存 key + stale 回退）**

在 `test_market_breadth_distribution.py` 末尾追加：

```python
def test_intraday_session_routes_to_estimate_distribution(monkeypatch):
    monkeypatch.setattr(
        fund_return_distribution, "build_trading_session",
        lambda: {"is_continuous_trading": True},
    )
    monkeypatch.setattr(fund_return_distribution, "get_spot_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(fund_return_distribution, "get_spot_snapshot_any_age", lambda *a, **k: None)
    saved: dict = {}
    monkeypatch.setattr(
        fund_return_distribution, "save_spot_snapshot",
        lambda key, payload: saved.update({"key": key, "payload": payload}),
    )
    monkeypatch.setattr(
        fund_return_distribution, "run_akshare_json_script",
        lambda *a, **k: {
            "as_of_date": "2026-07-26", "source_row_count": 12, "valid_count": 9,
            "missing_count": 3, "coverage_percent": 75.0,
            "advance_count": 4, "decline_count": 4, "flat_count": 1,
            "bins": {"le_neg5": 1, "neg5_neg3": 0, "neg3_neg1": 1, "neg1_zero": 2,
                     "zero": 1, "zero_one": 2, "one_three": 1, "three_five": 0, "ge_five": 1},
        },
    )
    result = fund_return_distribution.build_fund_return_distribution(force_refresh=True)
    assert result["source_mode"] == "intraday_estimate"
    assert result["available"] is True
    assert saved["key"] == "fund:return-distribution:intraday:v1"
    assert "实时估值" in result["source_name"]


def test_intraday_fetch_failure_falls_back_to_stale_snapshot(monkeypatch):
    monkeypatch.setattr(
        fund_return_distribution, "build_trading_session",
        lambda: {"is_continuous_trading": True},
    )
    monkeypatch.setattr(fund_return_distribution, "get_spot_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(
        fund_return_distribution, "get_spot_snapshot_any_age",
        lambda *a, **k: {"available": True, "source_mode": "intraday_estimate",
                          "valid_count": 9, "bins": {"zero": 9},
                          "advance_count": 0, "decline_count": 0, "flat_count": 9,
                          "as_of_date": "2026-07-25"},
    )
    monkeypatch.setattr(
        fund_return_distribution, "run_akshare_json_script",
        lambda *a, **k: {"error": "boom"},
    )
    result = fund_return_distribution.build_fund_return_distribution(force_refresh=True)
    assert result["stale"] is True
    assert result["available"] is True
    assert "上次成功统计" in result["message"]


def test_intraday_fetch_failure_without_stale_returns_unavailable(monkeypatch):
    monkeypatch.setattr(
        fund_return_distribution, "build_trading_session",
        lambda: {"is_continuous_trading": True},
    )
    monkeypatch.setattr(fund_return_distribution, "get_spot_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(fund_return_distribution, "get_spot_snapshot_any_age", lambda *a, **k: None)
    monkeypatch.setattr(
        fund_return_distribution, "run_akshare_json_script",
        lambda *a, **k: {"error": "boom"},
    )
    result = fund_return_distribution.build_fund_return_distribution(force_refresh=True)
    assert result["available"] is False
    assert result["source_mode"] == "intraday_estimate"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd apps/api && python -m pytest tests/test_market_breadth_distribution.py::test_intraday_session_routes_to_estimate_distribution -v`
Expected: FAIL（`result["source_mode"]` 仍是 `"official_nav"`，因为还没路由）

- [ ] **Step 3: 既有 official 测试钉住 session（避免受墙钟时间影响）**

在 `test_official_fund_distribution_requires_conservation_and_records_scope` 与 `test_official_fund_distribution_rejects_non_conserving_payload` 两个函数体首行各加一行：

```python
    monkeypatch.setattr(
        fund_return_distribution, "build_trading_session",
        lambda: {"is_continuous_trading": False},
    )
```

- [ ] **Step 4: 抽取共享构建辅助 `_build_distribution`，改写 `build_fund_return_distribution` 路由**

在 `fund_return_distribution.py` 顶部 import 段新增：

```python
from app.services.trading_session import build_trading_session
```

在 `build_fund_return_distribution` 之前新增共享辅助（两条分支共用三级回退）：

```python
_INTRADAY_CACHE_KEY = "fund:return-distribution:intraday:v1"
_INTRADAY_CACHE_TTL_SECONDS = 10 * 60.0
_INTRADAY_SOURCE_NAME = "东方财富实时估值"
_INTRADAY_UNIVERSE_SCOPE = "开放式基金份额代码（A/C/E 等分别计数，盘中估算口径）"
_INTRADAY_STALE_MESSAGE = "实时估值源本次更新失败，正在展示上次成功统计。"
_INTRADAY_UNAVAILABLE_MESSAGE = "暂未取得可核验的盘中实时估值分布。"

_OFFICIAL_STALE_MESSAGE = "官方净值源本次更新失败，正在展示上次成功统计。"
_OFFICIAL_UNAVAILABLE_MESSAGE = "暂未取得可核验的开放式基金官方净值分布。"


def _build_distribution(
    *,
    cache_key: str,
    cache_ttl_seconds: float,
    fetch_fn,
    source_mode: str,
    source_name: str,
    universe_scope: str,
    stale_message: str,
    unavailable_message: str,
    force_refresh: bool,
) -> dict:
    if not force_refresh:
        cached = get_spot_snapshot(cache_key, ttl_seconds=cache_ttl_seconds)
        if cached is not None:
            return dict(cached)

    result = fetch_fn(timeout=_FETCH_TIMEOUT_SECONDS)
    if result is not None:
        payload = {
            "available": True,
            "stale": False,
            "source_mode": source_mode,
            "source_name": source_name,
            "universe_scope": universe_scope,
            "fetched_at": datetime.now(_CN_TZ).isoformat(),
            "as_of_datetime": result.get("as_of_date"),
            **result,
        }
        save_spot_snapshot(cache_key, payload)
        return payload

    stale = get_spot_snapshot_any_age(cache_key)
    if stale is not None:
        payload = dict(stale)
        payload.update({"stale": True, "message": stale_message})
        return payload

    return {
        "available": False,
        "stale": True,
        "source_mode": source_mode,
        "message": unavailable_message,
    }
```

把 `build_fund_return_distribution` 整体改写为：

```python
def build_fund_return_distribution(*, force_refresh: bool = False) -> dict:
    """返回当前时段口径下的全量开放式基金涨跌分布。

    交易日连续交易时段（盘中、收盘前，排除午休）走东方财富实时估值按估算
    增长率分桶；其余时段（非交易日、盘前、午休、收盘后）走官方已结算净值。
    """
    session = build_trading_session()
    if session.get("is_continuous_trading"):
        return _build_distribution(
            cache_key=_INTRADAY_CACHE_KEY,
            cache_ttl_seconds=_INTRADAY_CACHE_TTL_SECONDS,
            fetch_fn=_fetch_intraday_estimate_distribution,
            source_mode="intraday_estimate",
            source_name=_INTRADAY_SOURCE_NAME,
            universe_scope=_INTRADAY_UNIVERSE_SCOPE,
            stale_message=_INTRADAY_STALE_MESSAGE,
            unavailable_message=_INTRADAY_UNAVAILABLE_MESSAGE,
            force_refresh=force_refresh,
        )
    return _build_distribution(
        cache_key=_CACHE_KEY,
        cache_ttl_seconds=_CACHE_TTL_SECONDS,
        fetch_fn=_fetch_official_distribution,
        source_mode="official_nav",
        source_name="东方财富开放式基金净值",
        universe_scope="开放式基金份额代码（A/C/E 等分别计数）",
        stale_message=_OFFICIAL_STALE_MESSAGE,
        unavailable_message=_OFFICIAL_UNAVAILABLE_MESSAGE,
        force_refresh=force_refresh,
    )
```

注意：原 `build_fund_return_distribution` 的内联 official 逻辑被 `_build_distribution` 取代；`_fetch_official_distribution` 保留不变。`as_of_datetime` 对 official 分支也写入（取 `as_of_date`），前端可选用。

- [ ] **Step 5: 运行全部 market_breadth_distribution 测试确认通过**

Run: `cd apps/api && python -m pytest tests/test_market_breadth_distribution.py -v`
Expected: PASS（既有 official + 新增 intraday 全绿）

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/services/fund_return_distribution.py apps/api/tests/test_market_breadth_distribution.py
git commit -m "feat(api): 基金涨跌分布按交易时段切换盘中估值/官方净值口径"
```

---

## Task 4: 前端类型扩展

**Files:**
- Modify: `apps/web/src/lib/api/marketDiagnostics.ts`
- Modify: `apps/web/src/lib/api.ts`

- [ ] **Step 1: 扩展 `FundReturnDistribution` 类型**

在 `apps/web/src/lib/api/marketDiagnostics.ts` 的 `FundReturnDistribution` 类型（约 107–124 行）改 `source_mode` 并加 `as_of_datetime`：

```ts
export type FundReturnDistribution = {
  available: boolean;
  stale?: boolean;
  message?: string | null;
  source_mode?: "official_nav" | "intraday_estimate";
  source_name?: string | null;
  universe_scope?: string | null;
  as_of_date?: string | null;
  as_of_datetime?: string | null;
  fetched_at?: string | null;
  source_row_count?: number | null;
  valid_count?: number | null;
  missing_count?: number | null;
  coverage_percent?: number | null;
  advance_count?: number | null;
  decline_count?: number | null;
  flat_count?: number | null;
  bins?: Partial<Record<FundReturnDistributionBinKey, number>>;
};
```

- [ ] **Step 2: 给 `TradingSession` 补 `is_continuous_trading`**

在 `apps/web/src/lib/api.ts` 的 `TradingSession` 类型（约 454–469 行）加字段：

```ts
  is_continuous_trading: boolean;
  market_phase?: string;
```

加在 `is_trading_day: boolean;` 之后。

- [ ] **Step 3: typecheck**

Run: `cd apps/web && npx tsc --noEmit`
Expected: 无新增错误（既有 `next.config.ts` 的 `@next/bundle-analyzer` 报错可忽略，与本任务无关）。

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/lib/api/marketDiagnostics.ts apps/web/src/lib/api.ts
git commit -m "feat(web): FundReturnDistribution 与 TradingSession 类型扩展"
```

---

## Task 5: localStorage 冷启动缓存 helper（TDD）

**Files:**
- Modify: `apps/web/src/lib/storage.ts`
- Test: 新增 `apps/web/src/lib/storage.test.ts`（如已存在则追加 describe）

- [ ] **Step 1: 确认 storage 测试文件是否存在**

Run: `ls apps/web/src/lib/storage.test.ts 2>/dev/null && echo EXISTS || echo MISSING`

若 MISSING，新建文件头：

```ts
import { beforeEach, describe, expect, it } from "vitest";
import {
  loadFundReturnDistributionCache,
  saveFundReturnDistributionCache,
} from "@/lib/storage";

beforeEach(() => {
  window.localStorage.clear();
});
```

若 EXISTS，在文件末尾的顶层追加下面的 describe。

- [ ] **Step 2: 写失败测试**

追加：

```ts
describe("FundReturnDistribution cache", () => {
  it("round-trips a distribution payload through localStorage", () => {
    const payload = { available: true, source_mode: "intraday_estimate", valid_count: 9 };
    saveFundReturnDistributionCache(payload);
    expect(loadFundReturnDistributionCache()).toEqual(payload);
  });

  it("returns null for missing or malformed entries", () => {
    expect(loadFundReturnDistributionCache()).toBeNull();
    window.localStorage.setItem("fundpilot-fund-return-distribution", "{not json");
    expect(loadFundReturnDistributionCache()).toBeNull();
  });

  it("returns null when older than the max age", () => {
    saveFundReturnDistributionCache({ available: true, valid_count: 1 });
    const stale = JSON.stringify({
      fetchedAt: Date.now() - 31 * 60 * 1000,
      data: { available: true },
    });
    window.localStorage.setItem("fundpilot-fund-return-distribution", stale);
    expect(loadFundReturnDistributionCache(30 * 60 * 1000)).toBeNull();
  });
});
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd apps/web && npx vitest run src/lib/storage.test.ts 2>&1 | tail -20`
Expected: FAIL（`loadFundReturnDistributionCache is not a function`）

- [ ] **Step 4: 实现 helper**

在 `apps/web/src/lib/storage.ts` 末尾新增（参考 `loadDiscoverySectorHeatCache` 写法）：

```ts
const FUND_RETURN_DISTRIBUTION_KEY = "fundpilot-fund-return-distribution";

type FundReturnDistributionCache = {
  fetchedAt: number;
  data: FundReturnDistribution;
};

/** 基金涨跌分布：localStorage 冷启动缓存，进入市场页时先展示再后台续期。 */
export function loadFundReturnDistributionCache(
  maxAgeMs = 30 * 60 * 1000,
): FundReturnDistribution | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    const raw = window.localStorage.getItem(FUND_RETURN_DISTRIBUTION_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as FundReturnDistributionCache;
    if (!parsed || typeof parsed !== "object" || parsed.data == null) {
      return null;
    }
    if (Date.now() - parsed.fetchedAt > maxAgeMs) {
      return null;
    }
    return parsed.data;
  } catch {
    return null;
  }
}

export function saveFundReturnDistributionCache(data: FundReturnDistribution) {
  if (typeof window === "undefined" || data == null) {
    return;
  }
  const payload: FundReturnDistributionCache = { fetchedAt: Date.now(), data };
  window.localStorage.setItem(FUND_RETURN_DISTRIBUTION_KEY, JSON.stringify(payload));
}
```

并在文件顶部 import 段补 `FundReturnDistribution` 类型（若未引入）：

```ts
import type { FundReturnDistribution } from "@/lib/api";
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd apps/web && npx vitest run src/lib/storage.test.ts 2>&1 | tail -20`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/lib/storage.ts apps/web/src/lib/storage.test.ts
git commit -m "feat(web): 基金涨跌分布 localStorage 冷启动缓存"
```

---

## Task 6: `FundReturnDistributionPanel` 盘中实时 + 15 分钟定时 + 闸门（TDD）

**Files:**
- Modify: `apps/web/src/components/FundReturnDistributionPanel.tsx`
- Test: `apps/web/src/components/FundReturnDistributionPanel.test.tsx`

- [ ] **Step 1: 扩展测试 mock，加 `fetchTradingSession`**

在 `FundReturnDistributionPanel.test.tsx` 顶部 `apiMocks` 加一个：

```ts
const apiMocks = vi.hoisted(() => ({
  fetchFundReturnDistribution: vi.fn(),
  fetchTradingSession: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    fetchFundReturnDistribution: apiMocks.fetchFundReturnDistribution,
    fetchTradingSession: apiMocks.fetchTradingSession,
  };
});
```

`afterEach` 里 `vi.clearAllMocks()` 之外，也清 sessionStorage：

```ts
afterEach(() => {
  cleanup();
  deleteClientCache("diagnostics:fund-return-distribution", "memory");
  deleteClientCache("diagnostics:fund-return-distribution", "session");
  window.sessionStorage.clear();
  window.localStorage.clear();
  vi.useRealTimers();
  vi.clearAllMocks();
});
```

- [ ] **Step 2: 写失败测试——盘中 tick 触发刷新、非交易日 tick 不刷新**

追加 describe（用 `vi.useFakeTimers`）：

```ts
describe("FundReturnDistributionPanel scheduling", () => {
  it("refreshes on a 15-minute tick during continuous trading", async () => {
    vi.useFakeTimers();
    apiMocks.fetchTradingSession.mockResolvedValue({ is_continuous_trading: true });
    apiMocks.fetchFundReturnDistribution.mockResolvedValue({
      available: true,
      source_mode: "intraday_estimate",
      as_of_datetime: "2026-07-26",
      valid_count: 9,
      advance_count: 4, decline_count: 4, flat_count: 1,
      bins: { zero: 9 },
    });

    render(<FundReturnDistributionPanel />);
    // 初始挂载拉一次
    expect(apiMocks.fetchFundReturnDistribution).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(15 * 60 * 1000);

    expect(apiMocks.fetchTradingSession).toHaveBeenCalled();
    expect(apiMocks.fetchFundReturnDistribution).toHaveBeenCalledTimes(2);
  });

  it("skips the 15-minute tick on a non-trading day (空跑保护)", async () => {
    vi.useFakeTimers();
    apiMocks.fetchTradingSession.mockResolvedValue({ is_continuous_trading: false });
    apiMocks.fetchFundReturnDistribution.mockResolvedValue({
      available: true,
      source_mode: "official_nav",
      as_of_date: "2026-07-25",
      valid_count: 9,
      advance_count: 4, decline_count: 4, flat_count: 1,
      bins: { zero: 9 },
    });

    render(<FundReturnDistributionPanel />);
    await vi.advanceTimersByTimeAsync(15 * 60 * 1000);

    expect(apiMocks.fetchTradingSession).toHaveBeenCalled();
    // 初始挂载那一次之后，非连续交易时段不再发分布请求
    expect(apiMocks.fetchFundReturnDistribution).toHaveBeenCalledTimes(1);
  });

  it("labels the intraday source subtitle distinctly from official NAV", async () => {
    apiMocks.fetchTradingSession.mockResolvedValue({ is_continuous_trading: true });
    apiMocks.fetchFundReturnDistribution.mockResolvedValue({
      available: true,
      source_mode: "intraday_estimate",
      as_of_datetime: "2026-07-26",
      valid_count: 9,
      advance_count: 4, decline_count: 4, flat_count: 1,
      bins: { zero: 9 },
    });

    render(<FundReturnDistributionPanel />);

    expect(await screen.findByText("基金涨跌分布")).toBeTruthy();
    expect(screen.getByText(/实时估值 · 截至 2026-07-26/)).toBeTruthy();
  });
});
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd apps/web && npx vitest run src/components/FundReturnDistributionPanel.test.tsx 2>&1 | tail -30`
Expected: FAIL（subtitle 仍是 "官方净值"；定时器/闸门未实现 → `fetchTradingSession` not called 或调用次数不对）

- [ ] **Step 4: 改写 `FundReturnDistributionPanel`**

把 `apps/web/src/components/FundReturnDistributionPanel.tsx` 的 `FundReturnDistributionPanel` 函数与顶部 import 段改为：

顶部 import 段加：

```ts
import { useEffect } from "react";
import { fetchTradingSession } from "@/lib/api";
import {
  loadFundReturnDistributionCache,
  saveFundReturnDistributionCache,
} from "@/lib/storage";
```

把 `CACHE_KEY`/`STALE_MS` 常量段改为：

```ts
const CACHE_KEY = "diagnostics:fund-return-distribution";
const STALE_MS = 15 * 60_000;
const REFRESH_INTERVAL_MS = 15 * 60_000;
```

改写组件主体：

```ts
export function FundReturnDistributionPanel() {
  const { data, error, loading, revalidating, refresh } = useCachedFetch<FundReturnDistribution>({
    cacheKey: CACHE_KEY,
    fetcher: fetchFundReturnDistribution,
    staleTimeMs: STALE_MS,
    storage: "session",
    bootstrap: () => loadFundReturnDistributionCache(),
  });

  // 拉到的最新数据回写 localStorage，供下次冷启动秒开。
  useEffect(() => {
    if (data?.available) {
      saveFundReturnDistributionCache(data);
    }
  }, [data]);

  // 15 分钟定时 + visibility：只在交易日连续交易时段真正发请求，
  // 其余时段跳过（空跑保护）。完全照抄 MarketBreadthGauge 的 effect 骨架。
  useEffect(() => {
    let timer: number | null = null;
    const stop = () => {
      if (timer != null) {
        window.clearInterval(timer);
        timer = null;
      }
    };
    const tick = async () => {
      try {
        const session = await fetchTradingSession();
        if (session?.is_continuous_trading) {
          await refresh();
        }
        // 非连续交易时段：空跑保护，跳过本次分布请求，保留缓存展示。
      } catch {
        // trading-session 拉取失败：保守发一次，宁可多请求也不空跑掉实时性。
        await refresh();
      }
    };
    const start = () => {
      if (timer == null) {
        timer = window.setInterval(() => {
          void tick();
        }, REFRESH_INTERVAL_MS);
      }
    };
    const handleVisibility = () => {
      if (document.hidden) {
        stop();
        return;
      }
      void tick();
      start();
    };
    if (!document.hidden) {
      start();
    }
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [refresh]);

  const isIntraday = data?.source_mode === "intraday_estimate";
  const asOfLabel = isIntraday
    ? data?.as_of_datetime
      ? `实时估值 · 截至 ${data.as_of_datetime}`
      : "实时估值"
    : data?.as_of_date
      ? `官方净值 · 截至 ${data.as_of_date}`
      : "正在确认净值日期";

  return (
    <section className="mt-4 min-w-0 max-w-full overflow-hidden rounded-2xl border border-slate-200/90 bg-[#fbfaf7] px-4 py-4 sm:px-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-slate-950 text-[#f4ead2]">
            <BarChart3 size={18} aria-hidden />
          </div>
          <div>
            <h4 className="text-base font-black text-slate-950">基金涨跌分布</h4>
            <p className="mt-1 text-xs leading-5 text-slate-500">
              {asOfLabel}
              {data?.stale ? " · 上次成功统计" : ""}
            </p>
          </div>
        </div>
        {revalidating ? (
          <span className="inline-flex items-center gap-1 text-xs font-semibold text-slate-500" role="status">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />更新中
          </span>
        ) : null}
      </div>

      {loading && !data ? (
        <div className="mt-5 flex h-44 items-center justify-center rounded-xl bg-white/60 text-sm text-slate-500">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />
          正在聚合全量官方净值…
        </div>
      ) : data?.available ? (
        <DistributionContent data={data} />
      ) : (
        <p className="mt-4 rounded-xl bg-white/70 px-3 py-3 text-sm leading-6 text-slate-600" role="status">
          {data?.message ?? "基金官方净值分布暂不可用。"}
        </p>
      )}

      {error ? (
        <p className="mt-3 text-xs font-semibold text-amber-700" role="status">
          本次更新失败；如有历史结果仍会保留展示。
        </p>
      ) : null}
    </section>
  );
}
```

注意：`DistributionContent`、`BINS`、`formatCount`、`ratio`、`BAR_TONE` 等保持不变。loading 文案保留"正在聚合全量官方净值…"（既有，不动），副标题口径由 `asOfLabel` 区分。

- [ ] **Step 5: 运行测试确认通过**

Run: `cd apps/web && npx vitest run src/components/FundReturnDistributionPanel.test.tsx 2>&1 | tail -30`
Expected: PASS（既有"shows the official NAV date..." + 新增 scheduling 三个全绿）

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/components/FundReturnDistributionPanel.tsx apps/web/src/components/FundReturnDistributionPanel.test.tsx
git commit -m "feat(web): 基金涨跌分布盘中实时口径 + 15 分钟定时与空跑保护"
```

---

## Task 7: 自验证

- [ ] **Step 1: 后端全量测试**

Run: `cd apps/api && python -m pytest tests/test_market_breadth_distribution.py tests/test_market_diagnostics_router.py -v`
Expected: PASS

- [ ] **Step 2: 前端 typecheck**

Run: `cd apps/web && npx tsc --noEmit`
Expected: 无新增错误（`next.config.ts` 的 `@next/bundle-analyzer` 报错为预存，与本任务无关）。

- [ ] **Step 3: 前端相关组件测试**

Run: `cd apps/web && npx vitest run src/components/FundReturnDistributionPanel.test.tsx src/components/MarketBreadthGauge.test.tsx src/lib/storage.test.ts 2>&1 | tail -30`
Expected: PASS

- [ ] **Step 4: 若全部绿，最终提交（如有未提交项）**

```bash
git status
```

如有残留改动：

```bash
git add -A && git commit -m "chore: 基金涨跌分布盘中实时缓存收尾"
```

---

## Self-Review

**Spec coverage:**
- §2 时段规则 → Task 3（`build_trading_session` 路由 + `is_continuous_trading`）✓
- §3.1 `_fetch_intraday_estimate_distribution` + 子进程分桶 + 同构输出 → Task 2 ✓
- §3.1 数值守卫（`_as_non_negative_int`、合计校验）→ Task 1 抽取 `_normalize_distribution_counts`，Task 2 复用 ✓
- §3.2 服务端缓存 intraday key/10min TTL + 三级回退 → Task 3 `_build_distribution` ✓
- §3.3 端点签名不变 → 路由文件不动（已说明）✓
- §4.1 类型扩展 → Task 4 ✓
- §4.2 localStorage helper + `storage="session"` + bootstrap + 15min staleTimeMs → Task 5 + Task 6 ✓
- §4.3 定时 + visibility + 闸门 + 副标题口径 → Task 6 ✓
- §5 错误处理（三级回退、session 失败保守刷新）→ Task 3 + Task 6 ✓
- §6 测试（后端 mock akshare、session 切换、TTL、stale 回退；前端 tick/空跑/bootstrap/口径）→ Task 2/3/5/6 ✓

**Placeholder scan:** 无 TBD/TODO；每步含可运行代码与命令。

**Type consistency:** `_fetch_intraday_estimate_distribution` / `_build_distribution` / `build_fund_return_distribution` 签名在各 Task 一致；`source_mode` 取值 `"official_nav" | "intraday_estimate"` 前后端一致；`as_of_datetime` 后端写入、前端读取，字段名一致。

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-26-fund-return-distribution-intraday-cache.md`.

**Two execution options:**

**1. Subagent-Driven (recommended)** - 每个 Task 派一个 fresh subagent，task 间 review，迭代快。

**2. Inline Execution** - 在本 session 用 executing-plans 批量执行，带 checkpoint。

**Which approach?**
