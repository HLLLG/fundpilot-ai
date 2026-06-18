# 主题板块扩展（~100 板块 + 后台 15min 刷新）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把市场 Tab 主题板块从 21 个扩展到约 100 个（东财行业全量 + 21 canonical 概念主题），涨跌幅与连涨天数统一从 push2delay 日 K 计算，并由后台 daemon 线程每 15min（盘中）/1h（收盘）刷新、前台只读缓存。

**Architecture:** 新增 `list_theme_board_universe()` 合并行业 spot 全量与 canonical 概念（带 `board_kind`、secid 去重）；`refresh_theme_board_snapshot()` 后台并行拉日 K 同时算 change+streak，写 `theme:boards:v3` 缓存；`get_theme_board_snapshot()` 改为只读缓存 + 持仓叠加；lifespan 启动时段感知 daemon 刷新线程。前端加 `board_kind` 标签、staleTime 提到 15min。

**Tech Stack:** FastAPI, Pydantic Settings, pytest, Next.js, TypeScript, Tailwind, `useCachedFetch`

**Spec:** `docs/superpowers/specs/2026-06-18-market-theme-boards-expansion-design.md`

---

## File Structure

| 文件 | 责任 |
|------|------|
| `apps/api/app/config.py` | 3 个新 Settings 字段 |
| `apps/api/app/services/theme_board_snapshot.py` | universe 构建、后台刷新、缓存读写、payload(board_kind)、刷新循环 |
| `apps/api/app/lifespan.py` | 启动后台刷新线程 |
| `apps/api/app/main.py` | 路由透传（基本不变） |
| `apps/api/tests/test_theme_board_snapshot.py` | universe/刷新/board_kind/持仓匹配单测 |
| `apps/api/tests/test_api.py` | API smoke 更新 |
| `apps/api/tests/conftest.py` | 关闭后台线程 env |
| `apps/web/src/lib/api.ts` | `board_kind` 类型、`refreshed_at` |
| `apps/web/src/lib/marketThemeBoard.ts` | board_kind helper、updatedAt 用后端时间 |
| `apps/web/src/components/ThemeSectorOverview.tsx` | board_kind 标签 |
| `apps/web/src/components/MarketTab.tsx` | theme staleTimeMs 15min |
| `docs/PROJECT_CONTEXT.md`、`.env.example` | 文档同步 |

---

## Task 1: 配置项

**Files:** Modify `apps/api/app/config.py`

- [ ] **Step 1:** 在 `Settings` 类 `us_market_qdii_enabled` 行后新增字段：

```python
    theme_board_refresh_enabled: bool = True
    theme_board_refresh_interval_seconds: int = 900
    theme_board_refresh_idle_interval_seconds: int = 3600
```

- [ ] **Step 2:** Commit `git add -A; git commit -m "feat(config): 主题板块后台刷新开关与间隔"`

---

## Task 2: `list_theme_board_universe()` + board_kind

**Files:** Modify `apps/api/app/services/theme_board_snapshot.py`, `apps/api/tests/test_theme_board_snapshot.py`

- [ ] **Step 1: 写失败测试**（追加到测试文件）

```python
def test_list_theme_board_universe_merges_industry_and_canonical(monkeypatch):
    from app.services import theme_board_snapshot as mod

    monkeypatch.setattr(
        mod,
        "fetch_eastmoney_board_records",
        lambda board_type: [
            {"name": "电子", "code": "BK1037x", "change_percent": 1.0},
            {"name": "有色金属", "code": "BK0478", "change_percent": 2.0},  # 与 canonical 同码
        ]
        if board_type == "industry"
        else [],
    )
    universe = mod.list_theme_board_universe()
    labels = {item["sector_label"] for item in universe}
    assert "电子" in labels            # 行业
    assert "半导体" in labels          # canonical 概念
    # 同码去重：有色金属只出现一次，且 board_kind 取 canonical 行业口径
    youse = [i for i in universe if i["sector_label"] == "有色金属"]
    assert len(youse) == 1
    kinds = {item["board_kind"] for item in universe}
    assert kinds <= {"industry", "concept", "index"}
```

