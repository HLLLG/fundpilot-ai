# 截图识别升级：云端 VLM（qwen3-vl-flash）+ 本地解析器修复

**日期：** 2026-06-24
**状态：** 已确认，待实现
**范围：** 「新增持有」截图识别路径（`POST /api/ocr` preview）。交易记录 OCR（`/api/transactions/ocr`）本期不动。

---

## 1. 背景与根因（已复现）

用户反馈三个问题，已在本机用真实截图（image1 支付宝「全部持有」顶部 6 只、image2 底部含余额宝）复现并定位根因：

| # | 现象 | 根因（有证据） |
|---|------|----------------|
| ① | image2 上传直接 `failed to fetch` | `parse_holdings_from_text` **未被 try/except 包裹**（`ocr_pipeline.py:88`）；`_extract_my_holdings_metrics` 在 `holding_profit=None` 时调用 `is_near_zero(None)` → `abs(None)` `TypeError` → 端点 500；云网关下 500/504 不带 CORS → 浏览器报 `failed to fetch` |
| ② | image1 有 6 只，只识别出 3 只 | **QDII 基金名识别失败**：`混合(QDII)C` / `股票(QDII)C` 同时不被 `COMPLETE_FUND_NAME_RE` 与 `looks_like_fund_product_name` 接受（实测 5 只 QDII 全部 False，非 QDII 全部 True）。漏掉 QDII 还导致 image2 落入有 bug 的 `_split_fund_blocks` 兜底路径 → 触发①的崩溃 |
| ③ | 识别慢（~10s+），养基宝 <5s | 本机 PaddleOCR CPU 推理本身 **单图 6–9s**（实测 9.4/5.9/6.9s），冷启动模型加载 +~3.8s，首次上传还要拉东财基金名称表子进程。已在 mobile 模型。**养基宝的快是因为走云端 OCR（小程序/32MB App 不可能本地跑模型）**，其准确率同样有 ~3% 误差并对截图有诸多限制——同类「云 OCR + 规则解析」架构，只是引擎在云端 |

**结论：** ①② 是纯本地解析器 bug，可零架构改动修复；③ 是架构问题，本机 CPU 无法稳定 <5s，需云端引擎。已与用户确认走 **Plan A：云端 VLM 直接出结构化 JSON**，模型选 **`qwen3-vl-flash`**（阿里云百炼/DashScope，OpenAI 兼容；单图 token 极低成本可忽略；支持结构化输出）。

---

## 2. 目标

1. **快**：识别端到端追平养基宝（目标 <5s，warm 路径）。
2. **准**：恢复 image1 全部基金、正确处理 QDII 名与截不全的截图。
3. **稳**：image2 类截图绝不再 `failed to fetch`；云端不可用时自动回退本地仍可用。
4. **隐私透明**：启用 VLM 时明确告知截图会发往所配置的视觉 API。

非目标：交易记录 OCR、养基宝详情 OCR 的 VLM 化（后续可复用同抽象，本期不做）。

---

## 3. 架构：识别引擎抽象 + 自动回退

把「图片/文本 → `list[Holding]`」抽象成 provider，下游（查码、补档案、preview 返回、写库）**完全不变**。

```
POST /api/ocr (preview)
  → run_ocr_upload_pipeline
      → HoldingsExtractor.extract(file_bytes, text)         # 新抽象层
          ├─ VlmHoldingsProvider   (qwen3-vl-flash 图→JSON)  [主]
          └─ LocalOcrHoldingsProvider (PaddleOCR + 修好的解析器) [回退]
      → _resolve_fund_codes(holdings)   # 名称→6位码，不变
      → enrich / 组装 preview 响应       # 不变
```

### 3.1 选择策略 `FUND_AI_OCR_PROVIDER`

| 值 | 行为 |
|----|------|
| `auto`（默认） | 有 `FUND_AI_VLM_OCR_API_KEY` → 先用 VLM；VLM 超时/报错/返回空 → **回退本地**。无 key → 直接本地 |
| `vlm` | 强制 VLM；失败仍回退本地（保证可用性），但日志告警 |
| `local` | 强制本地（隐私优先 / 离线） |

回退是「软回退」：任何 VLM 异常都被捕获，绝不冒泡到端点。

### 3.2 数据契约

provider 统一返回：

```python
@dataclass
class ExtractionResult:
    holdings: list[Holding]
    ocr_source: str          # 仍用 "alipay_holdings"，下游 amount_semantics 不变
    raw_text: str            # VLM 路径可为模型回读文本或空串
    provider: str            # "vlm" | "local"
```

`run_ocr_upload_pipeline` 响应新增 `extraction_provider` 字段（前端/调试透明）。

---

## 4. VLM Provider（`vlm_holdings_provider.py`）

- **调用**：DashScope OpenAI 兼容端点 `{base_url}/chat/completions`，`httpx`，复用 `deepseek_http` 同款 headers/timeout/错误格式化风格（独立函数，不耦合 DeepSeek key）。图片以 base64 data URL 放入 `messages[].content` 的 `image_url`。
- **输出**：要求模型**只输出 JSON**：

```json
{"holdings": [
  {"fund_name": "天弘全球高端制造混合(QDII)C",
   "fund_code": null,
   "holding_amount": 100.00,
   "daily_profit": 0.00,
   "holding_profit": 0.00,
   "cumulative_profit": 0.00,
   "holding_return_percent": 0.00,
   "weight_percent": 0.29}
]}
```

