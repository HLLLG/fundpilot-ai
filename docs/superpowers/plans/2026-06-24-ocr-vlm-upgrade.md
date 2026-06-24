# 截图识别升级（VLM + 本地解析器修复）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让「新增持有」截图识别又快(<5s)又准(QDII/截不全全中)又稳(不再 `failed to fetch`)：主路走云端 `qwen3-vl-flash` 出结构化 JSON，本地 PaddleOCR 解析器修好后作自动回退。

**Architecture:** 新增 `HoldingsExtractor` 抽象层（`vlm` 主 + `local` 回退，`auto` 策略软回退）插入 `run_ocr_upload_pipeline`，下游查码/补档案/preview 不变。本地解析器修两个 bug（`is_near_zero(None)` 崩溃、QDII 名识别失败）作为可靠兜底。

**Tech Stack:** Python / FastAPI / Pydantic v2 / httpx（复用现有依赖，无新增）/ PaddleOCR（保留）/ 阿里云百炼 DashScope OpenAI 兼容端点 / pytest。

设计依据：`docs/superpowers/specs/2026-06-24-ocr-vlm-upgrade-design.md`

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `apps/api/app/services/ocr_text_utils.py` | 修 `is_near_zero` None 安全（被 `infer_holding_profit` 等复用） |
| `apps/api/app/services/alipay_holdings_parser.py` | 修 `_extract_my_holdings_metrics` None 守卫；QDII 名正则；噪声行过滤 |
| `apps/api/app/services/ocr_parser.py` | `parse_holdings_from_text` try/except 兜底，永不抛 |
| `apps/api/app/services/vlm_holdings_provider.py` | **新增** 图片→qwen3-vl-flash→`list[Holding]`（可注入 completion 便于测试） |
| `apps/api/app/services/holdings_extractor.py` | **新增** provider 选择 + 软回退；返回 `ExtractionResult` |
| `apps/api/app/services/ocr_pipeline.py` | 接入 extractor 替换直连 OCR+parse；响应加 `extraction_provider` |
| `apps/api/app/config.py` | 新增 5 个 VLM 配置项 |
| `.env.example` | 文档化新配置 |
| `apps/api/scripts/smoke_vlm_ocr.py` | **新增** 一次性 live 验证脚本（手动跑，需 key） |
| `docs/PROJECT_CONTEXT.md` / `README.md` | 隐私边界 + 能力清单更新 |
| `apps/api/tests/fixtures/alipay_overview_qdii_top_ocr.txt` | **已生成**（真实 image1 OCR，6 只含 3 QDII） |
| `apps/api/tests/fixtures/alipay_overview_qdii_bottom_ocr.txt` | **已生成**（真实 image2 OCR，2 只 + 余额宝/footer） |
| `apps/api/tests/test_alipay_holdings_parser.py` | 解析器修复回归（新增/补充） |
| `apps/api/tests/test_vlm_holdings_provider.py` | **新增** VLM provider 单测（mock） |
| `apps/api/tests/test_holdings_extractor.py` | **新增** selector/回退单测 |

测试命令（本仓库）：`cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/<file> -v`

---

> **状态：已完成并推送 — commit 6146bf6，2026-06-24**

## Task 1: 本地解析器崩溃修复（永不抛异常）

**Files:**
- Modify: `apps/api/app/services/ocr_text_utils.py` (`is_near_zero`, line 86-87)
- Modify: `apps/api/app/services/alipay_holdings_parser.py` (`_extract_my_holdings_metrics`, line ~286-296)
- Modify: `apps/api/app/services/ocr_parser.py` (`parse_holdings_from_text`, line 25-29)
- Test: `apps/api/tests/test_alipay_holdings_parser.py`

- [ ] **Step 1: Write failing tests**

新建/追加 `apps/api/tests/test_alipay_holdings_parser.py`：

```python
from pathlib import Path

from app.services.alipay_holdings_parser import _extract_my_holdings_metrics
from app.services.ocr_parser import parse_holdings_from_text

FIXTURES = Path(__file__).parent / "fixtures"


def test_extract_metrics_no_crash_when_profit_missing():
    # 复现 image2 崩溃路径：只有金额、无收益数字、percent 行无内联数字
    amount, yesterday, profit = _extract_my_holdings_metrics(
        ["1000.00"],
        percent_line="0.00%",
        percent_pending_negative=False,
    )
    assert amount == 1000.00
    assert profit is None


def test_parse_bottom_fixture_does_not_raise():
    text = (FIXTURES / "alipay_overview_qdii_bottom_ocr.txt").read_text(encoding="utf-8")
    holdings = parse_holdings_from_text(text)  # 之前抛 TypeError
    assert isinstance(holdings, list)
```

