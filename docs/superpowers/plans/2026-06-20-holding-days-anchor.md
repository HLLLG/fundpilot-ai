# 持有天数锚点修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every newly-recorded holding a stable "first seen" date anchor so 持有天数 increments daily, including funds added via the Alipay holdings list.

**Architecture:** Add a `first_seen_date` field to `FundProfile` (stored in the existing JSON payload, no DB migration). Stamp it once at first persistence in `FundProfileService.save_profile`. Resolve holding days from `first_purchase_date` → `first_seen_date` → legacy OCR aging → snapshot, recomputed against `today` each request.

**Tech Stack:** Python 3 / FastAPI / Pydantic v2 backend, pytest; Next.js/React/TypeScript frontend.

---

### Task 1: Add `first_seen_date` to the FundProfile model

**Files:**
- Modify: `apps/api/app/models.py` (FundProfile, ~line 150)

- [ ] **Step 1: Add the field**

In `apps/api/app/models.py`, in the `FundProfile` class, add `first_seen_date` right after `first_purchase_date`:

```python
    holding_days: int | None = None
    holding_days_as_of: str | None = None
    first_purchase_date: str | None = None
    first_seen_date: str | None = None
    sector_name: str | None = None
```

- [ ] **Step 2: Verify it imports**

Run: `apps/api/.venv/Scripts/python.exe -c "from app.models import FundProfile; print(FundProfile(fund_code='000001', fund_name='x').first_seen_date)"`
(run from `apps/api`)
Expected: prints `None`

- [ ] **Step 3: Commit**

```bash
git add apps/api/app/models.py
git commit -m "feat: add first_seen_date anchor field to FundProfile"
```

---

### Task 2: Anchor resolver + stamp on first persistence

**Files:**
- Modify: `apps/api/app/services/fund_profile.py` (imports, `merge_detail_profile`, `save_profile`)
- Test: `apps/api/tests/test_fund_profile.py`

- [ ] **Step 1: Write the failing test**

Add to `apps/api/tests/test_fund_profile.py` (top of file already imports `FundProfile`/service; if not, add `from datetime import date, timedelta`, `from app.models import FundProfile`, `from app.services.fund_profile import FundProfileService, resolve_first_seen_anchor`):