- **Prompt 关键约束**：
  - 只提取带「基金」标签的持仓行；**跳过 `余额宝/余额/现金/灵活取用` 货基行**与底部法律声明、页眉/Tab。
  - 保留**完整基金名**（含 `(QDII)`、`ETF联接C`、份额字母 A/B/C 等），不要拆词。
  - 按支付宝列布局对应：名称/金额、日收益、持有收益、累计收益、持有收益率(%)、占比(%)。
  - 亏损保留负号；缺失字段填 `null`，不要编造。
  - 截不全（无表头/底部片段）也尽量提取可见基金行。
- **映射**：→ `Holding`，无 `fund_code` → `"000000"`（交给现有 `_resolve_fund_codes` 用东财名称表补码，名称更全 → 匹配更准）。
- **健壮性**：解析模型返回时 strip ```` ```json ```` 包裹、容忍多余文本（正则抠首个 JSON 对象）；解析失败/空 holdings → 抛内部异常触发回退。
- **超时**：`FUND_AI_VLM_OCR_TIMEOUT_SECONDS`（默认 20）。

---

## 5. 本地解析器修复（兜底路径，必做）

`alipay_holdings_parser.py` / `ocr_parser.py`：

1. **修崩溃**：
   - `_extract_my_holdings_metrics`：`is_near_zero(holding_profit)` 前加 `holding_profit is not None` 守卫（两处分支）。
   - `parse_holdings_from_text` 整体 try/except，异常 → 返回 `[]`，**永不抛到端点**。（`ocr_pipeline.py` 亦不应因解析崩溃 500。）
2. **修 QDII 名**：`COMPLETE_FUND_NAME_RE` 与 `looks_like_fund_product_name` 允许 `混合/股票/指数/联接` 与份额字母间出现 `(QDII)`/`（QDII）`（半/全角括号、可选 `-` 变体）。
3. **过滤噪声行**：`余额宝/余额/灵活取用` 与法律声明 footer（`本页面非任何法律文件…`/`该页面由蚂蚁财富平台…`）不计入基金，加入 noise/footer markers。

---

## 6. 配置与隐私

新增 `Settings`（`config.py`）+ `.env.example`：

| 变量 | 默认 | 含义 |
|------|------|------|
| `FUND_AI_OCR_PROVIDER` | `auto` | `auto`/`vlm`/`local` |
| `FUND_AI_VLM_OCR_API_KEY` | — | DashScope/百炼 API Key（缺省则 auto 回退本地） |
| `FUND_AI_VLM_OCR_BASE_URL` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | OpenAI 兼容端点 |
| `FUND_AI_VLM_OCR_MODEL` | `qwen3-vl-flash` | 视觉模型；可切 `qwen3-vl-plus` |
| `FUND_AI_VLM_OCR_TIMEOUT_SECONDS` | `20` | 读超时 |

**隐私边界更新**（README / `PROJECT_CONTEXT.md`）：原「默认不把原始截图发往云端」改为「**启用 VLM（默认 auto，配置了 key 即启用）时，截图图片会发往所配置的视觉 API（阿里云百炼）用于识别**；设 `FUND_AI_OCR_PROVIDER=local` 可强制本地不外传」。与养基宝同款取舍，用户已确认。

---

## 7. 测试与验证

**单元测试（全部可注入 mock，不打真实 API）：**
- `test_vlm_holdings_provider.py`：给定样例 JSON 响应 → 正确 `Holding` 列表；畸形 JSON / ```` ```json ```` 包裹 / 多余文本 → 正确抠出或触发异常；prompt 侧跳过余额宝由「响应不含余额宝」覆盖（断言映射不臆造）。
- `test_holdings_extractor.py`：`auto` 有 key 且 VLM 成功 → 用 VLM；VLM 抛异常 → 回退 local；无 key → local；`local` 强制 local。
- `test_ocr_parser.py` / `test_alipay_holdings_parser.py` 增量：QDII 名被识别；image2 真实 OCR 文本（新增 fixture）不再崩、recover 全部基金；余额宝/footer 被过滤。
- `test_ocr_pipeline.py`：注入假 provider，验证 `extraction_provider` 字段与下游不变。

**Fixtures：** 用本次抓到的 image1/image2 真实 OCR 文本落 `tests/fixtures/`（本地解析器回归用）。

**实测验证：**
- 本地解析器修复：用真实文本 fixture 离线验证（无需网络）。
- VLM 路径：用户提供 key 后，跑一次性 live smoke 脚本对 image1/image2 端到端验证 **速度 + 准确率（8 只全中、QDII 正确、image2 不崩）**。

---

## 8. 影响的文件（预估）

- 新增：`apps/api/app/services/vlm_holdings_provider.py`、`holdings_extractor.py`；`tests/test_vlm_holdings_provider.py`、`tests/test_holdings_extractor.py`；`tests/fixtures/alipay_overview_*_ocr.txt`。
- 改：`ocr_pipeline.py`（接入 extractor + try/except）、`alipay_holdings_parser.py` / `ocr_parser.py`（QDII + 崩溃 + 噪声）、`config.py`、`.env.example`、`docs/PROJECT_CONTEXT.md`、`README.md`。
- 前端：基本不变（`parseOcrUpload` 契约不变）；可选展示 `extraction_provider`（非必须）。

---

## 9. YAGNI / 边界

- 不做交易记录/养基宝详情的 VLM 化（同抽象后续可扩展）。
- 不引入新依赖（用现有 `httpx`）。
- 不改前端识别 UI 流程，只增强后端引擎。
