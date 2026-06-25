# 截图识别引擎切换：qwen3-vl-flash → qwen-vl-ocr（纯文本 OCR + 本地解析 + 上传前压缩）

**日期：** 2026-06-25
**状态：** ✅ 已实现并 live 烟测通过
**范围：** 「新增持有」截图识别的云端 VLM provider（`vlm_holdings_provider.py`）+ 配置 + 文档。交易记录 / 养基宝详情 OCR 本期不动。

> **⚠️ 实现期重大修订（2026-06-25，实测后定稿）**
> 原设计（§6.2/§6.3 below）打算让 qwen-vl-ocr **直接输出结构化 JSON**（自定义 prompt）。**两张真实截图实测推翻了该方案**：qwen-vl-ocr 文字识别极强，但**做不了支付宝「金额/日收益/持有收益/累计收益」多列 + 纵向错位的字段归属推理**——无论 prompt 怎么写，都会把日收益/累计收益错当持有收益、把数字当基金名、还把余额宝当基金。用用户自己在百炼控制台验证过的 prompt 复测同样如此（top6 持有收益 -5.71 应为 +0.01；bottom2 含余额宝、持有收益 -391.90 应为 -373.30）。
>
> **最终方案**：**模型只做纯文本 OCR（不传 text prompt，用模型默认逐行转录；传「阅读顺序」prompt 反而触发文字定位/坐标输出），再把 OCR 文本交给久经测试的本地 `parse_holdings_from_text` 做结构化**——与本地 PaddleOCR 路径共用同一解析器（对称、确定性、对位正确、自带余额宝/footer 过滤）。`extract_holdings_via_vlm` 改返回 `(holdings, ocr_text)`，raw_text 透传 pipeline。
>
> 实测：top6 6 只全中(含 3 QDII)、电网设备 2000.01/持有收益 +0.01 列对位正确(4.16s)；bottom2 2 只、余额宝跳过、中航 9626.70/持有收益 -373.30 正确(2.47s)。唯一小限制：bottom2 持有收益率模型漏识别（None，次要、导入后重算）。
>
> 下文 §2.2、§6.2、§6.3 的「自定义 prompt 出 JSON」段落为**历史设计，已废弃**，保留供追溯。§4 配置、§5 压缩、§6.1 messages（除 text prompt 已移除）、§7 测试仍有效。

---

## 1. 背景与动机

2026-06-24 已完成「云端 VLM + 本地解析器修复」升级（设计见 `2026-06-24-ocr-vlm-upgrade-design.md`），但**尚未配置 API Key、未正式启用**，默认模型为 `qwen3-vl-flash`。

经用户调研：

1. `qwen3-vl-flash` 即将下线；
2. `qwen-vl-ocr`（文字识别专用模型）同样能识别支付宝持仓截图，且**更便宜**。

因此本期把默认云端识别模型从 `qwen3-vl-flash` 切到 **`qwen-vl-ocr` 稳定版**，并：

- 按百炼官方文档为 `qwen-vl-ocr` 配置正确的调用参数（`min_pixels` / `max_pixels`）；
- 为「正确提取支付宝持仓」设计专用 prompt（含列布局描述、噪声过滤）；
- 在上传图片前做**安全压缩**（转 JPEG + 仅超大时缩放），在不牺牲准确度的前提下减小上传体积/延迟。

**已确认的决策（与用户）：**

| 决策 | 选择 |
|------|------|
| 模型版本 | `qwen-vl-ocr` 稳定版（现已等同 `qwen-vl-ocr-2025-11-20`，基于 Qwen3-VL，能力最新、最便宜、版本名稳定） |
| 压缩策略 | 转 JPEG(质量~85) + 仅当最长边超阈值才等比缩小；`max_pixels` 设较大值兜底 token（优先准确度） |
| 验证方式 | 用户在 `.env` 配置 `FUND_AI_VLM_OCR_API_KEY` 后，由 AI 用真实截图跑 live 烟测 |

---

## 2. 关键调研结论（百炼官方文档 + 计费）