- [ ] **Step 2: 运行确认失败** `cd apps/api; ./.venv/Scripts/python.exe -m pytest tests/test_theme_board_snapshot.py::test_list_theme_board_universe_merges_industry_and_canonical -v` → FAIL (AttributeError list_theme_board_universe)

- [ ] **Step 3: 实现**。在 `theme_board_snapshot.py` 顶部 import 增加 `fetch_eastmoney_board_records`（来自 `app.services.eastmoney_spot_client`），新增：

```python
from app.services.eastmoney_spot_client import fetch_eastmoney_board_records

def _board_kind_from_source_type(source_type: str) -> str:
    return source_type if source_type in {"industry", "concept", "index"} else "concept"


def list_theme_board_universe() -> list[dict[str, Any]]:
    """行业 spot 全量 + 21 canonical 概念/指数，按 secid 去重；canonical 优先。"""
    by_secid: dict[str, dict[str, Any]] = {}

    # 1) canonical 概念/指数（优先，精确 secid）
    for label in list_discovery_sector_labels():
        canon = get_quote_canonical_sector(label) or get_canonical_sector(label)
        if canon is None:
            continue
        by_secid[canon.eastmoney_secid] = {
            "sector_label": label,
            "secid": canon.eastmoney_secid,
            "source_code": canon.source_code,
            "board_kind": _board_kind_from_source_type(canon.source_type),
            "_canon": canon,
        }

    # 2) 行业 spot 全量（同 secid 不覆盖 canonical）
    try:
        rows = fetch_eastmoney_board_records("industry")
    except Exception as exc:
        logger.info("theme universe industry spot failed: %s", exc)
        rows = []
    for row in rows:
        name = str(row.get("name", "")).strip()
        code = str(row.get("code", "")).strip()
        if not name or not code:
            continue
        secid = f"90.{code}"
        if secid in by_secid:
            continue
        canon = get_quote_canonical_sector(name) or get_canonical_sector(name)
        resolved_secid = canon.eastmoney_secid if canon else secid
        if resolved_secid in by_secid:
            continue
        by_secid[resolved_secid] = {
            "sector_label": name,
            "secid": resolved_secid,
            "source_code": (canon.source_code if canon else code),
            "board_kind": (_board_kind_from_source_type(canon.source_type) if canon else "industry"),
            "_canon": canon,
        }

    return list(by_secid.values())
```

- [ ] **Step 4: 运行通过** 同上命令 → PASS

- [ ] **Step 5: Commit** `git add -A; git commit -m "feat(theme-boards): 行业全量+canonical合并 universe(board_kind)"`

---

## Task 3: `refresh_theme_board_snapshot()` 同源 change+streak + 缓存 v3

**Files:** Modify `apps/api/app/services/theme_board_snapshot.py`, `apps/api/tests/test_theme_board_snapshot.py`

- [ ] **Step 1: 写失败测试**

```python
def test_refresh_theme_board_snapshot_computes_change_and_streak(monkeypatch):
    from app.services import theme_board_snapshot as mod

    monkeypatch.setattr(
        mod,
        "list_theme_board_universe",
        lambda: [
            {"sector_label": "半导体", "secid": "90.BK1036", "source_code": "BK1036",
             "board_kind": "concept", "_canon": None},
        ],
    )

    def fake_series(secid, source_code=None, **kwargs):
        return [
            {"date": "2026-06-16", "change_percent": 1.0},
            {"date": "2026-06-17", "change_percent": 0.5},
            {"date": "2026-06-18", "change_percent": 2.0},
        ]

    monkeypatch.setattr(mod, "_fetch_universe_series", fake_series)
    monkeypatch.setattr(mod, "save_spot_snapshot", lambda *a, **k: None)

    snapshot = mod.refresh_theme_board_snapshot(trade_date="2026-06-18")
    item = snapshot["items"][0]
    assert item["change_1d_percent"] == 2.0
    assert item["consecutive_up_days"] == 3
    assert item["board_kind"] == "concept"
    assert "linked_fund_count" not in item
```

- [ ] **Step 2: 运行确认失败** → FAIL