- [ ] **Step 2: Run to verify fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_alipay_holdings_parser.py -v`
Expected: FAIL — `TypeError: bad operand type for abs(): 'NoneType'`

- [ ] **Step 3: Fix `is_near_zero` to be None-safe**

`ocr_text_utils.py` 替换：

```python
def is_near_zero(value: float | None) -> bool:
    if value is None:
        return False
    return abs(value) < 0.0001
```

- [ ] **Step 4: Guard the call site + never-raise wrapper**

`alipay_holdings_parser.py`，`_extract_my_holdings_metrics` 末尾分支（原 line ~286-296）替换为：

```python
    if holding_profit is None and inline_profit_numbers:
        for value in inline_profit_numbers:
            if not is_near_zero(value) and value != holding_amount:
                holding_profit = value
                break
    elif holding_profit is not None and is_near_zero(holding_profit) and inline_profit_numbers:
        for value in inline_profit_numbers:
            if not is_near_zero(value) and value != holding_amount:
                holding_profit = value
                break
```

`ocr_parser.py` `parse_holdings_from_text` 替换为（兜底，端点永不 500）：

```python
def parse_holdings_from_text(text: str) -> list[Holding]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not is_alipay_holdings_page(lines):
        return []
    try:
        return parse_alipay_holdings_page(text)
    except Exception:  # noqa: BLE001 — 解析异常不应让 /api/ocr 500
        return []
```

- [ ] **Step 5: Run to verify pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_alipay_holdings_parser.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/services/ocr_text_utils.py apps/api/app/services/alipay_holdings_parser.py apps/api/app/services/ocr_parser.py apps/api/tests/test_alipay_holdings_parser.py apps/api/tests/fixtures/alipay_overview_qdii_bottom_ocr.txt
git commit -m "fix(ocr): guard is_near_zero(None) crash so partial alipay screenshots don't 500"
```

---

## Task 2: QDII 基金名识别 + 噪声行过滤

**Files:**
- Modify: `apps/api/app/services/alipay_holdings_parser.py` (`COMPLETE_FUND_NAME_RE` line 67-71；`ALIPAY_NOISE_MARKERS`/`ALIPAY_FOOTER_MARKERS` line 42-59)
- Modify: `apps/api/app/services/fund_name_utils.py` (`looks_like_fund_product_name`)
- Test: `apps/api/tests/test_alipay_holdings_parser.py`

- [ ] **Step 1: Write failing tests**

追加到 `apps/api/tests/test_alipay_holdings_parser.py`：

```python
from app.services.alipay_holdings_parser import COMPLETE_FUND_NAME_RE
from app.services.fund_name_utils import looks_like_fund_product_name


def test_qdii_names_recognized():
    qdii_names = [
        "天弘全球高端制造混合（QDII）C",
        "天弘全球高端制造混合(QDII)C",
        "富国全球科技互联网股票（QDII)C",
        "广发全球精选股票(QDII)C",
        "华夏全球科技先锋混合（QDII)C",
    ]
    for name in qdii_names:
        assert COMPLETE_FUND_NAME_RE.match(name), name
        assert looks_like_fund_product_name(name), name


def test_parse_top_fixture_recovers_all_six_funds():
    text = (FIXTURES / "alipay_overview_qdii_top_ocr.txt").read_text(encoding="utf-8")
    holdings = parse_holdings_from_text(text)
    names = [h.fund_name for h in holdings]
    assert len(holdings) == 6, names
    assert "天弘全球高端制造混合（QDII）C" in names
    assert "广发全球精选股票（QDII)C" in names
    grid = next(h for h in holdings if "电网设备" in h.fund_name)
    assert grid.holding_amount == 2000.01


def test_parse_bottom_fixture_two_funds_skips_yuebao():
    text = (FIXTURES / "alipay_overview_qdii_bottom_ocr.txt").read_text(encoding="utf-8")
    holdings = parse_holdings_from_text(text)
    names = [h.fund_name for h in holdings]
    assert len(holdings) == 2, names
    assert any("华夏全球科技先锋" in n for n in names)
    assert any("中航机遇领航" in n for n in names)
    assert all("余额" not in n for n in names)
```

