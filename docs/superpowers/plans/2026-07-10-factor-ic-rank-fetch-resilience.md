# Factor IC Rank Fetch Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent the weekly Factor IC workflow from collapsing to an empty universe when the AkShare all-fund ranking request stalls on a GitHub-hosted runner.

**Architecture:** Keep the existing subprocess isolation and Eastmoney ranking semantics, but request only the caller's bounded result count from the same `rankhandler.aspx` endpoint. Bound every attempt with HTTP and subprocess timeouts, retry transient failures three times, and make the Factor IC runner fail explicitly before NAV work or artifact creation when the ranking source remains unavailable.

**Tech Stack:** Python 3.12, requests through the existing AkShare dependency set, subprocess isolation, pytest 9, GitHub Actions.

---

## File Structure

- Create `apps/api/tests/test_factor_ic_rank_fetch.py`: offline regression tests for bounded requests and retry count.
- Modify `apps/api/app/services/akshare_subprocess.py`: bounded Eastmoney rank script and three-attempt orchestration.
- Modify `apps/api/tests/test_factor_ic_backtest.py`: runner failure semantics and CLI error regression tests.
- Modify `apps/api/scripts/run_factor_ic.py`: `FactorIcRankUnavailable`, early abort, and concise CLI failure output.
- Modify `docs/superpowers/specs/2026-07-10-factor-ic-rank-fetch-resilience-design.md`: mark the confirmed design implemented after verification.

### Task 1: Bound and retry the ranking request

**Files:**
- Create: `apps/api/tests/test_factor_ic_rank_fetch.py`
- Modify: `apps/api/app/services/akshare_subprocess.py`

- [ ] **Step 1: Write failing offline retry tests**

Create `apps/api/tests/test_factor_ic_rank_fetch.py`:

```python
from __future__ import annotations

from types import SimpleNamespace

from app.services import akshare_subprocess as target

_REAL_FETCH_OPEN_FUND_RANK = target.fetch_open_fund_rank


def test_open_fund_rank_limits_request_and_retries_transient_timeouts(
    monkeypatch,
) -> None:
    calls: list[dict] = []
    sleeps: list[float] = []
    outcomes = iter(
        [
            None,
            None,
            {"data": [{"fund_code": "000001", "fund_name": "测试基金"}]},
        ]
    )

    def fake_runner(script: str, *, label: str, timeout: int | float):
        calls.append({"script": script, "label": label, "timeout": timeout})
        return next(outcomes)

    monkeypatch.setattr(target, "run_akshare_json_script", fake_runner)
    monkeypatch.setattr(
        target.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy direct subprocess path")
        ),
    )
    monkeypatch.setattr(
        target,
        "time",
        SimpleNamespace(sleep=sleeps.append),
        raising=False,
    )

    rows = _REAL_FETCH_OPEN_FUND_RANK(limit=500)

    assert rows == [{"fund_code": "000001", "fund_name": "测试基金"}]
    assert len(calls) == 3
    assert sleeps == [2.0, 5.0]
    script = calls[0]["script"]
    assert '"pn": "500"' in script
    assert "timeout=(5, 20)" in script
    assert "fund_open_fund_rank_em" not in script
    assert all(call["timeout"] == 35 for call in calls)


def test_open_fund_rank_stops_after_three_failures(monkeypatch) -> None:
    calls: list[dict] = []
    sleeps: list[float] = []

    def always_fail(script: str, *, label: str, timeout: int | float):
        calls.append({"script": script, "label": label, "timeout": timeout})
        return None

    monkeypatch.setattr(target, "run_akshare_json_script", always_fail)
    monkeypatch.setattr(
        target.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy direct subprocess path")
        ),
    )
    monkeypatch.setattr(
        target,
        "time",
        SimpleNamespace(sleep=sleeps.append),
        raising=False,
    )

    assert _REAL_FETCH_OPEN_FUND_RANK(limit=500) is None
    assert len(calls) == 3
    assert sleeps == [2.0, 5.0]
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
cd apps/api
./.venv/Scripts/python.exe -m pytest tests/test_factor_ic_rank_fetch.py -q
```

Expected: both tests fail because the current implementation makes one subprocess attempt and still calls `fund_open_fund_rank_em()`.

- [ ] **Step 3: Implement the bounded direct request and retry loop**

In `apps/api/app/services/akshare_subprocess.py`, add:

```python
import time

_FUND_RANK_ATTEMPTS = 3
_FUND_RANK_RETRY_DELAYS = (2.0, 5.0)
_FUND_RANK_SUBPROCESS_TIMEOUT = 35
```

Replace `fetch_open_fund_rank()` with a script that uses the same Eastmoney endpoint and ranking parameters while limiting `pn` to `cap`:

```python
def fetch_open_fund_rank(*, limit: int = 300) -> list[dict] | None:
    """读取开放式基金近一年排行榜；限量、有界并重试瞬时失败。"""
    cap = max(50, min(limit, 500))
    script = f"""
from datetime import date
import json
import requests
from akshare.utils import demjson

end = date.today()
try:
    start = end.replace(year=end.year - 1)
except ValueError:
    start = end.replace(year=end.year - 1, day=28)

params = {{
    "op": "ph", "dt": "kf", "ft": "all", "rs": "", "gs": "0",
    "sc": "1nzf", "st": "desc", "sd": start.isoformat(),
    "ed": end.isoformat(), "qdii": "", "tabSubtype": ",,,,,",
    "pi": "1", "pn": "{cap}", "dx": "1", "v": "0.1591891419018292",
}}
headers = {{
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://fund.eastmoney.com/fundguzhi.html",
}}

def number(parts, index):
    if index >= len(parts) or parts[index] in ("", "--"):
        return None
    try:
        return float(parts[index])
    except (TypeError, ValueError):
        return None

try:
    response = requests.get(
        "https://fund.eastmoney.com/data/rankhandler.aspx",
        params=params,
        headers=headers,
        timeout=(5, 20),
    )
    response.raise_for_status()
    start_index = response.text.find("{{")
    end_index = response.text.rfind("}}")
    if start_index < 0 or end_index < start_index:
        raise ValueError("rank payload missing object")
    payload = demjson.decode(response.text[start_index : end_index + 1])
    rows = []
    for raw in (payload.get("datas") or [])[:{cap}]:
        parts = str(raw).split(",")
        code = parts[0].strip().zfill(6) if parts else ""
        if not code.isdigit() or len(code) != 6:
            continue
        rows.append({{
            "fund_code": code,
            "fund_name": parts[1].strip() if len(parts) > 1 else "",
            "return_1y_percent": number(parts, 11),
            "return_6m_percent": number(parts, 10),
            "return_3m_percent": number(parts, 9),
            "max_drawdown_1y_percent": None,
            "fund_scale_yi": None,
        }})
    if not rows:
        raise ValueError("empty rank rows")
    print(json.dumps({{"data": rows}}, ensure_ascii=False))
except Exception as exc:
    print(json.dumps({{"error": str(exc)}}, ensure_ascii=False))
"""
    for attempt in range(_FUND_RANK_ATTEMPTS):
        payload = run_akshare_json_script(
            script,
            label=f"fund_open_rank:{cap}:attempt-{attempt + 1}",
            timeout=_FUND_RANK_SUBPROCESS_TIMEOUT,
        )
        if isinstance(payload, dict):
            rows = payload.get("data")
            if isinstance(rows, list) and rows:
                return rows
        if attempt < len(_FUND_RANK_RETRY_DELAYS):
            time.sleep(_FUND_RANK_RETRY_DELAYS[attempt])
    logger.warning("akshare fund rank unavailable after %s attempts", _FUND_RANK_ATTEMPTS)
    return None
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```powershell
cd apps/api
./.venv/Scripts/python.exe -m pytest tests/test_factor_ic_rank_fetch.py tests/test_akshare_isolation.py -q
```

Expected: all tests pass; no live network is used.

- [ ] **Step 5: Commit the bounded fetcher**

```powershell
git add -- apps/api/app/services/akshare_subprocess.py apps/api/tests/test_factor_ic_rank_fetch.py
git commit -m "fix: bound factor IC rank fetch"
```

### Task 2: Fail early with an explicit ranking error

**Files:**
- Modify: `apps/api/tests/test_factor_ic_backtest.py`
- Modify: `apps/api/scripts/run_factor_ic.py`

- [ ] **Step 1: Add failing runner and CLI tests**

Append to `apps/api/tests/test_factor_ic_backtest.py`:

```python
def test_runner_fails_before_nav_fetch_when_rank_is_unavailable(tmp_path) -> None:
    from scripts.run_factor_ic import FactorIcRankUnavailable, build_ic_report

    nav_calls = 0

    def fetch_nav(*_args):
        nonlocal nav_calls
        nav_calls += 1
        return []

    with pytest.raises(
        FactorIcRankUnavailable,
        match="开放式基金排行榜获取失败",
    ):
        build_ic_report(
            fetch_rank=lambda _limit: [],
            fetch_nav=fetch_nav,
            out_dir=str(tmp_path),
            universe_mode="sampled",
            universe_size=300,
            sample_pool_size=500,
        )

    assert nav_calls == 0
    assert not (tmp_path / "summary.json").exists()
    assert not (tmp_path / "report.txt").exists()


def test_runner_cli_reports_rank_source_failure(monkeypatch, capsys) -> None:
    from scripts import run_factor_ic as runner

    def fail(**_kwargs):
        raise runner.FactorIcRankUnavailable("开放式基金排行榜获取失败")

    monkeypatch.setattr(runner, "build_ic_report", fail)
    monkeypatch.setattr(runner.sys, "argv", ["run_factor_ic.py"])

    assert runner.main() == 2
    assert "开放式基金排行榜获取失败" in capsys.readouterr().err
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
cd apps/api
./.venv/Scripts/python.exe -m pytest \
  tests/test_factor_ic_backtest.py::test_runner_fails_before_nav_fetch_when_rank_is_unavailable \
  tests/test_factor_ic_backtest.py::test_runner_cli_reports_rank_source_failure -q