- [ ] **Step 3: 实现**。新增 `_fetch_universe_series` 与 `refresh_theme_board_snapshot`，并加缓存版本常量 `_CACHE_VERSION = "v3"`（替换原 v2）：

```python
def _fetch_universe_series(secid: str, source_code: str | None = None, *, timeout: float = 8.0) -> list[dict]:
    from app.services.sector_canonical import CanonicalSector
    source_type = "concept"
    if secid.startswith("2."):
        source_type = "index"
    elif secid.startswith("90.") and source_code and not source_code.startswith("BK"):
        source_type = "index"
    canon = CanonicalSector(
        label=secid, source_type=source_type, source_name=secid,
        eastmoney_secid=secid, source_code=source_code,
    )
    return fetch_canonical_daily_kline_series(canon, max_days=20, timeout=timeout)


def refresh_theme_board_snapshot(*, trade_date: str | None = None) -> dict[str, Any]:
    session = build_trading_session()
    resolved_date = trade_date or session.get("effective_trade_date")
    session_kind = session.get("session_kind", "")
    universe = list_theme_board_universe()

    items: list[dict[str, Any]] = []
    spot_changes: dict[str, float] | None = None

    def enrich(entry: dict[str, Any]) -> dict[str, Any]:
        secid = entry["secid"]
        series = _fetch_universe_series(secid, entry.get("source_code"))
        change = _latest_change_percent(series, resolved_date) if series else None
        streak = compute_consecutive_up_days(series, resolved_date) if series else None
        return {
            "sector_label": entry["sector_label"],
            "board_kind": entry["board_kind"],
            "secid": secid,
            "change_1d_percent": change,
            "consecutive_up_days": streak,
        }

    deadline = time.monotonic() + 90.0
    executor = ThreadPoolExecutor(max_workers=8)
    futures = {executor.submit(enrich, e): e for e in universe}
    pending = set(futures)
    try:
        while pending and time.monotonic() < deadline:
            done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
            for fut in done:
                try:
                    items.append(fut.result())
                except Exception as exc:
                    logger.debug("theme universe enrich failed: %s", exc)
        # 超预算未完成的，用基础行补齐（change/streak=None）
        for fut, entry in futures.items():
            if fut in pending:
                items.append({
                    "sector_label": entry["sector_label"],
                    "board_kind": entry["board_kind"],
                    "secid": entry["secid"],
                    "change_1d_percent": None,
                    "consecutive_up_days": None,
                })
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    # 日 K 缺失的板块用行业现货榜按代码兜底涨跌幅
    missing = [it for it in items if it["change_1d_percent"] is None]
    if missing:
        spot_changes = _load_theme_spot_changes()
        for it in missing:
            change = spot_changes.get(it["sector_label"])
            if change is not None:
                it["change_1d_percent"] = round(float(change), 2)

    refreshed_at = datetime.now(timezone.utc).isoformat()
    snapshot = {
        "items": items,
        "trade_date": resolved_date,
        "session_kind": session_kind,
        "refreshed_at": refreshed_at,
    }
    cache_key = f"theme:boards:{_CACHE_VERSION}:{resolved_date}"
    save_spot_snapshot(cache_key, snapshot)
    return snapshot
```

补充 import：`from datetime import datetime, timezone`。

- [ ] **Step 4: 运行通过** → PASS

- [ ] **Step 5: Commit** `git add -A; git commit -m "feat(theme-boards): 后台刷新同源算change+streak+缓存v3"`

---

## Task 4: `get_theme_board_snapshot` 只读缓存 + payload(board_kind) + 移除 linked_fund_count

**Files:** Modify `apps/api/app/services/theme_board_snapshot.py`, `apps/api/tests/test_theme_board_snapshot.py`

- [ ] **Step 1: 写失败测试**