- [ ] **Step 2: Run to verify fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_alipay_holdings_parser.py -k "qdii or six or yuebao" -v`
Expected: FAIL — QDII 不匹配；top 仅 3 只

- [ ] **Step 3: Extend the fund-name regex (QDII infix)**

`alipay_holdings_parser.py` 替换 `COMPLETE_FUND_NAME_RE`（line 67-71）：

```python
# 允许 混合/股票/指数/联接 与份额字母间出现 (QDII)/（QDII）/(QDII-ETF) 等括注
_QDII_INFIX = r"(?:[（(](?:QDII|LOF|FOF|QDII-ETF)[)）])?"
COMPLETE_FUND_NAME_RE = re.compile(
    r"^[\u4e00-\u9fffA-Za-z0-9·]{4,40}"
    r"(?:混合|联接|ETF联接|主题ETF联接|股票|指数)"
    + _QDII_INFIX
    + r"[A-CEH]$",
    re.IGNORECASE,
)
```

- [ ] **Step 4: Mirror the fix in `looks_like_fund_product_name`**

读 `fund_name_utils.py` 的 `looks_like_fund_product_name`，找到其判断「以 混合/股票/指数/联接 + 份额字母结尾」的正则/逻辑，套用同样的可选 `(QDII)` 括注。若该函数复用了某共享后缀正则，则改共享正则；保持其它分支不变。实现后必须使 `test_qdii_names_recognized` 通过。

- [ ] **Step 5: Add noise/footer markers (defense-in-depth)**

`alipay_holdings_parser.py` 在 `ALIPAY_FOOTER_MARKERS`（line 42）追加法律声明与货基行 marker：

```python
ALIPAY_FOOTER_MARKERS = (
    "基金市场",
    "上证指数",
    "新增持有",
    "批量",
    "本页面非任何法律文件",
    "该页面由蚂蚁财富",
    "以上按照持有收益排序",
)
```

并在 `ALIPAY_NOISE_MARKERS`（line 43-59）追加：

```python
    "余额宝",
    "余额",
    "灵活取用",
```

- [ ] **Step 6: Run to verify pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_alipay_holdings_parser.py -v`
Expected: PASS（全部，含 Task 1 用例）

- [ ] **Step 7: Run full parser/ocr suite for regressions**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_ocr_parser.py tests/test_alipay_transactions_parser.py tests/test_overview_pipeline.py -v`
Expected: PASS（不得回归既有支付宝/养基宝用例）

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/services/alipay_holdings_parser.py apps/api/app/services/fund_name_utils.py apps/api/tests/test_alipay_holdings_parser.py apps/api/tests/fixtures/alipay_overview_qdii_top_ocr.txt
git commit -m "fix(ocr): recognize QDII fund names so all alipay holdings are parsed"
```

---

## Task 3: VLM 配置项

**Files:**
- Modify: `apps/api/app/config.py` (Settings, after line 88 `ocr_max_image_side`)
- Modify: `.env.example`
- Test: `apps/api/tests/test_config.py`

- [ ] **Step 1: Write failing test**

追加到 `apps/api/tests/test_config.py`：

```python
def test_vlm_ocr_settings_defaults():
    from app.config import Settings

    s = Settings()
    assert s.ocr_provider == "auto"
    assert s.vlm_ocr_model == "qwen3-vl-flash"
    assert s.vlm_ocr_base_url.startswith("https://dashscope.aliyuncs.com")
    assert s.vlm_ocr_timeout_seconds == 20
    assert s.vlm_ocr_api_key is None
```

- [ ] **Step 2: Run to verify fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_config.py::test_vlm_ocr_settings_defaults -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'ocr_provider'`

- [ ] **Step 3: Add settings fields**

`config.py` 在 `ocr_max_image_side: int = 1280`（line 88）后插入：

```python
    # 截图识别引擎：auto（有 key 走云 VLM 否则本地）/ vlm（强制云，失败回退本地）/ local（强制本地不外传）
    ocr_provider: str = "auto"
    vlm_ocr_api_key: str | None = None
    vlm_ocr_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    vlm_ocr_model: str = "qwen3-vl-flash"
    vlm_ocr_timeout_seconds: float = 20.0
```

- [ ] **Step 4: Document in `.env.example`**

`.env.example` 追加：

```dotenv
# 截图识别引擎：auto=有 key 走云端 VLM 否则本地 PaddleOCR；vlm=强制云端(失败回退本地)；local=强制本地不外传截图
FUND_AI_OCR_PROVIDER=auto
# 阿里云百炼(DashScope) API Key；配置后 auto 模式即启用云端视觉识别（截图图片会发往该 API）
FUND_AI_VLM_OCR_API_KEY=
FUND_AI_VLM_OCR_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
FUND_AI_VLM_OCR_MODEL=qwen3-vl-flash
FUND_AI_VLM_OCR_TIMEOUT_SECONDS=20
```

- [ ] **Step 5: Run to verify pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_config.py::test_vlm_ocr_settings_defaults -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/config.py .env.example apps/api/tests/test_config.py
git commit -m "feat(ocr): add VLM OCR provider settings (qwen3-vl-flash via DashScope)"
```

