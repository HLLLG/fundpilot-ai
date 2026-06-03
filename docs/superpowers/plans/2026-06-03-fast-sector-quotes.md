# Fast Sector Quotes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Make Yangjibao-style sector quote refresh return quickly, avoid blocking on failing Eastmoney/AkShare calls, and clearly report whether data came from live providers or stale cache.

**Architecture:** Keep the existing provider/resolver/service boundaries. Add provider metadata beside the existing boards payload, enforce a real live-call time budget by passing short per-provider timeouts, and let the API response expose `provider_path` / cache status without changing holding math.

**Tech Stack:** FastAPI, Pydantic v2 models/dicts, pytest with monkeypatch, existing SQLite sector spot cache.

---

## File Structure

- Modify `apps/api/app/services/sector_quote_provider.py`: add `SpotBoardFetchResult`, live/cache provider path metadata, and bounded Eastmoney/AkShare calls.
- Modify `apps/api/app/services/sector_quote_service.py`: consume provider metadata and include it in success/failure responses.
- Modify `apps/api/app/main.py`: keep the frontend refresh budget at 5 seconds but rely on provider-level enforcement.
- Modify `apps/api/tests/test_sector_quote_provider.py`: regression tests for bounded Eastmoney timeout arguments, stale cache metadata, and skipping slow AkShare when no budget remains.
- Modify `apps/api/tests/test_sector_quote_api.py`: assert the route passes a timeout budget and response can carry provider metadata.
- Keep existing resolver/on-demand behavior unchanged except that it benefits from faster board fetch.

### Task 1: Provider Metadata And Real Time Budget

**Files:**
- Modify: `apps/api/app/services/sector_quote_provider.py`
- Test: `apps/api/tests/test_sector_quote_provider.py`

- [x] **Step 1: Write failing provider tests**

Add tests that express the desired behavior:

```python
def test_fetch_spot_boards_result_uses_short_eastmoney_timeout(monkeypatch):
    from app.services import sector_quote_provider as provider

    calls = []
    monkeypatch.setattr(provider, "get_spot_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(provider, "save_spot_snapshot", lambda *a, **k: None)

    class Settings:
        sector_quotes_enabled = True
        sector_quotes_ttl_seconds = 60

    monkeypatch.setattr(provider, "get_settings", lambda: Settings())

    def fake_eastmoney(**kwargs):
        calls.append(kwargs)
        return {"concept": {"商业航天": 1.2}, "industry": {}, "index": {}}

    monkeypatch.setattr(provider, "fetch_eastmoney_boards", fake_eastmoney)
    monkeypatch.setattr(provider, "fetch_boards_via_akshare", lambda **_: {"concept": {}, "industry": {}, "index": {}})

    result = provider.fetch_spot_boards_result(force_refresh=True, timeout_seconds=5.0)

    assert result.provider_path == "eastmoney_live"
    assert result.from_stale_cache is False
    assert calls == [{"timeout": 1.5, "max_retries": 1}]


def test_fetch_spot_boards_result_returns_stale_cache_with_metadata(monkeypatch):
    from app.services import sector_quote_provider as provider

    stale = {"concept": {"旧板块": 0.5}, "industry": {}, "index": {}}

    class Settings:
        sector_quotes_enabled = True
        sector_quotes_ttl_seconds = 60

    monkeypatch.setattr(provider, "get_settings", lambda: Settings())
    monkeypatch.setattr(provider, "get_spot_snapshot", lambda _key, ttl_seconds: None if ttl_seconds == 60 else stale)
    monkeypatch.setattr(provider, "fetch_eastmoney_boards", lambda **_: {"concept": {}, "industry": {}, "index": {}})
    monkeypatch.setattr(provider, "fetch_boards_via_akshare", lambda **_: {"concept": {}, "industry": {}, "index": {}})

    result = provider.fetch_spot_boards_result(force_refresh=True, timeout_seconds=0.1)

    assert result.boards == stale
    assert result.provider_path == "stale_cache"
    assert result.from_stale_cache is True
    assert result.live_attempted is True


def test_fetch_live_boards_skips_akshare_when_budget_is_exhausted(monkeypatch):
    from app.services import sector_quote_provider as provider

    akshare_called = False

    def fake_akshare(**kwargs):
        nonlocal akshare_called
        akshare_called = True
        return {"concept": {"不应调用": 1.0}, "industry": {}, "index": {}}

    monkeypatch.setattr(provider, "fetch_eastmoney_boards", lambda **_: {"concept": {}, "industry": {}, "index": {}})
    monkeypatch.setattr(provider, "fetch_boards_via_akshare", fake_akshare)

    result = provider._fetch_live_boards(timeout_seconds=0.01)

    assert result.boards == {"index": {}, "concept": {}, "industry": {}}
    assert result.provider_path == "empty"
    assert akshare_called is False
```

- [x] **Step 2: Run provider tests to verify RED**

Run: `cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/test_sector_quote_provider.py -q`