```python
def test_get_theme_board_snapshot_reads_cache_and_overlays(monkeypatch):
    from app.services import theme_board_snapshot as mod
    from app.models import Holding

    cached = {
        "items": [
            {"sector_label": "半导体", "board_kind": "concept",
             "secid": "90.BK1036", "change_1d_percent": 2.0, "consecutive_up_days": 3},
            {"sector_label": "电子", "board_kind": "industry",
             "secid": "90.BK0447", "change_1d_percent": 1.0, "consecutive_up_days": 1},
        ],
        "trade_date": "2026-06-18", "session_kind": "trading_day_intraday",
        "refreshed_at": "2026-06-18T06:00:00+00:00",
    }
    monkeypatch.setattr(mod, "get_spot_snapshot", lambda *a, **k: cached)
    holding = Holding(fund_code="000001", fund_name="半导体基金", sector_name="半导体")
    payload = mod.get_theme_board_snapshot(holdings=[holding], sort="change")
    assert payload["from_cache"] is True
    assert payload["refreshed_at"] == "2026-06-18T06:00:00+00:00"
    semi = next(i for i in payload["items"] if i["sector_label"] == "半导体")
    assert semi["in_portfolio"] is True
    assert semi["board_kind"] == "concept"
    assert "linked_fund_count" not in semi
    assert payload["items"][0]["change_1d_percent"] >= payload["items"][1]["change_1d_percent"]
```

- [ ] **Step 2: 运行确认失败** → FAIL（旧实现叠加 linked_fund_count / 无 board_kind）

- [ ] **Step 3: 实现**。重写 `apply_holdings_overlay`、`build_theme_board_payload`、`get_theme_board_snapshot`：

```python
def _holding_secids(holdings: list[Holding]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for holding in holdings:
        canon = get_quote_canonical_sector(holding.sector_name) or get_canonical_sector(holding.sector_name)
        if canon is None:
            continue
        counts[canon.eastmoney_secid] = counts.get(canon.eastmoney_secid, 0) + 1
    return counts


def apply_holdings_overlay(items: list[dict[str, Any]], holdings: list[Holding]) -> list[dict[str, Any]]:
    held = _holding_secids(holdings or [])
    out = []
    for item in items:
        count = held.get(str(item.get("secid")), 0)
        out.append({**item, "held_fund_count": count, "in_portfolio": count > 0})
    return out


def build_theme_board_payload(items, *, sort, snapshot_meta, holdings=None):
    overlaid = apply_holdings_overlay(items, holdings or [])
    sorted_items = _sort_theme_items(overlaid, sort=sort)
    ranked = [{**row, "rank": i + 1} for i, row in enumerate(sorted_items)]
    return {
        "trade_date": snapshot_meta.get("trade_date"),
        "session_kind": snapshot_meta.get("session_kind"),
        "available": snapshot_meta.get("available", False),
        "from_cache": snapshot_meta.get("from_cache", False),
        "stale": snapshot_meta.get("stale", False),
        "refreshed_at": snapshot_meta.get("refreshed_at"),
        "message": snapshot_meta.get("message"),
        "sort": sort,
        "items": ranked,
    }


def get_theme_board_snapshot(*, force_refresh=False, holdings=None, sort="change", fetch_series=None):
    session = build_trading_session()
    trade_date = session.get("effective_trade_date")
    session_kind = session.get("session_kind", "")
    cache_key = f"theme:boards:{_CACHE_VERSION}:{trade_date}"

    cached = None
    if not force_refresh:
        cached = get_spot_snapshot(cache_key, ttl_seconds=_CLOSED_TTL_SECONDS)
    if cached is None:
        cached = get_spot_snapshot_any_age(cache_key)

    if cached is None or force_refresh:
        cached = refresh_theme_board_snapshot(trade_date=trade_date)
        from_cache = False
    else:
        from_cache = True

    items = list(cached.get("items") or [])
    available = bool(items)
    snapshot_meta = {
        "trade_date": cached.get("trade_date", trade_date),
        "session_kind": cached.get("session_kind", session_kind),
        "available": available,
        "from_cache": from_cache,
        "stale": False,
        "refreshed_at": cached.get("refreshed_at"),
        "message": None if available else "行情暂不可用，请稍后重试",
    }
    return build_theme_board_payload(items, sort=sort, snapshot_meta=snapshot_meta, holdings=holdings)
```