---

## Task 4: VLM provider（图片 → 结构化 holdings）

**Files:**
- Create: `apps/api/app/services/vlm_holdings_provider.py`
- Test: `apps/api/tests/test_vlm_holdings_provider.py`

- [ ] **Step 1: Write failing tests**

`apps/api/tests/test_vlm_holdings_provider.py`：

```python
import pytest

from app.services.vlm_holdings_provider import (
    extract_holdings_via_vlm,
    parse_vlm_response,
)


def test_parse_vlm_response_plain_json():
    content = (
        '{"holdings":[{"fund_name":"天弘全球高端制造混合(QDII)C",'
        '"fund_code":null,"holding_amount":100.0,"daily_profit":0.0,'
        '"holding_profit":0.0,"holding_return_percent":0.0,"weight_percent":0.29}]}'
    )
    holdings = parse_vlm_response(content)
    assert len(holdings) == 1
    h = holdings[0]
    assert h.fund_name == "天弘全球高端制造混合(QDII)C"
    assert h.fund_code == "000000"  # 无码 → 占位，交给下游查码
    assert h.holding_amount == 100.0
    assert h.holding_return_percent == 0.0


def test_parse_vlm_response_fenced_and_prose():
    content = (
        "好的，识别结果如下：\n```json\n"
        '{"holdings":[{"fund_name":"中航机遇领航混合C","holding_amount":9626.7,'
        '"holding_profit":-373.3,"holding_return_percent":-3.73}]}\n```\n'
    )
    holdings = parse_vlm_response(content)
    assert len(holdings) == 1
    assert holdings[0].fund_name == "中航机遇领航混合C"
    assert holdings[0].holding_profit == -373.3


def test_parse_vlm_response_uses_six_digit_code_when_present():
    content = '{"holdings":[{"fund_name":"X混合C","fund_code":"026790","holding_amount":1000.0}]}'
    holdings = parse_vlm_response(content)
    assert holdings[0].fund_code == "026790"


def test_parse_vlm_response_malformed_raises():
    with pytest.raises(ValueError):
        parse_vlm_response("抱歉我无法识别这张图片")


def test_extract_holdings_via_vlm_injects_completion():
    captured = {}

    def fake_completion(messages, settings):
        captured["messages"] = messages
        return '{"holdings":[{"fund_name":"某基金C","holding_amount":500.0}]}'

    holdings = extract_holdings_via_vlm(b"\x89PNG_fake", completion=fake_completion)
    assert holdings[0].fund_name == "某基金C"
    # 图片以 base64 data URL 形式进入 messages
    blob = str(captured["messages"])
    assert "image_url" in blob and "base64" in blob
```

- [ ] **Step 2: Run to verify fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_vlm_holdings_provider.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement the provider**

`apps/api/app/services/vlm_holdings_provider.py`：

```python
from __future__ import annotations

import base64
import json
import re
from typing import Callable

import httpx

from app.config import Settings, get_settings
from app.models import Holding

VLM_EXTRACTION_PROMPT = (
    "你是基金持仓截图识别器。请从这张支付宝/养基宝「持有」截图中提取每一只**基金**的持仓。"
    "只输出 JSON，不要任何解释。格式："
    '{"holdings":[{"fund_name":"完整基金名","fund_code":"6位代码或null",'
    '"holding_amount":金额数字,"daily_profit":日收益或null,'
    '"holding_profit":持有收益或null,"cumulative_profit":累计收益或null,'
    '"holding_return_percent":持有收益率数字或null,"weight_percent":占比数字或null}]} 。'
    "规则：1)只提取带「基金」标签的行；跳过『余额宝/余额/现金/灵活取用』等货币基金行与底部法律声明、页眉/Tab。"
    "2)保留完整基金名（含 (QDII)、ETF联接、份额字母 A/B/C 等），不要拆词或翻译。"
    "3)亏损金额/收益率保留负号；看不到的字段填 null，不要编造。"
    "4)截图可能截不全（无表头或只有底部），仍尽量提取所有可见基金行。"
)

CompletionFn = Callable[[list[dict], Settings], str]

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _image_data_url(image_bytes: bytes) -> str:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:image/png;base64,{b64}"


def build_vlm_messages(image_bytes: bytes) -> list[dict]:
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": VLM_EXTRACTION_PROMPT},
                {"type": "image_url", "image_url": {"url": _image_data_url(image_bytes)}},
            ],
        }
    ]


