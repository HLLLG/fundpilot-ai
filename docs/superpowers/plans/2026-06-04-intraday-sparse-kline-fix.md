# 分时图稀疏 K 线修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 025856（只显示 2 点斜线）和 519674（暂无分时数据）两个分时图 bug。

**Architecture:** 在 `fetch_eastmoney_intraday_trends()` 内引入有效分时阈值（30 点），当 kline 返回稀疏骨架点时继续尝试 trends2，取点数更多的结果；对中证指数跳过无效的 AkShare `index_zh_a_hist_min_em` fallback 并补充日志。

**Tech Stack:** Python 3.11，pytest，requests，apps/api/app/services/eastmoney_trends_client.py，apps/api/app/services/sector_intraday_provider.py

---

## 文件清单

| 操作 | 路径 |
|------|------|
| Modify | `apps/api/app/services/eastmoney_trends_client.py` |
| Modify | `apps/api/app/services/sector_intraday_provider.py` |
| Modify | `apps/api/tests/test_eastmoney_trends_client.py` |

---

### Task 1：稀疏 kline 时 fallthrough 到 trends2

**目标：** 当 kline 返回点数 < 30（骨架点）时，继续尝试 trends2，最终返回点数更多的结果。

**Files:**
- Modify: `apps/api/app/services/eastmoney_trends_client.py:95-134`
- Test: `apps/api/tests/test_eastmoney_trends_client.py`

- [ ] **Step 1：写失败测试**

在 `apps/api/tests/test_eastmoney_trends_client.py` 末尾追加：

```python
def test_sparse_kline_falls_through_to_trends2(monkeypatch):
    """kline 返回 2 个骨架点时，应继续尝试 trends2，返回 trends2 的完整结果。"""

    call_log: list[str] = []

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload
            self.text = ""

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    def make_kline_response():
        # 只返回开盘 + 收盘 2 个骨架点
        return FakeResponse(
            {
                "data": {
                    "preKPrice": 100.0,
                    "klines": [
                        "2026-06-04 09:31,99,101,102,99,0,0,0,0.2,0,0,0",
                        "2026-06-04 15:00,100,110,111,109,0,0,0,0.8,0,0,0",
                    ],
                }
            }
        )

    def make_trends2_response():
        # trends2 返回 30 个完整分钟点
        trends = [
            f"2026-06-04 {h:02d}:{m:02d},1000,{1000 + i},0,0,0,0,{1000 + i}"
            for i, (h, m) in enumerate(
                [(9, 31 + j) if j < 29 else (14, 59) for j in range(30)]
            )
        ]
        return FakeResponse({"data": {"prePrice": 1000.0, "trends": trends}})

    class FakeSession:
        def __init__(self):
            self.headers = {}

        def get(self, url, params=None, timeout=None, proxies=None):
            if "kline/get" in url:
                call_log.append("kline")
                return make_kline_response()
            if "trends2/get" in url:
                call_log.append("trends2")
                return make_trends2_response()
            raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(
        "app.services.eastmoney_trends_client.requests.Session",
        lambda: FakeSession(),
    )

    points = fetch_eastmoney_intraday_trends(
        "2.931994",
        source_code="931994",
        trade_date="2026-06-04",
    )

    # trends2 必须被调用
    assert "trends2" in call_log, "sparse kline should have fallen through to trends2"
    # 最终结果应是 trends2 的 30 点，而非 kline 的 2 点
    assert len(points) == 30, f"expected 30 points from trends2, got {len(points)}"


def test_rich_kline_does_not_call_trends2(monkeypatch):
    """kline 返回 ≥30 个点时，不应调用 trends2。"""

    call_log: list[str] = []

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload
            self.text = ""

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    class FakeSession:
        def __init__(self):
            self.headers = {}

        def get(self, url, params=None, timeout=None, proxies=None):
            if "kline/get" in url:
                call_log.append("kline")
                # 构造 30 个正常分钟点
                klines = [
                    f"2026-06-04 09:{31 + i:02d},99,{100 + i},{101 + i},99,0,0,0,{i * 0.1:.1f},0,0,0"
                    for i in range(30)
                ]
                return FakeResponse(
                    {"data": {"preKPrice": 100.0, "klines": klines}}
                )
            if "trends2/get" in url:
                call_log.append("trends2")
                return FakeResponse({"data": {"trends": []}})
            raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(
        "app.services.eastmoney_trends_client.requests.Session",
        lambda: FakeSession(),
    )

    points = fetch_eastmoney_intraday_trends(
        "2.931994",
        source_code="931994",
        trade_date="2026-06-04",
    )

    assert "trends2" not in call_log, "rich kline should NOT call trends2"
    assert len(points) == 30
```