`_sort_theme_items` 保留（None 排末尾）。删除/不再使用：`build_linked_fund_counts` 在 payload 路径的调用、`_merge_theme_board_rows`、`_build_theme_board_items`、`_enrich_theme_board_daily_change`、`_enrich_theme_board_streak`、`_theme_streak_unavailable_hint`、`_lookup_spot_change_fallback`。保留 `_load_theme_spot_changes`、`_lookup_spot_change`（兜底）、`compute_consecutive_up_days`、`_latest_change_percent`、`_bars_through_trade_date`、`_as_float`。

> 同步删除 `test_theme_board_snapshot.py` 中针对已删函数的旧用例：`test_build_linked_fund_counts_includes_seeds`、`test_merge_theme_board_rows_fills_all_labels`、`test_enrich_theme_board_prefers_kline_over_spot`、`test_theme_streak_unavailable_hint`、`test_apply_holdings_overlay`（改为新 secid 口径）。

- [ ] **Step 4: 运行整文件** `./.venv/Scripts/python.exe -m pytest tests/test_theme_board_snapshot.py -v` → PASS

- [ ] **Step 5: Commit** `git add -A; git commit -m "feat(theme-boards): 只读缓存+持仓secid叠加+board_kind payload"`

---

## Task 5: 后台刷新线程 + lifespan + conftest

**Files:** Modify `apps/api/app/services/theme_board_snapshot.py`, `apps/api/app/lifespan.py`, `apps/api/tests/conftest.py`

- [ ] **Step 1:** 在 `theme_board_snapshot.py` 末尾新增循环（无需单测，集成验证）：

```python
import os


def _refresh_enabled() -> bool:
    from app.config import get_settings
    return get_settings().theme_board_refresh_enabled


def theme_board_refresh_loop() -> None:
    from app.config import get_settings
    try:
        refresh_theme_board_snapshot()
    except Exception as exc:
        logger.info("theme board initial refresh failed: %s", exc)
    while True:
        settings = get_settings()
        session_kind = build_trading_session().get("session_kind", "")
        interval = (
            settings.theme_board_refresh_interval_seconds
            if session_kind in {"trading_day_intraday", "trading_day_pre_close", "trading_day_pre_open"}
            else settings.theme_board_refresh_idle_interval_seconds
        )
        time.sleep(max(60, interval))
        try:
            refresh_theme_board_snapshot()
        except Exception as exc:
            logger.info("theme board refresh failed: %s", exc)
```

- [ ] **Step 2:** `lifespan.py` 增加：

```python
from app.services.theme_board_snapshot import _refresh_enabled, theme_board_refresh_loop
...
    if _refresh_enabled():
        threading.Thread(target=theme_board_refresh_loop, name="theme-board-refresh", daemon=True).start()
```

- [ ] **Step 3:** `conftest.py` autouse 设置环境变量关闭（在已有 env 设置处追加）：

```python
    monkeypatch.setenv("FUND_AI_THEME_BOARD_REFRESH_ENABLED", "false")
```
若 conftest 用 `os.environ` 直接设置则同风格追加，并在设置后调用现有的 `refresh_settings()`（若有）。

- [ ] **Step 4:** Commit `git add -A; git commit -m "feat(theme-boards): lifespan后台时段感知刷新线程"`

---

## Task 6: API smoke 更新

**Files:** Modify `apps/api/tests/test_api.py`

- [ ] **Step 1:** 更新 `test_market_theme_boards`：

```python
def test_market_theme_boards(client):
    response = client.get("/api/market/theme-boards?sort=change")
    assert response.status_code == 200
    body = response.json()
    assert body["sort"] == "change"
    assert isinstance(body["items"], list)
    if body["items"]:
        assert "board_kind" in body["items"][0]
        assert "linked_fund_count" not in body["items"][0]
```

- [ ] **Step 2:** 运行 `./.venv/Scripts/python.exe -m pytest tests/test_api.py::test_market_theme_boards tests/test_api.py::test_market_theme_boards_invalid_sort -v` → PASS

- [ ] **Step 3:** Commit `git add -A; git commit -m "test(theme-boards): API smoke 含 board_kind"`