```

Expected: collection or assertion failure because `FactorIcRankUnavailable` and the early-abort behavior do not exist.

- [ ] **Step 3: Implement early failure and concise CLI handling**

In `apps/api/scripts/run_factor_ic.py`, add:

```python
class FactorIcRankUnavailable(RuntimeError):
    """The external fund ranking source produced no usable universe."""
```

At the beginning of `build_ic_report()`, fetch and validate candidates before sampling:

```python
    rank_limit = sample_pool_size if universe_mode == "sampled" else universe_size
    rank_candidates = fetch_rank(rank_limit) or []
    if not rank_candidates:
        raise FactorIcRankUnavailable(
            f"开放式基金排行榜获取失败（请求前 {rank_limit} 条）"
        )
    if universe_mode == "sampled":
        from app.services.fund_universe_sampler import sample_universe

        rank_rows = sample_universe(rank_candidates, universe_size)
    else:
        rank_rows = rank_candidates
```

Wrap the call in `main()`:

```python
    try:
        summary = build_ic_report(
            out_dir=args.out_dir,
            universe_size=args.universe_size,
            universe_mode=args.universe_mode,
            sample_pool_size=args.sample_pool_size,
            nav_days=args.nav_days,
            rebalance_step=args.rebalance_step,
            forward_days=args.forward_days,
            factor_lookback=args.factor_lookback,
            max_workers=args.max_workers,
            limit_funds=args.limit_funds,
        )
    except FactorIcRankUnavailable as exc:
        print(f"factor IC generation failed: {exc}", file=sys.stderr)
        return 2
```

- [ ] **Step 4: Run focused and component regression tests**

Run:

```powershell
cd apps/api
./.venv/Scripts/python.exe -m pytest \
  tests/test_factor_ic_backtest.py \
  tests/test_factor_ic_rank_fetch.py \
  tests/test_factor_ic_workflow_contract.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit the explicit failure behavior**

```powershell
git add -- apps/api/scripts/run_factor_ic.py apps/api/tests/test_factor_ic_backtest.py
git commit -m "fix: fail clearly when factor rank is unavailable"
```

### Task 3: Verify, document, and publish the fix

**Files:**
- Modify: `docs/superpowers/specs/2026-07-10-factor-ic-rank-fetch-resilience-design.md`

- [ ] **Step 1: Run an online diagnostic without writing production data**

From `apps/api`, call `fetch_open_fund_rank(limit=500)` once and assert 500 normalized rows are returned. This check may access Eastmoney but must not invoke the production publish API or use the publication token:

```powershell
@'
from app.services.akshare_subprocess import fetch_open_fund_rank
rows = fetch_open_fund_rank(limit=500) or []
assert len(rows) == 500, len(rows)
assert all(len(row["fund_code"]) == 6 for row in rows)
print({"rows": len(rows), "first_code": rows[0]["fund_code"]})
'@ | ./.venv/Scripts/python.exe -
```

Expected: `rows` is 500.

- [ ] **Step 2: Run the full backend suite**

```powershell
cd apps/api
$env:PYTEST_XDIST_AUTO_NUM_WORKERS='4'
./.venv/Scripts/python.exe -m pytest tests -q -n auto --dist loadscope
```

Expected: all backend tests pass; only the existing Starlette TestClient deprecation warnings may remain.

- [ ] **Step 3: Mark the design implemented**

Change the design status to:

```markdown
**状态：** 已实现并验证
```

Append this verification section, replacing a count only if the executed suite reports a different total:

```markdown
## 7. 实现验证

- 排行榜限量、三次重试、runner 提前失败与工作流契约：20 passed。
- 后端全量测试：656 passed；仅保留既有 Starlette TestClient 弃用警告。
- 在线只读诊断：同源限量请求返回 500 条标准化基金记录；未调用生产发布 API，未读取发布 Token。
```

- [ ] **Step 4: Run final safety checks**

```powershell
git diff --check
git status --short
git grep -nE "FUND_AI_FACTOR_IC_PUBLISH_TOKEN=[A-Za-z0-9_-]{32,}" -- ':!docs/superpowers/*'
```

Expected: no whitespace errors, no token matches, and only the intended implementation files plus the user's pre-existing `docs/deploy/cloudbase.md` modification appear.

- [ ] **Step 5: Commit documentation without staging the user's file**

```powershell
git add -- docs/superpowers/specs/2026-07-10-factor-ic-rank-fetch-resilience-design.md
git commit -m "docs: verify resilient factor rank fetch"
```

- [ ] **Step 6: Push and rerun acceptance**

```powershell
git push origin main
```

After the push, manually run `Actions → Factor IC Refresh → Run workflow`. Expected: the generation step passes the rank stage, the publish step succeeds, and Actions Summary reports a publish result, `universe_size >= 240`, `rebalance_count >= 12`, and all four factor rows.
