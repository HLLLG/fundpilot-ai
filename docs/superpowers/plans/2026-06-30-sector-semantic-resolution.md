# Sector Semantic Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add fast, computation-based fund-name semantic sector resolution so OCR-imported new holdings show competitor-aligned associated sectors immediately without waiting for slow benchmark or holdings fetches.

**Architecture:** Add a focused semantic resolver in `apps/api/app/services/sector_labels.py` that cleans fund product noise, extracts theme phrases, scores them against existing canonical/theme registries, and applies explicit QDII classification rules. Integrate the resolver into `apps/api/app/services/fund_primary_sector_service.py` as a `semantic_name` source that runs in fast mode while preserving manual/OCR/benchmark priority.

**Tech Stack:** Python 3, pytest, existing FastAPI service modules, existing `Holding` Pydantic model, existing sector canonical/registry helpers.

---

## File Structure

- Modify `apps/api/app/services/sector_labels.py`
  - Owns label normalization and name-based inference helpers.
  - Add `SemanticSectorCandidate` and `infer_semantic_sector_from_fund_name`.
  - Keep legacy `infer_sector_label_from_fund_name` for compatibility, but make it delegate to the new resolver where safe.

- Modify `apps/api/app/services/fund_primary_sector_service.py`
  - Add `semantic_name` to `_SOURCE_PRIORITY`.
  - Call the semantic resolver from `resolve_primary_sector` when `allow_name_infer=True`.
  - Let OCR fast path call `resolve_primary_sector(... allow_name_infer=True, fetch_benchmark=False)` through `apply_primary_sector_to_holding`.
  - Let `refresh_benchmark_sectors_for_holdings(fetch_missing_benchmark=False)` still apply semantic sectors without fetching benchmarks.

- Modify `apps/api/tests/test_fund_sector_auto_match.py`
  - Add unit tests for semantic extraction and primary-sector integration.

- Modify `apps/api/tests/test_holdings_fast_sector_resolution.py`
  - Add fast-mode regression tests proving no benchmark fetch happens while semantic sectors are applied.

- Modify `apps/api/tests/test_apply_holdings_fast_path.py`
  - Add OCR fast-path regression proving new imported holdings get sector labels immediately.

---

### Task 1: Add Semantic Name Resolver Tests

**Files:**
- Modify: `apps/api/tests/test_fund_sector_auto_match.py`
- Later implementation target: `apps/api/app/services/sector_labels.py`

- [ ] **Step 1: Write failing semantic resolver unit tests**

Append these tests to `apps/api/tests/test_fund_sector_auto_match.py`:

```python
def test_semantic_sector_from_fund_name_matches_competitor_examples():
    from app.services.sector_labels import infer_semantic_sector_from_fund_name

    cases = {
        "华夏中证电网设备主题ETF发起式联接C": "电网设备",
        "中欧上证科创板人工智能指数C": "人工智能",
        "天弘科创芯片设计主题ETF发起联接C": "科创芯片设计",
        "富国全球科技互联网股票(QDII)C": "海外基金",
        "天弘全球高端制造混合(QDII)C": "全球高端制造",
        "广发全球精选股票(QDII)人民币C": "全球精选股票",
    }

    for fund_name, expected in cases.items():
        candidate = infer_semantic_sector_from_fund_name(fund_name)
        assert candidate is not None, fund_name
        assert candidate.sector_name == expected
        assert candidate.source == "semantic_name"
        assert candidate.confidence >= 0.55


def test_semantic_sector_ignores_generic_product_words():
    from app.services.sector_labels import infer_semantic_sector_from_fund_name

    for fund_name in (
        "某某灵活配置混合C",
        "某某成长精选股票A",
        "某某稳健回报混合C",
    ):
        assert infer_semantic_sector_from_fund_name(fund_name) is None


def test_legacy_name_infer_keeps_existing_keyword_behavior():
    from app.services.sector_labels import infer_sector_label_from_fund_name

    assert infer_sector_label_from_fund_name("某某国防军工混合C") == "国防军工"
    assert infer_sector_label_from_fund_name("某某CPO主题股票A") == "CPO"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
cd apps\api
python -m pytest tests/test_fund_sector_auto_match.py::test_semantic_sector_from_fund_name_matches_competitor_examples tests/test_fund_sector_auto_match.py::test_semantic_sector_ignores_generic_product_words tests/test_fund_sector_auto_match.py::test_legacy_name_infer_keeps_existing_keyword_behavior -q
```