def parse_vlm_response(content: str) -> list[Holding]:
    if not content or not content.strip():
        raise ValueError("VLM 返回为空")
    text = content.strip()
    match = _JSON_OBJECT_RE.search(text)
    if not match:
        raise ValueError(f"VLM 返回非 JSON：{text[:120]}")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ValueError(f"VLM JSON 解析失败：{exc}") from exc

    rows = data.get("holdings") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("VLM 返回缺少 holdings 数组")

    holdings: list[Holding] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("fund_name") or "").strip()
        amount = row.get("holding_amount")
        if not name or amount is None:
            continue
        code = str(row.get("fund_code") or "").strip()
        if not re.fullmatch(r"\d{6}", code):
            code = "000000"
        ret = row.get("holding_return_percent")
        holdings.append(
            Holding(
                fund_code=code,
                fund_name=name,
                holding_amount=float(amount),
                return_percent=float(ret) if ret is not None else 0,
                daily_profit=_opt_float(row.get("daily_profit")),
                holding_profit=_opt_float(row.get("holding_profit")),
                holding_return_percent=_opt_float(ret),
            )
        )
    if not holdings:
        raise ValueError("VLM 未提取到任何基金持仓")
    return holdings


def _opt_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _dashscope_completion(messages: list[dict], settings: Settings) -> str:
    if not settings.vlm_ocr_api_key:
        raise RuntimeError("VLM OCR API key 未配置")
    url = f"{settings.vlm_ocr_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.vlm_ocr_api_key}",
        "Content-Type": "application/json",
    }
    payload = {"model": settings.vlm_ocr_model, "messages": messages}
    timeout = httpx.Timeout(
        connect=10, read=settings.vlm_ocr_timeout_seconds, write=30, pool=10
    )
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    return data["choices"][0]["message"]["content"]


def extract_holdings_via_vlm(
    image_bytes: bytes,
    *,
    settings: Settings | None = None,
    completion: CompletionFn | None = None,
) -> list[Holding]:
    resolved = settings or get_settings()
    run = completion or _dashscope_completion
    messages = build_vlm_messages(image_bytes)
    content = run(messages, resolved)
    return parse_vlm_response(content)
```

- [ ] **Step 4: Run to verify pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_vlm_holdings_provider.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/vlm_holdings_provider.py apps/api/tests/test_vlm_holdings_provider.py
git commit -m "feat(ocr): add qwen3-vl-flash VLM holdings provider (image -> structured JSON)"
```

---

## Task 5: HoldingsExtractor（选择策略 + 软回退）

**Files:**
- Create: `apps/api/app/services/holdings_extractor.py`
- Test: `apps/api/tests/test_holdings_extractor.py`

- [ ] **Step 1: Write failing tests**

`apps/api/tests/test_holdings_extractor.py`：

```python
from app.config import Settings
from app.models import Holding
from app.services.holdings_extractor import extract_holdings


def _holding(name: str) -> list[Holding]:
    return [Holding(fund_code="000000", fund_name=name, holding_amount=100.0)]


def test_auto_uses_vlm_when_key_present():
    s = Settings(ocr_provider="auto", vlm_ocr_api_key="sk-test")
    result = extract_holdings(
        file_bytes=b"img",
        text="",
        settings=s,
        vlm_fn=lambda b, settings: _holding("VLM基金C"),
        local_fn=lambda b, t: (_holding("本地基金C"), "raw"),
    )
    assert result.provider == "vlm"
    assert result.holdings[0].fund_name == "VLM基金C"


def test_auto_falls_back_to_local_on_vlm_error():
    s = Settings(ocr_provider="auto", vlm_ocr_api_key="sk-test")

    def boom(b, settings):
        raise RuntimeError("vlm down")

    result = extract_holdings(
        file_bytes=b"img",
        text="",
        settings=s,
        vlm_fn=boom,
        local_fn=lambda b, t: (_holding("本地基金C"), "raw"),
    )
    assert result.provider == "local"
    assert result.holdings[0].fund_name == "本地基金C"


def test_auto_uses_local_when_no_key():
    s = Settings(ocr_provider="auto", vlm_ocr_api_key=None)
    result = extract_holdings(
        file_bytes=b"img",
        text="",
        settings=s,
        vlm_fn=lambda b, settings: _holding("VLM基金C"),
        local_fn=lambda b, t: (_holding("本地基金C"), "raw"),
    )
    assert result.provider == "local"


def test_provider_local_forces_local_even_with_key():
    s = Settings(ocr_provider="local", vlm_ocr_api_key="sk-test")
    result = extract_holdings(
        file_bytes=b"img",
        text="",
        settings=s,
        vlm_fn=lambda b, settings: _holding("VLM基金C"),
        local_fn=lambda b, t: (_holding("本地基金C"), "raw"),
    )
    assert result.provider == "local"


def test_manual_text_uses_local_no_vlm():
    s = Settings(ocr_provider="auto", vlm_ocr_api_key="sk-test")
    called = {"vlm": False}

    def vlm(b, settings):
        called["vlm"] = True
        return _holding("VLM基金C")

    result = extract_holdings(
        file_bytes=None,
        text="某基金C\n1000.00\n0.00%",
        settings=s,
        vlm_fn=vlm,
        local_fn=lambda b, t: (_holding("本地基金C"), t),
    )
    assert called["vlm"] is False
    assert result.provider == "local"
```

