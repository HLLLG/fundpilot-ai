# 场内关联板块实时涨跌 — 方案设计

**日期：** 2026-06-02  
**状态：** 已确认（2026-06-02）  
**目标：** 在不绑账户、不传截图的前提下，用公开行情接口刷新养基宝「关联板块」列的**当日涨跌幅**，使校对表与 AI 日报在盘中接近养基宝的实时体验。

### 已确认产品决策

| 项 | 决定 |
|----|------|
| 刷新方式 | **自动 + 手动**；交易时段默认每 **120 秒** 自动刷新（`localStorage` 可关） |
| 覆盖手改 | **始终用最新行情覆盖** `sector_return_percent`（`respect_manual=false`） |
| 未匹配/低置信 | **P0 含映射选择 UI**：多候选时用户点选，写入 `sector_mappings` |

**原则：**

- 板块涨跌仍写入现有 `sector_name` / `sector_return_percent`，不新增平行字段（元数据除外）。
- **失败可降级**：拉取失败时保留 OCR/手填值，不阻塞生成报告。
- **名称映射可积累**：首次模糊匹配成功后写入本地映射表，下次精确命中。
- 复用已有 `trading_session` 判断交易时段；复用 `news_cache` 的「同日 TTL」思路。

---

## 1. 背景与问题

### 1.1 现状

| 环节 | 行为 |
|------|------|
| OCR | 从养基宝总览解析 `sector_name`、`sector_return_percent` |
| 校对表 | 用户可手改；「估算当日收益率」= 板块涨跌 + 持有收益率 |
| 穿透拆分 | `allocate_penetration_daily` 按**当前**板块涨跌权重拆账户当日收益 |
| AkShare | 仅用于基金净值、新闻；**无板块实时接口调用** |

板块数据是**上传截图时的快照**，盘中不会变。

### 1.2 养基宝侧语义（从 fixture 归纳）

OCR 样例中的关联板块名：

| 养基宝显示 | 可能的数据类型 |
|------------|----------------|
| `中证电网设备` | 中证行业/主题指数 |
| `中证人工智能` | 中证指数 |
| `半导体` | 东财概念板块 |
| `商业航天` | 东财概念板块 |
| `上证指数` | 宽基指数 |

**核心难点：** 养基宝名称 ≠ 东财列表名称（前缀「中证」、省略「主题/ETF」等），需要**解析 + 映射 + 模糊匹配**，不能假设 6 位代码一一对应。

### 1.3 成功标准

1. 交易日上午 9:30–15:00，用户点「刷新板块涨跌」后，校对表 `sector_return_percent` 与养基宝同板块误差 **≤ 0.15%**（允许四舍五入差异）。
2. 刷新 10 只以内持仓，P95 耗时 **< 8s**（单次批量拉全市场 spot + 内存匹配，非每只单独 HTTP）。
3. AkShare 不可用时，界面提示「仍使用 OCR 值」，生成报告不受影响。
4. 用户手改过的板块涨跌**会被刷新覆盖**（以最新行情为准，减少人工维护）。

---

## 2. 方案对比（2–3 种）

### 方案 A：全量 Spot 快照 + 内存匹配（推荐）

**做法：** 每次刷新拉 2–3 张东财 spot 表（指数 / 行业 / 概念），在内存建 `{规范化名称 → 涨跌幅}` 索引，对每只持仓的 `sector_name` 做解析与匹配。

| 优点 | 缺点 |
|------|------|
| HTTP 次数固定（2–3 次/刷新），适合 10+ 持仓 | 首次冷启动需拉全表（约 1–3s） |
| 与现有 `news_service` 批量思路一致 | 仍依赖名称匹配质量 |
| 易 mock 测试 | 全表较大时需分页合并（AkShare 已封装） |

### 方案 B：按板块名单独查询

**做法：** 为每个 `sector_name` 调 `index_zh_a_hist_min_em` 或板块 hist 接口取最新一根分钟线。

| 优点 | 缺点 |
|------|------|
| 精准到代码后数据干净 | N 只持仓 ≈ N 次请求，易触发限频 |
| | 仍需先解决「名称→代码」 |

### 方案 C：仅维护静态映射表（JSON/SQLite）