- [ ] **Step 2：运行新测试，确认失败**

```bash
cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_eastmoney_trends_client.py::test_sparse_kline_falls_through_to_trends2 tests/test_eastmoney_trends_client.py::test_rich_kline_does_not_call_trends2 -v
```

预期：两个测试均 **FAIL**（`test_sparse_kline_falls_through_to_trends2` 失败因为 trends2 未被调用；`test_rich_kline_does_not_call_trends2` 可能通过也可能失败）。

- [ ] **Step 3：修改 `fetch_eastmoney_intraday_trends`**

在 `apps/api/app/services/eastmoney_trends_client.py` 中：

在文件顶部常量区（`_TRENDS_UT` 行之后，`IntradayPoint` 行之前）新增：

```python
# kline 返回点数低于此阈值时视为稀疏骨架，继续尝试 trends2
_MIN_RICH_INTRADAY_POINTS = 30
```

将 `fetch_eastmoney_intraday_trends` 函数体内的 `for candidate` 循环（第 113-132 行）替换为：

```python
    for candidate in _secid_candidates(cleaned, source_code):
        _apply_referer(session, candidate, source_code)
        # 中证 zz/2.{code} 分钟 K 更稳；趋势2 作备用
        if candidate.startswith("2."):
            primary, secondary = _fetch_kline_intraday, _fetch_trends2_intraday
        else:
            primary, secondary = _fetch_trends2_intraday, _fetch_kline_intraday

        shared = dict(
            trade_date=trade_date,
            timeout=timeout,
            max_retries=max_retries,
            proxies=proxies,
        )
        primary_points = primary(session, candidate, **shared)

        if len(primary_points) >= _MIN_RICH_INTRADAY_POINTS:
            return primary_points

        # primary 稀疏或为空，继续尝试 secondary
        secondary_points = secondary(session, candidate, **shared)

        # 取点数更多的结果；若两者都充足以 primary 优先
        best = (
            primary_points
            if len(primary_points) >= len(secondary_points)
            else secondary_points
        )
        if len(best) >= _MIN_RICH_INTRADAY_POINTS:
            return best

        # 两者都不足，保存备用，继续尝试下一个 candidate
        if best:
            # 有数据但不足 30 点，记录供最终兜底
            pass

    return []
```

**注意**：上面最后的 `pass` 块需要改为实际保存稀疏备用并在循环结束后返回，完整替换如下（包含稀疏兜底）：

```python
    best_sparse: list[IntradayPoint] = []

    for candidate in _secid_candidates(cleaned, source_code):
        _apply_referer(session, candidate, source_code)
        if candidate.startswith("2."):
            primary, secondary = _fetch_kline_intraday, _fetch_trends2_intraday
        else:
            primary, secondary = _fetch_trends2_intraday, _fetch_kline_intraday

        shared = dict(
            trade_date=trade_date,
            timeout=timeout,
            max_retries=max_retries,
            proxies=proxies,
        )
        primary_points = primary(session, candidate, **shared)

        if len(primary_points) >= _MIN_RICH_INTRADAY_POINTS:
            return primary_points

        secondary_points = secondary(session, candidate, **shared)

        best = (
            primary_points
            if len(primary_points) >= len(secondary_points)
            else secondary_points
        )
        if len(best) >= _MIN_RICH_INTRADAY_POINTS:
            return best

        if len(best) > len(best_sparse):
            best_sparse = best

    return best_sparse
```