Expected: FAIL because `fetch_spot_boards_result` and provider metadata do not exist yet.

- [x] **Step 3: Implement provider result object and bounded calls**

Implement a small dataclass:

```python
@dataclass(frozen=True)
class SpotBoardFetchResult:
    boards: dict[str, SpotBoard]
    provider_path: str
    from_stale_cache: bool = False
    live_attempted: bool = False
    elapsed_seconds: float = 0.0
```

Add `fetch_spot_boards_result(...)` and make existing `fetch_spot_boards(...)` return only `result.boards` for compatibility. In `_fetch_live_boards`, call `fetch_eastmoney_boards(timeout=min(1.5, timeout_seconds * 0.3), max_retries=1)` when a frontend budget exists. Skip AkShare if elapsed time is already greater than `timeout_seconds * 0.5`; otherwise call it once.

- [x] **Step 4: Run provider tests to verify GREEN**

Run: `cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/test_sector_quote_provider.py -q`

Expected: PASS.

### Task 2: Service Response Metadata

**Files:**
- Modify: `apps/api/app/services/sector_quote_service.py`
- Test: `apps/api/tests/test_sector_quote_service.py`

- [x] **Step 1: Write failing service test**

Add a test that monkeypatches `fetch_spot_boards_result` to return stale cache metadata and asserts the API service response is not ambiguous:

```python
def test_refresh_sector_quotes_reports_stale_cache_provider(monkeypatch):
    from app.services import sector_quote_service as service
    from app.services.sector_quote_provider import SpotBoardFetchResult

    holding = Holding(
        fund_code="015608",
        fund_name="测试基金",
        holding_amount=1000,
        return_percent=0,
        sector_name="半导体",
        sector_return_percent=0.1,
    )

    monkeypatch.setattr(
        service,
        "fetch_spot_boards_result",
        lambda **_: SpotBoardFetchResult(
            boards={"concept": {"半导体": 1.23}, "industry": {}, "index": {}},
            provider_path="stale_cache",
            from_stale_cache=True,
            live_attempted=True,
            elapsed_seconds=0.02,
        ),
    )

    result = service.refresh_holdings_sector_quotes([holding], force_refresh=True, timeout_seconds=5.0)

    assert result["ok"] is True
    assert result["provider_path"] == "stale_cache"
    assert result["from_stale_cache"] is True
    assert result["summary"]["provider_path"] == "stale_cache"
```

- [x] **Step 2: Run service test to verify RED**

Run: `cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/test_sector_quote_service.py::test_refresh_sector_quotes_reports_stale_cache_provider -q`

Expected: FAIL because the service calls `fetch_spot_boards` and does not return provider metadata.

- [x] **Step 3: Implement service metadata**

Change `sector_quote_service.py` to import `fetch_spot_boards_result`, use `fetch_result.boards`, and include these keys in both success and provider-failure responses:

```python
"provider_path": fetch_result.provider_path,
"from_stale_cache": fetch_result.from_stale_cache,
"provider_elapsed_seconds": fetch_result.elapsed_seconds,
```

Also copy `provider_path` and `from_stale_cache` into `summary`.

- [x] **Step 4: Run service test to verify GREEN**

Run: `cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/test_sector_quote_service.py::test_refresh_sector_quotes_reports_stale_cache_provider -q`

Expected: PASS.

### Task 3: Route Timeout Regression

**Files:**
- Modify: `apps/api/tests/test_sector_quote_api.py`
- Review: `apps/api/app/main.py`

- [x] **Step 1: Add route assertion**

Update `test_refresh_sector_quotes_endpoint` monkeypatch to capture `timeout_seconds` and assert it equals `5.0`.

- [x] **Step 2: Run route test**

Run: `cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/test_sector_quote_api.py::test_refresh_sector_quotes_endpoint -q`

Expected: PASS if `main.py` still passes the budget.

### Task 4: Focused Regression Suite And Live Smoke

**Files:**
- No production file changes.

- [x] **Step 1: Run focused tests**

Run: `cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/test_sector_quote_provider.py tests/test_sector_quote_service.py tests/test_sector_quote_api.py tests/test_board_fetch_integration.py -q`

Expected: PASS.

- [x] **Step 2: Run local timing smoke**

Run a Python snippet that calls `fetch_spot_boards_result(force_refresh=True, timeout_seconds=5.0)` and prints elapsed/counts/provider path.

Expected: returns within roughly 5 seconds even on blocked network, with `provider_path` either `eastmoney_live`, `akshare_live`, `stale_cache`, or `empty`.

---

## Self-Review

- Spec coverage: The plan covers faster refresh, avoiding blocked AkShare, and explicit cache/source metadata. It intentionally does not add Yangjibao login API in this iteration.
- Placeholder scan: No task uses placeholder language; each code step has exact command and expected output.
- Type consistency: `SpotBoardFetchResult`, `provider_path`, `from_stale_cache`, and `elapsed_seconds` are defined before use.