1. **调用链路不变**：`qwen-vl-ocr` 支持 **OpenAI 兼容模式**（`https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`），与现有 `qwen3-vl-flash` 调用方式一致，沿用现有 `httpx` + `_dashscope_completion`。
2. **支持自定义 prompt**：在 `messages[].content` 的 `text` 字段传入 prompt 即可输出结构化 JSON；**不传 prompt 时模型只做纯文本转录**（默认 prompt 为 "Please output only the text content..."），所以必须传我们的结构化 prompt。
3. **支持 base64 data-URL**：OpenAI 兼容模式可传 `data:image/{format};base64,{...}`，**MIME 必须与真实图片格式一致**（压缩成 JPEG 后须用 `image/jpeg`）。
4. **图像缩放参数**：`min_pixels` / `max_pixels` 作为 `image_url` 的**同级字段**放在同一个 content part 内（非 `image_url` 对象内部）。`qwen-vl-ocr`(32×32 族) 默认 `min_pixels=3072`、`max_pixels=8388608`（≈8192 图像 token）。
5. **token 成本只与图像像素数有关**（每 1024 像素 = 1 token，封顶由 `max_pixels` 控制），**与 JPEG 文件体积无关**。
   - 一张支付宝持仓截图（约 1080×2400 ≈ 2.6M 像素）≈ **2.5k 图像 token**，远低于默认 `max_pixels`，不会被自动缩小。
   - 计费（中国内地）：输入 ¥0.30/百万 token、输出 ¥0.52/百万 token（≈$0.043/$0.072）。单图成本 **< ¥0.001**。
6. **结论（压缩）**：JPEG 压缩画质**不能省 token**；省 token 只能靠降分辨率/调小 `max_pixels`，但会损小字准确度，且省下来的钱可忽略。**压缩的真正收益是减小上传体积/延迟**（PNG 截图常 1–3MB → JPEG 几百 KB），有助 <5s 目标，尤其云端 CloudBase。故采用「JPEG + 仅超大才缩放」的安全压缩，`max_pixels` 用较大默认值仅作兜底。

---

## 3. 架构（不变）

`HoldingsExtractor` 的 `auto`/`vlm`/`local` 软回退、`_resolve_fund_codes`、preview 链路、本地 PaddleOCR 回退**全部保持不变**。本期只改 `vlm_holdings_provider.py`（provider 内部）+ `config.py` + 文档。

```
POST /api/ocr (preview)
  → run_ocr_upload_pipeline
      → HoldingsExtractor.extract
          ├─ VlmHoldingsProvider   ← 本期改：qwen-vl-ocr + 压缩 + min/max_pixels + 新 prompt
          └─ LocalOcrHoldingsProvider (PaddleOCR)  ← 不变
      → _resolve_fund_codes / enrich / preview   ← 不变
```

---

## 4. 配置变更（`config.py` + `.env.example`）

`Settings` 中：

| 字段 | 旧 → 新默认 | 含义 |
|------|------|------|
| `vlm_ocr_model` | `"qwen3-vl-flash"` → **`"qwen-vl-ocr"`** | 默认云端识别模型 |
| `vlm_ocr_min_pixels: int` | 新增 `3072` | 图像最小像素（小于则放大），qwen-vl-ocr 默认/最小值 |
| `vlm_ocr_max_pixels: int` | 新增 `8388608` | 图像最大像素（大于则缩小）；token 上限兜底 |
| `vlm_ocr_compress_enabled: bool` | 新增 `True` | 是否在上传前转 JPEG 压缩 |
| `vlm_ocr_jpeg_quality: int` | 新增 `85` | JPEG 画质（1~95） |
| `vlm_ocr_max_image_side: int` | 新增 `2000` | 仅当最长边超过该值才等比缩小（像素，0/负数表示不缩放） |

`vlm_ocr_base_url` / `vlm_ocr_api_key` / `vlm_ocr_timeout_seconds` / `ocr_provider` 不变。

`.env.example` 同步追加上述新变量及注释（含「JPEG 压缩不影响 token、token 由 max_pixels 控制」的说明）。