Expected: FAIL because `infer_semantic_sector_from_fund_name` does not exist.

- [ ] **Step 3: Implement semantic resolver**

In `apps/api/app/services/sector_labels.py`, add the dataclass and helpers below. Keep existing imports and add `dataclasses` and `THEME_BOARD_INDEX` imports.

```python
from dataclasses import dataclass
from app.services.sector_registry_data import THEME_BOARD_INDEX


@dataclass(frozen=True)
class SemanticSectorCandidate:
    sector_name: str
    source: str = "semantic_name"
    confidence: float = 0.0
    reason: str = ""
    quote_key: str | None = None


_FUND_COMPANY_PREFIXES: tuple[str, ...] = (
    "华夏", "中欧", "天弘", "富国", "广发", "中航", "易方达", "招商", "南方", "嘉实",
    "博时", "鹏华", "汇添富", "工银瑞信", "银华", "国泰", "景顺长城", "兴全", "兴证全球",
)

_PRODUCT_TOKENS: tuple[str, ...] = (
    "发起式", "主题", "指数增强", "指数", "交易型开放式", "开放式", "ETF", "LOF",
    "联接", "连接", "混合型", "混合", "股票型", "债券型", "债券", "基金",
    "人民币", "美元", "QDII", "FOF",
)

_GENERIC_THEME_WORDS: frozenset[str] = frozenset({
    "精选", "成长", "稳健", "回报", "价值", "优势", "灵活配置", "配置", "量化",
})

_OVERSEAS_TECH_WORDS: tuple[str, ...] = ("科技互联网", "科技先锋", "科技")
_UNKNOWN_COMPANY_PREFIXES: tuple[str, ...] = ("某某", "测试")


def _strip_fund_company_prefix(name: str) -> str:
    for prefix in sorted(_FUND_COMPANY_PREFIXES, key=len, reverse=True):
        if name.startswith(prefix) and len(name) > len(prefix) + 1:
            return name[len(prefix):]
    return name


def _clean_fund_name_for_semantic(name: str) -> str:
    cleaned = normalize_sector_label(name.replace("...", ""))
    cleaned = re.sub(r"[（）()]", "", cleaned)
    cleaned = _strip_fund_company_prefix(cleaned)
    for token in sorted(_PRODUCT_TOKENS, key=len, reverse=True):
        cleaned = cleaned.replace(token, "")
    cleaned = re.sub(r"[A-H]$", "", cleaned, flags=re.IGNORECASE)
    return normalize_sector_label(cleaned)


def _is_generic_semantic_phrase(phrase: str) -> bool:
    if not phrase or len(phrase) < 2:
        return True
    if phrase in _GENERIC_THEME_WORDS:
        return True
    remaining = phrase
    for word in sorted(_GENERIC_THEME_WORDS, key=len, reverse=True):
        remaining = remaining.replace(word, "")
    for prefix in _UNKNOWN_COMPANY_PREFIXES:
        if remaining.startswith(prefix):
            remaining = remaining[len(prefix):]
    return len(remaining) <= 2


def _registry_labels() -> tuple[str, ...]:
    labels = set(THEME_BOARD_INDEX.keys())
    labels.update(_TOPIC_ALIASES)
    return tuple(sorted(labels, key=len, reverse=True))


def _match_registered_theme(cleaned: str) -> str | None:
    for label in _registry_labels():
        if label and label in cleaned:
            return label
    return None


def infer_semantic_sector_from_fund_name(fund_name: str | None) -> SemanticSectorCandidate | None:
    if not fund_name:
        return None
    normalized = normalize_sector_label(fund_name)
    if not normalized:
        return None

    cleaned = _clean_fund_name_for_semantic(normalized)
    if not cleaned or _is_generic_semantic_phrase(cleaned):
        return None

    is_qdii = "QDII" in normalized.upper() or "全球" in normalized or "海外" in normalized
    if is_qdii and any(word in cleaned for word in _OVERSEAS_TECH_WORDS):
        return SemanticSectorCandidate(
            sector_name="海外基金",
            confidence=0.72,
            reason="qdii_overseas_tech",
            quote_key=None,
        )

    registered = _match_registered_theme(cleaned)
    if registered:
        return SemanticSectorCandidate(
            sector_name=registered,
            confidence=0.82,
            reason="registered_theme_substring",
            quote_key=registered if registered in THEME_BOARD_INDEX else None,
        )

    if is_qdii and cleaned.startswith("全球") and len(cleaned) >= 4:
        return SemanticSectorCandidate(
            sector_name=cleaned,
            confidence=0.68,
            reason="qdii_global_theme_phrase",
            quote_key=None,
        )

    if len(cleaned) >= 4 and not _is_generic_semantic_phrase(cleaned):
        return SemanticSectorCandidate(
            sector_name=cleaned,
            confidence=0.6,
            reason="cleaned_theme_phrase",
            quote_key=cleaned if cleaned in THEME_BOARD_INDEX else None,
        )

    return None
```