---

## Task 7: 前端

**Files:** Modify `apps/web/src/lib/api.ts`, `marketThemeBoard.ts`, `ThemeSectorOverview.tsx`, `MarketTab.tsx`

- [ ] **Step 1:** `api.ts` 找到 `MarketThemeBoardItem`/response：加 `board_kind`、`refreshed_at`，删 `linked_fund_count`：

```ts
export type MarketThemeBoardKind = "industry" | "concept" | "index";
// MarketThemeBoardItem 增 board_kind: MarketThemeBoardKind; 删 linked_fund_count
// response 增 refreshed_at?: string | null;
```

- [ ] **Step 2:** `marketThemeBoard.ts` 新增：

```ts
export function formatBoardKindLabel(kind: string): string {
  return kind === "industry" ? "行业" : kind === "index" ? "指数" : "概念";
}
export function boardKindClass(kind: string): string {
  if (kind === "industry") return "bg-slate-100 text-slate-600";
  if (kind === "index") return "bg-violet-100 text-violet-700";
  return "bg-amber-100 text-amber-700";
}
export function formatThemeBoardUpdatedFromIso(iso: string | null | undefined): string {
  if (!iso) return "加载中…";
  return formatThemeBoardUpdatedAt(new Date(iso));
}
```

- [ ] **Step 3:** `ThemeSectorOverview.tsx`：板块名旁渲染 board_kind 标签；更新时间用 `data?.refreshed_at`：

```tsx
import { formatBoardKindLabel, boardKindClass, formatThemeBoardUpdatedFromIso } from "@/lib/marketThemeBoard";
// 板块名 span 后：
<span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${boardKindClass(item.board_kind)}`}>
  {formatBoardKindLabel(item.board_kind)}
</span>
// 更新于行改为 formatThemeBoardUpdatedFromIso(data?.refreshed_at)
```

- [ ] **Step 4:** `MarketTab.tsx`：theme `useCachedFetch` 的 `staleTimeMs` 改 `900_000`。

- [ ] **Step 5:** 验证 `cd apps/web; npm run lint; npm run typecheck` → PASS

- [ ] **Step 6:** Commit `git add -A; git commit -m "feat(web): 主题板块board_kind标签+15min缓存"`

---

## Task 8: 文档

**Files:** Modify `docs/PROJECT_CONTEXT.md`, `.env.example`

- [ ] **Step 1:** `.env.example` 板块实时区块追加 3 个变量及注释。
- [ ] **Step 2:** `PROJECT_CONTEXT.md`：更新记录加 2026-06-18 主题板块扩展条目；API 表 `theme-boards` 行补 board_kind/refreshed_at/后台刷新；环境变量表加 3 行；修改指引第 18 条提及后台刷新线程。
- [ ] **Step 3:** Commit `git add -A; git commit -m "docs: 主题板块扩展同步PROJECT_CONTEXT与env"`

---

## Task 9: 全量验证

- [ ] **Step 1:** `cd apps/api; ./.venv/Scripts/python.exe -m pytest tests -q` → 全绿
- [ ] **Step 2:** `cd apps/web; npm run lint; npm run typecheck; npm run build` → 通过
- [ ] **Step 3:** 手动起 API（可选）确认 `GET /api/market/theme-boards` 返回行数 > 21、含 board_kind、from_cache。
- [ ] **Step 4:** Final commit（若有遗留）。

---

## Self-Review

- **Spec coverage:** universe 合并(T2)、同源 change+streak(T3)、缓存 v3(T3)、只读缓存+持仓(T4)、board_kind(T2/T4/T7)、后台线程时段感知(T5)、env(T1/T5)、移除 linked_fund_count(T4)、前端标签+15min(T7)、文档(T8)、验证(T9) — 全覆盖。
- **类型一致:** `board_kind`/`secid`/`refreshed_at`/`held_fund_count`/`in_portfolio` 在后端 payload 与前端类型一致；`MarketThemeBoardKind` 三值与后端 `_board_kind_from_source_type` 一致。
- **占位符:** 无。