**做法：** 人工维护 `sector_name → 东财代码`，刷新时只查 spot 表中对应 code。

| 优点 | 缺点 |
|------|------|
| 命中后极准 | 新板块、改名需人工维护 |
| | 无法覆盖养基宝全部主题 |

### 推荐：**A + C 混合**

- 默认走 **A**（自动匹配）；
- 匹配成功且置信度高 → 写入 **`sector_mappings`**（C 的自动化版）；
- 用户可在基金档案中 **固定映射**（覆盖自动结果）。

---

## 3. 数据源选型（AkShare / 东财）

| 优先级 | AkShare 接口 | 适用名称 | 关键字段 |
|--------|--------------|----------|----------|
| 1 | `stock_zh_index_spot_em()` | 上证/深证/中证类指数 | `代码` `名称` `涨跌幅` |
| 2 | `stock_board_concept_spot_em()` | 半导体、商业航天等概念 | `板块名称` `涨跌幅` |
| 3 | `stock_board_industry_spot_em()` | 行业类（电网设备等） | `板块名称` `涨跌幅` |

**不采用（本期）：**

- 分钟 K 线逐只拉取（方案 B）— 成本高。
- 雪球/养基宝非公开 API — 违反产品边界与稳定性要求。

**刷新粒度：** 全量 spot 快照；单快照 TTL 默认 **60s**（可配置），同一分钟内多次刷新读缓存。

**非交易日 / 已收盘：**

- 仍允许刷新；spot 表为**最近交易日收盘涨跌幅**，与养基宝收盘后一致。
- `trading_session.session_kind === non_trading_day` 时 UI 文案改为「最近交易日收盘涨跌，非盘中实时」。

---

## 4. 名称解析与匹配 pipeline

```text
sector_name (养基宝 OCR)
  → normalize_sector_label()      # 去空格、统一「中证/国证/上证」前缀
  → lookup sector_mappings        # 用户固定 / 历史成功映射
  → match index spot              # 精确 + 前缀 + contains
  → match concept spot
  → match industry spot
  → keyword fallback              # 复用 news_service 的 _TOPIC_ALIASES
  → unresolved                    # 保留原 sector_return_percent
```

### 4.1 规范化规则（`sector_quote_resolver.py`）

```python
# 示例
"中证电网设备" → candidates: ["中证电网设备", "电网设备", "电网设备主题"]
"华夏中证电网设备.." → 仅取关联板块列 OCR 结果，不走基金名
```

规则与 `news_service._normalize_topic` **共用**或抽至 `sector_labels.py`，避免三套别名逻辑。

### 4.2 置信度

| 级别 | 条件 | 是否写回 holdings |
|------|------|-------------------|
| `high` | mappings 表命中，或 spot 名称完全相等 | 是（除非用户锁定） |
| `medium` | 唯一 fuzzy match（编辑距离 / 包含关系） | 是，并写 mappings |
| `low` | 多个候选 | 否，UI 提示用户选择（P2） |
| `none` | 无匹配 | 否 |

### 4.3 持久化映射表 `sector_mappings`

```sql
CREATE TABLE IF NOT EXISTS sector_mappings (
  sector_label TEXT PRIMARY KEY,   -- 养基宝 OCR 原文（规范化后）
  source_type TEXT NOT NULL,       -- index | concept | industry
  source_code TEXT,                -- 东财代码（若有）
  source_name TEXT NOT NULL,       -- spot 表中的标准名称
  confidence TEXT NOT NULL,        -- high | medium
  updated_at TEXT NOT NULL
);
```

- 首次 `medium+` 匹配成功 → `INSERT OR REPLACE`。
- 基金档案可增加可选字段 `sector_quote_mapping_id`（P2）；P0 先用全局 label 映射。

---

## 5. 数据模型扩展

### 5.1 Holding（响应层元数据，不强制入库）

刷新 API 返回每只持仓的 **`sector_quote_meta`**（不入 SQLite Report，仅当次校对会话）：