Then update `infer_sector_label_from_fund_name` at the bottom of `sector_labels.py` so legacy callers benefit from the broader resolver:

```python
def infer_sector_label_from_fund_name(fund_name: str | None) -> str | None:
    """总览 OCR 无关联板块时，从基金名称推断主题短名。"""
    candidate = infer_semantic_sector_from_fund_name(fund_name)
    if candidate is not None and candidate.quote_key:
        return candidate.sector_name
    if not fund_name:
        return None
    normalized = normalize_sector_label(fund_name.replace("...", ""))
    if not normalized:
        return None
    for token in sorted(_TOPIC_ALIASES, key=len, reverse=True):
        if token in normalized:
            return token
    return None
```

- [ ] **Step 4: Run semantic tests to verify they pass**

Run:

```powershell
cd apps\api
python -m pytest tests/test_fund_sector_auto_match.py::test_semantic_sector_from_fund_name_matches_competitor_examples tests/test_fund_sector_auto_match.py::test_semantic_sector_ignores_generic_product_words tests/test_fund_sector_auto_match.py::test_legacy_name_infer_keeps_existing_keyword_behavior -q
```

Expected: 3 passed.

- [ ] **Step 5: Commit Task 1**

Run:

```powershell
git add apps/api/app/services/sector_labels.py apps/api/tests/test_fund_sector_auto_match.py
git commit -m "feat: add semantic sector name resolver"
```

---

### Task 2: Integrate Semantic Resolver Into Primary Sector Service

**Files:**
- Modify: `apps/api/app/services/fund_primary_sector_service.py`
- Modify: `apps/api/tests/test_fund_sector_auto_match.py`
- Modify: `apps/api/tests/test_holdings_fast_sector_resolution.py`

- [ ] **Step 1: Write failing primary-sector integration tests**

Append this test to `apps/api/tests/test_fund_sector_auto_match.py`:

```python
def test_resolve_primary_sector_uses_semantic_name_when_allowed(monkeypatch):
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_profile_by_code",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service._resolve_from_benchmark_index",
        lambda *_args, **_kwargs: None,
    )

    record = resolve_primary_sector(
        "999998",
        fund_name="天弘科创芯片设计主题ETF发起联接C",
        allow_name_infer=True,
        fetch_benchmark=False,
    )

    assert record is not None
    assert record.source == "semantic_name"
    assert record.sector_name == "科创芯片设计"
    assert record.confidence >= 0.55
```

Append this test to `apps/api/tests/test_holdings_fast_sector_resolution.py`:

```python
def test_refresh_benchmark_sectors_fast_mode_applies_semantic_name_without_fetch(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_profile_by_code",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_benchmark_sector.fetch_fund_benchmark_text",
        lambda code: calls.append(code) or None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.save_fund_primary_sector",
        lambda **_kwargs: None,
    )

    from app.services.fund_primary_sector_service import refresh_benchmark_sectors_for_holdings

    result = refresh_benchmark_sectors_for_holdings(
        [
            _holding(
                fund_code="026790",
                fund_name="中欧上证科创板人工智能指数C",
                sector_name=None,
                intraday_index_name=None,
            )
        ],
        fetch_missing_benchmark=False,
    )

    assert calls == []
    assert result[0].sector_name == "人工智能"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
cd apps\api
python -m pytest tests/test_fund_sector_auto_match.py::test_resolve_primary_sector_uses_semantic_name_when_allowed tests/test_holdings_fast_sector_resolution.py::test_refresh_benchmark_sectors_fast_mode_applies_semantic_name_without_fetch -q
```

Expected: FAIL because `resolve_primary_sector` still uses legacy `name_infer`, and fast refresh returns early when benchmark fetching is disabled.

- [ ] **Step 3: Add `semantic_name` source and resolver integration**

In `apps/api/app/services/fund_primary_sector_service.py`, change imports:

```python
from app.services.sector_labels import (
    infer_sector_label_from_fund_name,
    infer_semantic_sector_from_fund_name,
)
```

Update `_SOURCE_PRIORITY`:

```python
_SOURCE_PRIORITY = {
    "ocr_detail": 100,
    "manual": 85,
    "holdings_infer": 70,
    "benchmark_index": 65,
    "alipay_overview": 50,
    "semantic_name": 40,
    "name_infer": 10,
}
```

In `resolve_primary_sector`, replace the `if allow_name_infer and fund_name:` block with:

```python
    if allow_name_infer and fund_name:
        semantic = infer_semantic_sector_from_fund_name(fund_name)
        if semantic:
            return PrimarySectorRecord(
                fund_code=code,
                sector_name=semantic.sector_name,
                intraday_index_name=infer_intraday_index_from_fund_name(fund_name)
                if semantic.quote_key
                else None,
                source=semantic.source,
                confidence=semantic.confidence,
            )

        inferred = infer_sector_label_from_fund_name(fund_name)
        if inferred and get_canonical_sector(inferred):
            return PrimarySectorRecord(
                fund_code=code,
                sector_name=inferred,
                intraday_index_name=infer_intraday_index_from_fund_name(fund_name),
                source="name_infer",
                confidence=0.35,
            )
```

In `apply_primary_sector_to_holding`, change the `resolve_primary_sector` call to enable semantic fast inference:

```python
        record = resolve_primary_sector(
            code,
            fund_name=holding.fund_name,
            allow_name_infer=True,
            fetch_benchmark=fetch_benchmark,
        )
```

In `refresh_benchmark_sectors_for_holdings`, replace this early return:

```python
        if not fetch_missing_benchmark and not fetch_holdings_infer:
            refreshed.append(holding)
            continue
```

with:

```python
        if not fetch_missing_benchmark and not fetch_holdings_infer:
            refreshed.append(apply_primary_sector_to_holding(holding, fetch_benchmark=False))
            continue
```

- [ ] **Step 4: Run integration tests to verify they pass**

Run:

```powershell
cd apps\api
python -m pytest tests/test_fund_sector_auto_match.py::test_resolve_primary_sector_uses_semantic_name_when_allowed tests/test_holdings_fast_sector_resolution.py::test_refresh_benchmark_sectors_fast_mode_applies_semantic_name_without_fetch -q
```

Expected: 2 passed.

- [ ] **Step 5: Run existing sector auto-match tests**

Run:

```powershell
cd apps\api
python -m pytest tests/test_fund_sector_auto_match.py tests/test_holdings_fast_sector_resolution.py -q
```

Expected: all tests in both files pass.

- [ ] **Step 6: Commit Task 2**

Run:

```powershell
git add apps/api/app/services/fund_primary_sector_service.py apps/api/tests/test_fund_sector_auto_match.py apps/api/tests/test_holdings_fast_sector_resolution.py
git commit -m "feat: apply semantic sectors in fast resolution"
```

---

### Task 3: Prove OCR Fast Path Applies Semantic Sectors

**Files:**
- Modify: `apps/api/tests/test_apply_holdings_fast_path.py`
- Existing implementation target from Task 2: `apps/api/app/services/fund_primary_sector_service.py`

- [ ] **Step 1: Write failing OCR fast-path regression test**

Append this test to `apps/api/tests/test_apply_holdings_fast_path.py`:

```python
def test_apply_confirmed_holdings_applies_semantic_sector_without_benchmark_fetch(monkeypatch):
    benchmark_calls: list[str] = []

    monkeypatch.setattr(
        "app.services.ocr_pipeline._finalize_confirmed_holdings",
        lambda holdings, _service: holdings,
    )
    monkeypatch.setattr(
        "app.services.ocr_pipeline.save_portfolio_summary",
        lambda _summary: None,
    )
    monkeypatch.setattr(
        "app.services.ocr_pipeline.save_daily_snapshot",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_profile_by_code",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.save_fund_primary_sector",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.fund_benchmark_sector.fetch_fund_benchmark_text",
        lambda code: benchmark_calls.append(code) or None,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.bootstrap_holding_baselines",
        lambda holdings, **_kwargs: holdings,
    )
    monkeypatch.setattr(
        "app.services.portfolio_persistence.enrich_loaded_holdings",
        lambda holdings, **_kwargs: holdings,
    )
    monkeypatch.setattr(
        "app.services.ocr_pipeline.enrich_holdings_from_profiles",
        lambda holdings, **_kwargs: holdings,
    )
    monkeypatch.setattr(
        "app.services.ocr_pipeline.FundProfileService",
        lambda: type(
            "StubProfileService",
            (),
            {
                "sync_profiles_from_holdings": lambda self, holdings: type(
                    "SyncResult", (), {"model_dump": lambda self: {"updated": 0, "created": 0}}
                )(),
            },
        )(),
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.refresh_holdings_sector_quotes",
        lambda holdings, **kwargs: {
            "ok": True,
            "holdings": [holding.model_dump(mode="json") for holding in holdings],
            "summary": {"matched": 0},
        },
    )

    result = apply_confirmed_holdings(
        [
            Holding(
                fund_code="021277",
                fund_name="广发全球精选股票(QDII)人民币C",
                holding_amount=100.0,
            )
        ]
    )

    assert benchmark_calls == []
    assert result["holdings"][0]["sector_name"] == "全球精选股票"
```