---

## 5. 图片压缩 helper（`vlm_holdings_provider.py`，best-effort）

新增纯函数：

```python
def compress_image_for_vlm(
    image_bytes: bytes, settings: Settings
) -> tuple[bytes, str]:
    """返回 (处理后字节, MIME)。任何异常都回退原图，绝不抛出。"""
```

逻辑：

1. `settings.vlm_ocr_compress_enabled` 为 False → 直接返回 `(image_bytes, _guess_mime(image_bytes))`。
2. 用 Pillow `Image.open(BytesIO(image_bytes))`：
   - 转 RGB（处理 RGBA / P / LA，避免 JPEG 不支持 alpha 报错）；
   - 若 `max_image_side > 0` 且最长边 > `max_image_side` → 等比 `thumbnail` 缩小；
   - `save(BytesIO, format="JPEG", quality=settings.vlm_ocr_jpeg_quality, optimize=True)`；
   - 返回 `(jpeg_bytes, "image/jpeg")`。
3. 任意异常（含非法图片字节、Pillow 缺失）→ `except Exception` → 返回 `(image_bytes, "image/png")`（保持现有行为）。

> 说明：`_guess_mime` 仅做最简判别（PNG/JPEG/WEBP magic bytes），无法判别时回退 `image/png`（与现状一致）。

---

## 6. VLM provider 调整（`vlm_holdings_provider.py`）

### 6.1 messages 构建

```python
def build_vlm_messages(image_bytes: bytes, settings: Settings) -> list[dict]:
    data, mime = compress_image_for_vlm(image_bytes, settings)
    data_url = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
    return [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": data_url},
                    "min_pixels": settings.vlm_ocr_min_pixels,
                    "max_pixels": settings.vlm_ocr_max_pixels,
                },
                {"type": "text", "text": VLM_EXTRACTION_PROMPT},
            ],
        }
    ]
```

> `build_vlm_messages` 签名新增 `settings` 参数（`extract_holdings_via_vlm` 已持有 settings，传入即可）。`min_pixels`/`max_pixels` 是 `image_url` content part 的同级字段。

### 6.2 新 prompt（针对 qwen-vl-ocr + 支付宝「全部持有」）

要点（最终文案在实现时定稿）：

- 角色：基金持仓截图识别器；**只输出 JSON，不要任何解释**。
- 输出 schema：`{"holdings":[{"fund_name","fund_code","holding_amount","daily_profit","holding_profit","cumulative_profit","holding_return_percent","weight_percent"}]}`。
- **列布局**（关键，帮助数字对位）：每只基金块内——
  - 第一行：基金名称（保留完整名，含 `(QDII)`/`（QDII）`/`ETF联接`/份额字母 A/B/C，不拆词不翻译）；
  - 标签行：`基金` / `进阶理财` / `定投` 等，忽略；
  - 数值行从左到右：**金额(holding_amount) → 日收益(daily_profit) → 持有收益(holding_profit) → 累计收益(cumulative_profit)**；
  - 金额下方：`占比 X.XX%`(weight_percent)；「持有收益」列下方：`持有收益率 X.XX%`(holding_return_percent)。
- **只提取带「基金」标签的行**；跳过：`余额宝 / 余额 / 现金 / 灵活取用` 等货基行、顶部 Tab（全部持有/收益明细/交易记录）、图标（清仓分析/收益地图/基金定投/专项计划）、排序控件（持有收益排序）、底部法律声明（本页面非任何法律文件…/该页面由蚂蚁财富…/以上按照持有收益排序）。
- 亏损（绿色减号）保留负号；看不到的字段填 `null`，不要编造。
- 截图可能截不全（无表头或只有底部片段），仍尽量提取所有可见基金行。

### 6.3 completion 调用

`_dashscope_completion` 基本不变（payload `{"model","messages"}`）；`extract_holdings_via_vlm` 把 `settings` 传给 `build_vlm_messages`。`parse_vlm_response` **完全不变**（已能抠首个 JSON 对象、容错 ```json``` 包裹与多余文本、无码→`000000`）。