- [ ] **Step 4：运行新测试，确认通过**

```bash
cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_eastmoney_trends_client.py::test_sparse_kline_falls_through_to_trends2 tests/test_eastmoney_trends_client.py::test_rich_kline_does_not_call_trends2 -v
```

预期：两个测试均 **PASS**。

- [ ] **Step 5：运行全部 trends client 测试，确认无回归**

```bash
cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_eastmoney_trends_client.py -v
```

预期：全部 **PASS**。

- [ ] **Step 6：提交**

```bash
git add apps/api/app/services/eastmoney_trends_client.py apps/api/tests/test_eastmoney_trends_client.py
git commit -m "fix: fall through to trends2 when kline returns sparse intraday points"
```

---

### Task 2：跳过对中证指数无效的 AkShare fallback，补充日志

**目标：** `_fetch_index_intraday` 中，中证指数（code 以 `93` 开头）跳过 `index_zh_a_hist_min_em` 调用（该接口不支持中证指数），并在跳过时输出 debug 日志，方便排查 519674 等场景。

**Files:**
- Modify: `apps/api/app/services/sector_intraday_provider.py:199-224`

- [ ] **Step 1：修改 `_fetch_index_intraday`**

将 `apps/api/app/services/sector_intraday_provider.py` 中第 216-224 行的 AkShare 调用块替换为：

```python
    if symbol:
        # AkShare index_zh_a_hist_min_em 仅支持主流上证/深证指数（如 000001）；
        # 中证指数（93xxxx）不在其支持范围，调用会静默返回空 DataFrame，跳过。
        if symbol.startswith("93"):
            logger.debug(
                "skipping akshare index_zh_a_hist_min_em for CSI index %s (not supported)",
                symbol,
            )
        else:
            try:
                frame = _call_akshare_index_min(symbol)
                parsed = _points_from_minute_frame(frame)
                if parsed:
                    return parsed
                logger.debug(
                    "akshare index intraday returned empty for %s", symbol
                )
            except Exception as exc:
                logger.debug(
                    "akshare index intraday fallback failed for %s: %s", symbol, exc
                )
    return []
```

- [ ] **Step 2：运行相关测试，确认无回归**

```bash
cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_eastmoney_trends_client.py tests/test_infer_intraday_index.py tests/test_sector_intraday.py -v
```

预期：全部 **PASS**。

- [ ] **Step 3：提交**

```bash
git add apps/api/app/services/sector_intraday_provider.py
git commit -m "fix: skip unsupported akshare csi index fallback, add debug logging"
```

---

### Task 3：全量回归测试

**目标：** 确认两项修改没有破坏其他测试。

**Files:**
- 无文件变更，仅运行测试

- [ ] **Step 1：运行全套 API 测试**

```bash
cd apps/api && .venv/Scripts/python.exe -m pytest tests -q
```

预期：全部通过（当前基线 145+ 项），无新增失败。

- [ ] **Step 2：手动验证 025856（若 API 服务正在运行）**

```powershell
curl.exe -s "http://127.0.0.1:8000/api/sector-quotes/intraday?source_type=index&source_name=%E4%B8%AD%E8%AF%81%E7%94%B5%E7%BD%91%E8%AE%BE%E5%A4%87&force_refresh=true" | python -c "import sys,json; d=json.load(sys.stdin); print('points:', len(d['points'])); print('note:', d.get('note'))"
```

预期：`points` 数量 ≥ 30（趋势2 返回完整分钟数据时约 240 点）。

- [ ] **Step 3：提交完成标记（若有未提交内容）**

若 Task 1 和 Task 2 均已提交，此步骤无需额外提交。

---

## 验收标准回顾

| 标准 | 验证方式 |
|------|---------|
| `pytest tests/test_eastmoney_trends_client.py` 全部通过（含新增 2 个测试） | Task 1 Step 5 |
| 025856 `points` ≥ 30 | Task 3 Step 2 |
| 全套 pytest 无回归 | Task 3 Step 1 |