```python
class SectorQuoteMeta(BaseModel):
    source: Literal["live", "ocr", "manual", "locked"]
    provider: str = "eastmoney-akshare"
    matched_name: str | None = None
    source_type: Literal["index", "concept", "industry"] | None = None
    source_code: str | None = None
    confidence: Literal["high", "medium", "low", "none"]
    fetched_at: datetime
    previous_percent: float | None = None   # 刷新前的值
    delta_vs_previous: float | None = None  # 与 OCR/旧值差
```

### 5.2 FundProfile（P2 可选）

```python
sector_quote_locked: bool = False   # 用户锁定，刷新跳过
sector_mapping_override: SectorMapping | None = None
```

P0 用前端 `localStorage` 记录「用户是否手改过 sector_return_percent」即可，改动量更小。

---

## 6. 后端设计

### 6.1 新模块

| 文件 | 职责 |
|------|------|
| `sector_quote_cache.py` | 内存 + SQLite TTL；键 `spot:{type}:{date}:{minute_bucket}` |
| `sector_quote_provider.py` | 调 AkShare 拉三类 spot，异常返回空 dict |
| `sector_quote_resolver.py` | label → `(change_percent, meta)` |
| `sector_quote_service.py` | `refresh_holdings_sector_quotes(holdings) -> RefreshResult` |

### 6.2 API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/holdings/refresh-sector-quotes` | body: `{ holdings, respect_manual?: true }`；返回更新后 holdings + `items[].sector_quote_meta` + `summary` |
| GET | `/api/sector-quotes/cache-status` | 上次快照时间、TTL、是否在交易时段 |

**与 OCR 关系：** OCR 接口**不变**；刷新是校对阶段的独立操作。

**与穿透拆分关系：** 刷新后用户可再点「一键填充估算当日收益」，逻辑不变，只是权重更新。

### 6.3 配置 `.env`

| 变量 | 默认 | 含义 |
|------|------|------|
| `FUND_AI_SECTOR_QUOTES_ENABLED` | `true` | 总开关 |
| `FUND_AI_SECTOR_QUOTES_TTL_SECONDS` | `60` | spot 快照缓存 TTL |
| `FUND_AI_SECTOR_QUOTES_RESPECT_MANUAL` | `false` | **始终覆盖**为最新行情（含曾手改的行） |
| `FUND_AI_SECTOR_QUOTES_AUTO_INTERVAL_SECONDS` | `120` | 前端自动刷新间隔（秒），仅交易时段 |
| `FUND_AI_SECTOR_QUOTES_DISCREPANCY_WARN` | `0.5` | 与 OCR 差超过此值（百分点）则 warnings |

### 6.4 合并策略

```text
if resolver confidence == high or medium (single match):
    sector_return_percent = live_change
elif resolver confidence == low (multiple candidates):
    return candidates[] → 前端映射选择 UI
else:
    keep ocr, meta.source = ocr
if |live - previous| > DISCREPANCY_WARN:
    holding_warnings += sector_quote_discrepancy (severity=info, 仅提示)
```

**不自动改 `sector_name`**，仅更新涨跌幅；匹配到的标准名放在 meta 里供 UI 展示。

---

## 7. 前端设计

### 7.1 校对表 `HoldingTable`

- 工具栏新增 **「刷新板块涨跌」** 按钮（`RefreshCw` 图标）。
- 板块涨跌列：
  - 实时值：绿色/红色 + 小标签 **「实时」** + `fetched_at` HH:MM
  - OCR 保留：标签 **「OCR」**
  - 未匹配：保持原值 + 琥珀色 **「未匹配」** tooltip
- 刷新中：按钮 loading；表格行级 skeleton 可选。
- 大偏差：复用 `holding_warnings` 高亮 `sector_return_percent`（info 级）。

### 7.2 自动刷新（P1，默认开启）

| 条件 | 行为 |
|------|------|
| 当前 Tab = 今日 | 是 |
| `trading_session` 为 intraday / pre_close | 是 |
| 有持仓 | 是 |
| 间隔 | 默认 **120s**（`localStorage` + env 可关/可改） |

**不在后台 Tab / 非交易时段轮询**，避免无意义打东财。

### 7.3 与 `TradingSessionBar` 联动

收盘前窗口显示：「板块涨跌可点刷新；距上次刷新 X 分钟」。

---

## 8. 数据流（端到端）