---

## 7. 测试与验证

### 7.1 单元测试（mock / 离线，不打真实 API）

- `tests/test_config.py`：默认模型断言 `qwen3-vl-flash` → `qwen-vl-ocr`；新增 `min_pixels=3072`/`max_pixels=8388608`/`compress_enabled=True`/`jpeg_quality=85`/`max_image_side=2000` 默认值断言。
- `tests/test_vlm_holdings_provider.py`：
  - 新增 `compress_image_for_vlm`：用 Pillow 生成一张大图（如 3000×100 纯色）→ 压缩后为 JPEG、最长边 ≤ 2000；非法图片字节（`b"\x89PNG_fake"`）→ 回退原字节 + `image/png`；`compress_enabled=False` → 原字节直返。
  - 新增 `build_vlm_messages` 含 `min_pixels`/`max_pixels` 字段、压缩后 data-URL 为 `image/jpeg`（用合法 PNG 字节）。
  - 既有 `parse_vlm_response` / `extract_holdings_via_vlm` 用例保持通过（`extract_holdings_via_vlm(b"\x89PNG_fake", completion=...)` 因压缩回退仍能 base64 编码）。
- **全量回归**：`pytest -q -n auto --dist loadscope` 全绿。

### 7.2 Live 烟测（用户配置 key 后，由 AI 执行）

`scripts/smoke_vlm_ocr.py`（已存在，必要时微调）对用户提供的两张真实截图：

- `assets/...107g-...png`（image1 顶部 6 只，含 3 只 QDII）；
- `assets/...8ecg-...png`（image2 底部 2 只 + 余额宝/余额 + footer）。

验收标准：

1. **image1 出 6 只**（含 `天弘全球高端制造混合(QDII)C`、`富国全球科技互联网股票(QDII)C`、`广发全球精选股票(QDII)C` 三只 QDII），`华夏中证电网设备主题ETF联接C` 金额 `2000.01`、占比 `5.77%`。
2. **image2 出 2 只**（`华夏全球科技先锋混合(QDII)C`、`中航机遇领航混合C`），**跳过 余额宝/余额 与 footer**；`中航机遇领航混合C` 金额 `9626.70`、日收益 `-391.90`、持有收益 `-373.30`、持有收益率 `-3.73%`、占比 `27.80%`（验证列对位与负号）。
3. **单图 < 5s**。
4. 端到端（`bash scripts/dev.sh` → 持仓 → 新增持有上传）成功进确认页、无 `failed to fetch`。
5. 临时 `FUND_AI_OCR_PROVIDER=local` 重启回退本地仍可用（回归不破坏）。

---

## 8. 影响的文件

- 改：`apps/api/app/services/vlm_holdings_provider.py`（压缩 helper + min/max_pixels + 新 prompt + MIME + build_vlm_messages 签名）、`apps/api/app/config.py`（默认模型 + 5 个新配置）、`.env.example`、`docs/PROJECT_CONTEXT.md`（环境变量默认值/能力清单/更新记录）。
- 改/增测试：`apps/api/tests/test_vlm_holdings_provider.py`、`apps/api/tests/test_config.py`。
- 资产：把两张真实截图纳入仓库（`apps/api/tests/fixtures/` 或复用 `assets/`）供烟测/回归参考。
- 不改：`holdings_extractor.py`、`ocr_pipeline.py`、前端、本地解析器。

---

## 9. YAGNI / 边界

- 不做交易记录 / 养基宝详情 OCR 的 VLM 化。
- 不引入新依赖（Pillow 已在 `requirements.txt`，且压缩 best-effort 缺失也不影响）。
- 不用 DashScope 原生 SDK 的 `ocr_options` 内置任务（OpenAI 兼容模式不支持该参数；自定义 prompt 已满足结构化需求，且保持与现有调用链一致）。
- 隐私边界不变：启用 VLM 时截图发往阿里云百炼；`FUND_AI_OCR_PROVIDER=local` 强制本地不外传。