- [ ] **Step 2: Run to verify fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_holdings_extractor.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement the extractor**

`apps/api/app/services/holdings_extractor.py`：

```python
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from app.config import Settings, get_settings
from app.models import Holding

logger = logging.getLogger(__name__)

VlmFn = Callable[[bytes, Settings], list[Holding]]
LocalFn = Callable[[bytes | None, str], tuple[list[Holding], str]]


@dataclass
class ExtractionResult:
    holdings: list[Holding] = field(default_factory=list)
    ocr_source: str = "unknown"
    raw_text: str = ""
    provider: str = "local"


def _default_local_fn(file_bytes: bytes | None, text: str) -> tuple[list[Holding], str]:
    from app.services.ocr_engine import OcrEngine
    from app.services.ocr_parser import parse_holdings_from_text

    raw_text = text
    if not raw_text and file_bytes is not None:
        from pathlib import Path
        from app.config import get_settings as _gs

        upload_dir = _gs().upload_dir
        upload_dir.mkdir(parents=True, exist_ok=True)
        tmp = upload_dir / "vlm-local-tmp.png"
        tmp.write_bytes(file_bytes)
        try:
            raw_text = OcrEngine().extract_text(tmp)
        finally:
            for p in (tmp, tmp.with_name(f"{tmp.stem}.ocr-prepared.jpg")):
                try:
                    p.unlink()
                except OSError:
                    pass
    return parse_holdings_from_text(raw_text), raw_text


def extract_holdings(
    *,
    file_bytes: bytes | None,
    text: str,
    settings: Settings | None = None,
    vlm_fn: VlmFn | None = None,
    local_fn: LocalFn | None = None,
) -> ExtractionResult:
    resolved = settings or get_settings()
    local = local_fn or _default_local_fn

    def run_local() -> ExtractionResult:
        holdings, raw_text = local(file_bytes, text)
        return ExtractionResult(
            holdings=holdings,
            ocr_source="alipay_holdings" if holdings else "unknown",
            raw_text=raw_text,
            provider="local",
        )

    # 手动文本 / 无图片 / 强制本地 / 无 key → 本地
    use_vlm = (
        file_bytes is not None
        and not text
        and resolved.ocr_provider in ("auto", "vlm")
        and bool(resolved.vlm_ocr_api_key)
    )
    if not use_vlm:
        return run_local()

    vlm = vlm_fn or _default_vlm_fn
    try:
        holdings = vlm(file_bytes, resolved)
        if not holdings:
            raise ValueError("VLM 返回空持仓")
        return ExtractionResult(
            holdings=holdings,
            ocr_source="alipay_holdings",
            raw_text="",
            provider="vlm",
        )
    except Exception:  # noqa: BLE001 — 云端失败软回退本地，绝不冒泡
        logger.warning("VLM 识别失败，回退本地 OCR", exc_info=True)
        return run_local()


def _default_vlm_fn(file_bytes: bytes, settings: Settings) -> list[Holding]:
    from app.services.vlm_holdings_provider import extract_holdings_via_vlm

    return extract_holdings_via_vlm(file_bytes, settings=settings)
```

- [ ] **Step 4: Run to verify pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_holdings_extractor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/holdings_extractor.py apps/api/tests/test_holdings_extractor.py
git commit -m "feat(ocr): add HoldingsExtractor with auto VLM->local soft fallback"
```

---

## Task 6: 接入 `run_ocr_upload_pipeline`

**Files:**
- Modify: `apps/api/app/services/ocr_pipeline.py` (line 51-179, 主要替换 62-99 段的 OCR/parse 与 ocr_source 推断)
- Test: `apps/api/tests/test_ocr_pipeline.py`

- [ ] **Step 1: Write failing test**

追加到 `apps/api/tests/test_ocr_pipeline.py`（注入假 extractor，避免真 OCR/网络）：

```python
def test_pipeline_reports_extraction_provider(monkeypatch):
    from app.models import Holding
    from app.services import ocr_pipeline
    from app.services.holdings_extractor import ExtractionResult

    def fake_extract(*, file_bytes, text, settings=None, vlm_fn=None, local_fn=None):
        return ExtractionResult(
            holdings=[Holding(fund_code="000000", fund_name="某基金C", holding_amount=100.0)],
            ocr_source="alipay_holdings",
            raw_text="某基金C",
            provider="vlm",
        )

    monkeypatch.setattr(ocr_pipeline, "extract_holdings", fake_extract)
    result = ocr_pipeline.run_ocr_upload_pipeline(
        file_bytes=b"img", filename="x.png", preview=True
    )
    assert result["extraction_provider"] == "vlm"
    assert result["holdings"]
    assert result["ocr_source"] == "alipay_holdings"