```text
用户上传 OCR → holdings (sector_* 来自截图)
       ↓
[可选] 点击「刷新板块涨跌」或自动轮询
       ↓
POST /api/holdings/refresh-sector-quotes
       → trading_session 校验
       → sector_quote_service 读/写 cache
       → provider 拉 index + concept + industry spot（或命中 cache）
       → resolver 逐行匹配 + mappings 学习
       → 返回 holdings + meta + warnings
       ↓
HoldingTable 更新 → 估算当日收益率列联动重算
       ↓
生成报告 → DeepSeek 读到的 sector_return_percent 已是实时值
```

---

## 9. 错误处理与降级

| 场景 | 行为 |
|------|------|
| AkShare 超时/代理错误 | HTTP 200 + `summary.failed=true` + 保留原值；toast 提示 |
| 部分匹配 | 200 + 逐行 meta；summary 统计 matched/unmatched |
| 非交易时段 | 仍返回收盘涨跌；summary 注明 `session_kind` |
| 总开关关闭 | 404 或 503 + 明确文案 |

**日志（P1）：** 每类 spot 拉取耗时、行数、匹配率；不 log 完整 holdings 金额。

---

## 10. 测试策略

| 层 | 用例 |
|----|------|
| `test_sector_quote_resolver.py` | 规范化；中证前缀；fixture 名称命中 mock spot |
| `test_sector_quote_service.py` | mock provider 三张表；TTL 命中；manual respect |
| `test_api.py` | POST refresh 返回 meta；开关关闭 |
| fixture | `tests/fixtures/sector_spot_index.json` 等静态帧 |

**不做：** 依赖真实东财网络的 CI 用例（仅本地人工验收）。

---

## 11. 分期实施

### P0（MVP，约 2–2.5 天）

- [ ] `sector_quote_provider` + `resolver` + `service`
- [ ] `sector_mappings` 表 + 成功匹配写入 + **POST 用户选定映射**
- [ ] `POST /api/holdings/refresh-sector-quotes`
- [ ] `HoldingTable` 手动刷新 + 来源标签 + **低置信度映射选择 UI**
- [ ] 交易时段 **120s 自动刷新**（可关）
- [ ] 配置项 + pytest（全 mock）
- [ ] 更新 `PROJECT_CONTEXT.md`

### P1（约 0.5 天）

- [ ] spot 快照 SQLite TTL（与 news_cache 同模式）
- [ ] OCR vs 实时偏差 `holding_warnings`
- [ ] `GET /api/sector-quotes/cache-status`

### P2（可选，部分并入 P0）

- [ ] `FundProfile.sector_mapping_override`（档案级固定映射）
- [ ] 刷新后可选「同步到基金档案」

---

## 12. 风险与限制

1. **AkShare 非官方**：接口变更需跟进；已有基金净值链路证明项目可接受此风险。
2. **名称无法 100% 对齐养基宝**：靠 mappings 积累 + 用户 override；首周可能 1–2 只需手选。
3. **不是基金本身实时估值**：仍是关联板块涨跌，与养基宝一致；基金当日收益率仍可能需估算或收盘后 OCR。
4. **限频**：TTL 60s + 禁止无持仓刷新 + 自动刷新 ≥180s，避免被封。

---

## 13. 产品决策（已确认 2026-06-02）

1. **自动刷新：** 开启；交易时段默认 **120 秒**；保留手动刷新按钮；`localStorage` 可关闭自动刷新。
2. **覆盖手改：** **始终覆盖**为最新行情（`FUND_AI_SECTOR_QUOTES_RESPECT_MANUAL=false`）。
3. **映射选择：** P0 包含；多候选时在 UI 选择并 `POST /api/sector-mappings` 持久化。

---

## 14. 验收清单（Definition of Done）

1. 交易日下午用真实持仓点「刷新板块涨跌」，≥80% 行匹配成功且与养基宝 App 同板块涨跌一致（±0.15%）。
2. 关闭 `FUND_AI_SECTOR_QUOTES_ENABLED` 后按钮隐藏或不可用，报告流程不变。
3. AkShare 失败时 holdings 不变，前端有明确提示。
4. `pytest tests` 全绿；`PROJECT_CONTEXT` 与 `.env.example` 已更新。