```python
def test_resolve_first_seen_anchor_prefers_purchase_date():
    profile = FundProfile(fund_code="000001", fund_name="A", first_purchase_date="2020-01-01")
    assert resolve_first_seen_anchor(profile, today=date(2026, 6, 20)) == "2020-01-01"


def test_resolve_first_seen_anchor_backdates_from_ocr_holding_days():
    profile = FundProfile(fund_code="000001", fund_name="A", holding_days=30)
    assert resolve_first_seen_anchor(profile, today=date(2026, 6, 20)) == "2026-05-21"


def test_resolve_first_seen_anchor_defaults_to_today():
    profile = FundProfile(fund_code="000001", fund_name="A")
    assert resolve_first_seen_anchor(profile, today=date(2026, 6, 20)) == "2026-06-20"


def test_save_profile_stamps_first_seen_for_new_profile():
    service = FundProfileService()
    saved = service.save_profile(FundProfile(fund_code="000002", fund_name="新基金"))
    assert saved.first_seen_date == date.today().isoformat()


def test_save_profile_keeps_existing_first_seen_on_reupload():
    service = FundProfileService()
    service.save_profile(
        FundProfile(fund_code="000003", fund_name="老基金", first_seen_date="2025-01-01")
    )
    again = service.save_profile(FundProfile(fund_code="000003", fund_name="老基金"))
    assert again.first_seen_date == "2025-01-01"
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `apps/api`): `.venv/Scripts/python.exe -m pytest tests/test_fund_profile.py -k first_seen -q`
Expected: FAIL — `ImportError: cannot import name 'resolve_first_seen_anchor'`

- [ ] **Step 3: Add `timedelta` import**

In `apps/api/app/services/fund_profile.py`, ensure the datetime import includes `timedelta`. Find the existing `from datetime import ...` line and make it:

```python
from datetime import date, timedelta
```

(If the existing import is `from datetime import date`, just add `, timedelta`.)

- [ ] **Step 4: Add the anchor resolver**

In `apps/api/app/services/fund_profile.py`, add this module-level function (place it just above `def merge_detail_profile(`):

```python
def resolve_first_seen_anchor(profile: FundProfile, *, today: date | None = None) -> str:
    """首次录入持有时的稳定锚点日期：用户购入日 > OCR 持有天数回推 > 今天。"""
    today = today or date.today()
    if profile.first_purchase_date:
        return profile.first_purchase_date
    if profile.holding_days is not None and profile.holding_days >= 0:
        return (today - timedelta(days=profile.holding_days)).isoformat()
    return today.isoformat()
```

- [ ] **Step 5: Carry `first_seen_date` through `merge_detail_profile`**

In `merge_detail_profile`, inside the `return incoming.model_copy(update={...})` dict, add this entry alongside `first_purchase_date`:

```python
            "first_purchase_date": existing.first_purchase_date,
            "first_seen_date": existing.first_seen_date or incoming.first_seen_date,
```

- [ ] **Step 6: Stamp the anchor in `save_profile`**

In `FundProfileService.save_profile`, after the line `profile = merge_detail_profile(existing, profile)` and the `if profile.source == "yangjibao-detail":` block, but **before** `saved = save_fund_profile(profile)`, insert:

```python
        if existing is None and not profile.first_seen_date:
            profile = profile.model_copy(
                update={"first_seen_date": resolve_first_seen_anchor(profile)}
            )
```

- [ ] **Step 7: Run tests to verify they pass**

Run (from `apps/api`): `.venv/Scripts/python.exe -m pytest tests/test_fund_profile.py -k "first_seen or anchor" -q`
Expected: PASS (5 tests)

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/services/fund_profile.py apps/api/tests/test_fund_profile.py
git commit -m "feat: stamp first_seen_date anchor when a holding is first recorded"
```

---

### Task 3: Resolve holding days from the anchor

**Files:**
- Modify: `apps/api/app/services/holding_detail_service.py` (`_resolve_holding_days`, ~line 156)
- Test: `apps/api/tests/test_holding_detail_service.py`

- [ ] **Step 1: Write the failing tests**

Add to `apps/api/tests/test_holding_detail_service.py` (ensure `from datetime import date, timedelta` and `from app.services.holding_detail_service import _resolve_holding_days` are imported; the file already imports `FundProfile`, `Holding`):

```python
def _holding(code="600000"):
    return Holding(fund_code=code, fund_name="测试基金", holding_amount=1000.0)


def test_resolve_holding_days_from_first_seen_increments():
    anchor = (date.today() - timedelta(days=12)).isoformat()
    profile = FundProfile(fund_code="600000", fund_name="测试基金", first_seen_date=anchor)
    days, source = _resolve_holding_days(profile, _holding())
    assert days == 12
    assert source == "first_seen"


def test_resolve_holding_days_user_date_beats_first_seen():
    profile = FundProfile(
        fund_code="600000",
        fund_name="测试基金",
        first_purchase_date=(date.today() - timedelta(days=100)).isoformat(),
        first_seen_date=(date.today() - timedelta(days=5)).isoformat(),
    )
    days, source = _resolve_holding_days(profile, _holding())
    assert days == 100
    assert source == "user"


def test_resolve_holding_days_no_anchor_returns_none():
    profile = FundProfile(fund_code="600000", fund_name="测试基金")
    days, source = _resolve_holding_days(profile, _holding())
    assert days is None
    assert source is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `apps/api`): `.venv/Scripts/python.exe -m pytest tests/test_holding_detail_service.py -k "first_seen or user_date or no_anchor" -q`
Expected: FAIL — `test_resolve_holding_days_from_first_seen_increments` returns `(None, None)` instead of `(12, "first_seen")`

- [ ] **Step 3: Insert the first_seen branch**

In `apps/api/app/services/holding_detail_service.py`, in `_resolve_holding_days`, add the `first_seen_date` branch immediately after the existing `first_purchase_date` block and before `snapshot_days = ...`:

```python
    if profile and profile.first_purchase_date:
        try:
            purchase = date.fromisoformat(profile.first_purchase_date)
            return max(0, (date.today() - purchase).days), "user"
        except ValueError:
            pass

    if profile and profile.first_seen_date:
        try:
            seen = date.fromisoformat(profile.first_seen_date)
            return max(0, (date.today() - seen).days), "first_seen"
        except ValueError:
            pass

    snapshot_days = _holding_days_from_snapshots(holding)
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `apps/api`): `.venv/Scripts/python.exe -m pytest tests/test_holding_detail_service.py -k "first_seen or user_date or no_anchor" -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/holding_detail_service.py apps/api/tests/test_holding_detail_service.py
git commit -m "feat: resolve holding days from first_seen_date anchor"
```

---

### Task 4: Frontend source-hint label

**Files:**
- Modify: `apps/web/src/components/YangjibaoFundDetail.tsx` (`PROVENANCE_LABEL`, ~line 53)

- [ ] **Step 1: Add the label**

In `apps/web/src/components/YangjibaoFundDetail.tsx`, add `first_seen` to the `PROVENANCE_LABEL` map:

```typescript
const PROVENANCE_LABEL: Record<string, string> = {
  ocr_detail: "详情 OCR",
  first_seen: "按首次记录日",
  nav: "净值推算",
  snapshot: "历史快照",
  computed: "公式估算",
  profile: "自动维护",
};
```

(Match the exact existing keys/order in the file; only add the `first_seen` line.)

- [ ] **Step 2: Typecheck**

Run (from `apps/web`): `npm run typecheck`
Expected: PASS, no errors

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/components/YangjibaoFundDetail.tsx
git commit -m "feat: label first_seen holding-days source in fund detail"
```

---

### Task 5: Full verification

- [ ] **Step 1: Run the backend test suite**

Run (from `apps/api`): `.venv/Scripts/python.exe -m pytest tests -q`
Expected: all tests pass (no regressions in OCR/holding/profile tests)

- [ ] **Step 2: Frontend lint + typecheck**

Run (from `apps/web`): `npm run lint` then `npm run typecheck`
Expected: PASS

- [ ] **Step 3: Final commit (if any cleanup)**

```bash
git add -A
git commit -m "chore: Phase 1 holding-days anchor verification"
```

(Skip if nothing changed.)

---

## Notes for the implementer

- `fund_profiles` stores the whole `FundProfile` as JSON; the new field needs **no** SQL migration.
- `FundProfileService.save_profile` is the only persistence path for overview OCR, detail OCR, Alipay-list OCR, and `apply-holdings` — stamping there covers all entry points.
- Do **not** backfill `first_seen_date = today` for profiles read without an anchor; long-held funds must keep showing `—` rather than resetting to 0. The resolver returning `(None, None)` for anchorless profiles (Task 3 Step 3) is the intended behavior.
- Tests force SQLite (`conftest.py`); run pytest from `apps/api` so the `.venv` and `app` package resolve.