```

- [ ] **Step 2: Run to verify fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_ocr_pipeline.py::test_pipeline_reports_extraction_provider -v`
Expected: FAIL — `KeyError: 'extraction_provider'` 或 import 错误

- [ ] **Step 3: Wire the extractor into the pipeline**

`ocr_pipeline.py` 顶部加导入：

```python
from app.services.holdings_extractor import ExtractionResult, extract_holdings
```

将 `run_ocr_upload_pipeline` 中「写 upload → OCR/缓存 → `parse_holdings_from_text` → ocr_source 推断」这一段（原 line 62-99）替换为：

```python
    if file_bytes and filename:
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
        upload_path = settings.upload_dir / Path(filename).name
        upload_path.write_bytes(file_bytes)

    try:
        extraction: ExtractionResult = extract_holdings(file_bytes=file_bytes, text=text)
    except Exception as exc:  # noqa: BLE001 — 识别异常不应让端点 500
        _cleanup_upload_artifacts(upload_path)
        return {
            "raw_text": "",
            "upload_path": str(upload_path) if upload_path else None,
            "holdings": [],
            "error": f"识别失败：{exc}",
            "extraction_provider": "none",
        }

    text = extraction.raw_text or text
    parsed_holdings = extraction.holdings
    profile_service = FundProfileService()
    ocr_source = extraction.ocr_source
    lines = [line.strip() for line in text.splitlines() if line.strip()] if text else []
    if parsed_holdings and ocr_source == "unknown":
        ocr_source = "alipay_holdings"
```

> 删除原先 `cache_hit`/`get_ocr_text_cache`/`save_ocr_text_cache` 与 `detect_ocr_source`/`is_alipay_holdings_page` 推断逻辑（OCR 文本缓存改由 `holdings_extractor._default_local_fn` 内部不再做——本期接受丢失文本缓存；VLM 主路无需文本缓存）。`cache_hit` 字段固定为 `False`。

在最终 `result` 字典（原 line 160-176）加入：

```python
        "extraction_provider": extraction.provider,
```

并把 `"cache_hit": cache_hit,` 改为 `"cache_hit": False,`。