- [ ] **Step 2: Run OCR regression test**

Run:

```powershell
cd apps\api
python -m pytest tests/test_apply_holdings_fast_path.py::test_apply_confirmed_holdings_applies_semantic_sector_without_benchmark_fetch -q
```

Expected: PASS after Task 2. If it fails because later enrichment strips `sector_name`, inspect `apply_confirmed_holdings` and ensure semantic fields are applied after profile enrichment as well as before quote refresh.

- [ ] **Step 3: Run full targeted API regression**

Run:

```powershell
cd apps\api
python -m pytest tests/test_fund_sector_auto_match.py tests/test_holdings_fast_sector_resolution.py tests/test_apply_holdings_fast_path.py tests/test_fund_benchmark_sector.py tests/test_portfolio_sector_refresh.py -q
```

Expected: all targeted tests pass.

- [ ] **Step 4: Run API type/import smoke check**

Run:

```powershell
cd apps\api
python -m compileall app/services/sector_labels.py app/services/fund_primary_sector_service.py
```

Expected: both files compile successfully.

- [ ] **Step 5: Commit Task 3**

Run:

```powershell
git add apps/api/tests/test_apply_holdings_fast_path.py
git commit -m "test: cover OCR semantic sector fast path"
```

---

## Final Verification

- [ ] **Run final focused tests**

```powershell
cd apps\api
python -m pytest tests/test_fund_sector_auto_match.py tests/test_holdings_fast_sector_resolution.py tests/test_apply_holdings_fast_path.py tests/test_fund_benchmark_sector.py tests/test_portfolio_sector_refresh.py -q
```

Expected: all targeted tests pass.

- [ ] **Run compile check**

```powershell
cd apps\api
python -m compileall app/services/sector_labels.py app/services/fund_primary_sector_service.py
```

Expected: compile succeeds.

- [ ] **Manual probe for screenshot examples**

Run:

```powershell
cd apps\api
@'
from app.services.sector_labels import infer_semantic_sector_from_fund_name

names = [
    "华夏中证电网设备主题ETF发起式联接C",
    "中欧上证科创板人工智能指数C",
    "天弘科创芯片设计主题ETF发起联接C",
    "富国全球科技互联网股票(QDII)C",
    "天弘全球高端制造混合(QDII)C",
    "广发全球精选股票(QDII)人民币C",
]
for name in names:
    candidate = infer_semantic_sector_from_fund_name(name)
    print(name, "=>", candidate.sector_name if candidate else None)
'@ | python -
```

Expected output:

```text
华夏中证电网设备主题ETF发起式联接C => 电网设备
中欧上证科创板人工智能指数C => 人工智能
天弘科创芯片设计主题ETF发起联接C => 科创芯片设计
富国全球科技互联网股票(QDII)C => 海外基金
天弘全球高端制造混合(QDII)C => 全球高端制造
广发全球精选股票(QDII)人民币C => 全球精选股票
```

---

## Self-Review Notes

- Spec coverage: The plan covers computation-based semantic extraction, QDII competitor alignment, OCR fast-path speed, no benchmark fetch in fast mode, and preservation of existing higher-priority sources.
- Placeholder scan: No placeholder markers; each task has concrete tests, code locations, commands, and expected results.
- Type consistency: `SemanticSectorCandidate.source` is `"semantic_name"`, matching `_SOURCE_PRIORITY` and expected tests. Confidence is a float in the same style as existing `PrimarySectorRecord` usage.