- [ ] **Step 4: Run to verify pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_ocr_pipeline.py -v`
Expected: PASS

- [ ] **Step 5: Run full OCR-related suite**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_ocr_pipeline.py tests/test_ocr_parser.py tests/test_overview_pipeline.py tests/test_api.py tests/test_bc2_ocr_upload.py -v`
Expected: PASS（如 `test_bc2_ocr_upload.py` 等依赖旧 `cache_hit=True` 行为，按新契约更新断言）

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/services/ocr_pipeline.py apps/api/tests/test_ocr_pipeline.py
git commit -m "feat(ocr): route /api/ocr through HoldingsExtractor (vlm primary, local fallback)"
```

---

## Task 7: 隐私边界 + 文档

**Files:**
- Modify: `docs/PROJECT_CONTEXT.md`（隐私段 line 156；能力清单「输入」行；环境变量「DeepSeek / 新闻」表后新增 OCR 段；更新记录顶部加一条）
- Modify: `README.md`（隐私和边界段）

- [ ] **Step 1: Update PROJECT_CONTEXT 隐私段**

把 line 156「**隐私：** DeepSeek 收到…不传原始截图。」改为：

```markdown
**隐私：** DeepSeek 收到**结构化持仓、风控、净值摘要、新闻标题/摘要**。截图识别默认走云端视觉模型（`FUND_AI_OCR_PROVIDER=auto`，配置 `FUND_AI_VLM_OCR_API_KEY` 即启用 `qwen3-vl-flash`）：**此时截图图片会发往阿里云百炼用于识别**；设 `FUND_AI_OCR_PROVIDER=local` 可强制本地 PaddleOCR 不外传截图。见 `README.md`「隐私和边界」。
```

- [ ] **Step 2: Update 能力清单「输入」行 + 环境变量表 + 更新记录**

「输入」行追加：`；**截图识别引擎 auto**：有 key 走云端 `qwen3-vl-flash`(结构化JSON、QDII/截不全鲁棒)、否则/失败回退本地 PaddleOCR`。

在 line 696-698 OCR 环境变量附近追加：

```markdown
| `FUND_AI_OCR_PROVIDER` | auto | 截图识别引擎：auto/vlm/local |
| `FUND_AI_VLM_OCR_API_KEY` | — | 阿里云百炼 Key；配置后 auto 启用云端识别（截图发往该 API） |
| `FUND_AI_VLM_OCR_MODEL` | qwen3-vl-flash | 视觉模型，可切 qwen3-vl-plus |
| `FUND_AI_VLM_OCR_TIMEOUT_SECONDS` | 20 | VLM 读超时 |
```

更新记录顶部加一条（日期 2026-06-24）简述本次升级与根因。

- [ ] **Step 3: Update README 隐私和边界**

在 README「隐私和边界」补充与上文一致的「截图识别引擎与外传说明」。

- [ ] **Step 4: Commit**

```bash
git add docs/PROJECT_CONTEXT.md README.md
git commit -m "docs(ocr): document VLM OCR engine and updated privacy boundary"
```

---

## Task 8: Live 烟测脚本（手动验证，需 key）

**Files:**
- Create: `apps/api/scripts/smoke_vlm_ocr.py`

- [ ] **Step 1: Implement the smoke script**

`apps/api/scripts/smoke_vlm_ocr.py`：

```python
"""手动 live 验证：对真实截图跑 VLM 识别，打印耗时 + 结构化结果。

用法（需先在 .env 配置 FUND_AI_VLM_OCR_API_KEY）：
    cd apps/api && ./.venv/Scripts/python.exe scripts/smoke_vlm_ocr.py <图片路径> [更多图片...]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from app.services.vlm_holdings_provider import extract_holdings_via_vlm


def main() -> None:
    for path in sys.argv[1:]:
        p = Path(path)
        if not p.is_file():
            print(f"!! 文件不存在: {p}")
            continue
        t0 = time.perf_counter()
        try:
            holdings = extract_holdings_via_vlm(p.read_bytes())
        except Exception as exc:  # noqa: BLE001
            print(f"{p.name}: 失败 {type(exc).__name__}: {exc}")
            continue
        dt = time.perf_counter() - t0
        print(f"{p.name}: {dt:.2f}s, {len(holdings)} 只")
        for h in holdings:
            print(f"  - {h.fund_name} | 金额 {h.holding_amount} | 收益 {h.holding_profit} | {h.holding_return_percent}%")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add apps/api/scripts/smoke_vlm_ocr.py
git commit -m "chore(ocr): add live smoke script for VLM holdings extraction"
```

- [ ] **Step 3: 全量回归**

Run: `./.venv/Scripts/python.exe -m pytest tests -q -n auto --dist loadscope`
Expected: 全绿（与 CI 一致）

- [ ] **Step 4: 前端契约检查（无改动也跑）**

Run: `cd apps/web && npm run typecheck`
Expected: PASS（`parseOcrUpload` 契约新增可选字段不破坏类型）

---

## 验收（人工）

1. 配置 `FUND_AI_VLM_OCR_API_KEY` 后跑 `scripts/smoke_vlm_ocr.py` 对 image1/image2：**image1 出 6 只(含 3 QDII)、image2 出 2 只(跳过余额宝)且不报错、单图 <5s**。
2. 启动 `bash scripts/dev.sh`，在「持仓 → 新增持有」上传 image1/image2：均成功进确认页、数量正确、无 `failed to fetch`。
3. 临时 `FUND_AI_OCR_PROVIDER=local` 重启，重传 image1/image2：走本地仍能出全部基金、image2 不崩（验证回退路径）。

---

## Self-Review

- **Spec 覆盖：** ①崩溃→Task1；②QDII/准确→Task2；③速度/VLM→Task3/4/5/6；隐私→Task7；验证→Task8+验收。✓
- **类型一致：** `ExtractionResult`(holdings/ocr_source/raw_text/provider)、`extract_holdings(file_bytes,text,settings,vlm_fn,local_fn)`、`extract_holdings_via_vlm(image_bytes,settings,completion)`、`parse_vlm_response(content)` 跨 Task 一致。✓
- **回退安全：** VLM 任何异常被 `holdings_extractor` 与 `ocr_pipeline` 双层捕获，端点永不 500。✓
- **无占位：** 每步含真实代码/命令/期望。✓
- **风险点：** Task6 删除文本缓存可能影响 `test_bc2_ocr_upload.py` 等旧断言（Step5 已要求按新契约更新）；`fund_name_utils.looks_like_fund_product_name` 内部实现未知，Task2 Step4 要求读后照改并以测试为准。
