# 美股概览（市场 Tab · 美股子 Tab）设计

**关联需求：** `.kiro/specs/us-market-overview/requirements.md`（需求 1–9）
**对标：** 小倍养鸡「美股基金涨跌助手」
**复用基线：** `2026-06-17-market-sector-performance-design.md`、`2026-06-17-market-theme-boards-design.md`（市场 Tab 子 Tab + snapshot + TTL 缓存 + `useCachedFetch` 模式）

---

## 1. 背景与目标

在已有「市场」Tab（子 Tab：全市场 | 主题板块）下新增第三个子 Tab **「美股」**，提供：

1. 顶部指标卡：纳指期货 / 标普期货 / 道指期货（**真实指数期货**，禁止回退收盘价）+ USD/CNY 人民币汇率。
2. QDII「盘前参考涨跌」列表：为跟踪美股/全球指数的 QDII 基金按盘前指数期货涨跌估算 `Reference_Change_Percent`（明确标注为非承诺性预估）。
3. 美股交易时段标签（盘前 / 盘中 / 盘后 / 休市，含夏令时）与数据更新时间。

复用现有服务端 snapshot + 时段感知缓存、前端 `useCachedFetch` / `clientCache` session 缓存，并落实**优雅降级**（陈旧/不可用，禁止编造数值）与**数据源可行性验证**两项强制需求。

### 非目标（本期）

- 个股美股行情、美股盘中分时图、期权链。
- QDII 实时净值估算（仅给基于盘前指数的参考涨跌）。
- 美股节假日精确日历的权威接入（本期用务实简化，见 §6.3）。

---

## 2. 数据源可行性验证（需求 8 强制交付）

> 结论：本环境通过 **AkShare subprocess**（沿用 `akshare_subprocess.py` / `akshare_spot_client.py` 的子进程 + 清代理 + JSON stdout 约定）**存在可行的真实期货与外汇候选接口**；但实际可用性受网络与上游接口稳定性影响，**尚未在本环境实跑确认**。因此将「可行性实跑验证」列为实现阶段**第一个任务（feasibility spike）**，未通过则按需求 8.2 标注 `unavailable`（**不得回退收盘价**）。

### 2.1 美股指数期货（需求 1.3 — 必须真实期货）

候选接口（已核对存在于 AkShare 源 `akshare/__init__.py`）：

| 优先级 | AkShare 接口 | 说明 | 口径 |
|--------|-------------|------|------|
| 主选 | `futures_global_em()` | 东财「全球指数期货」实时行情，含纳斯达克100期货 / 标普500期货 / 道琼斯期货 | **真实期货** ✅ |
| 备选 | `futures_foreign_commodity_realtime(symbol=...)` | 新浪外盘期货实时（symbol 取自 `futures_foreign_commodity_subscribe_exchange_symbol()`） | 真实期货 ✅ |
| 历史/校验 | `futures_global_hist_em()` / `futures_global_spot_em()` | 历史与现货，用于离线 fixture 与回归 | 真实期货 ✅ |

> 明确禁止：`index_us_stock_sina(".IXIC"/".INX"/".DJI")`、`stock_us_*` 等**指数/收盘**接口仅可用于「人类可读名称对照」，**不得**作为期货数值来源或降级回退（违反 1.3 / 7.5）。

品种映射（`futures_global_em` 返回的「名称」列做匹配，匹配关键字而非精确等值，以兼容上游命名差异）：

| 内部 symbol | 展示名 | 匹配关键字（任一命中） |
|-------------|--------|------------------------|
| `NASDAQ_FUT` | 纳指期货 | 「纳斯达克」+「期货」/「纳指」 |
| `SP500_FUT` | 标普期货 | 「标普」/「标普500」+「期货」 |
| `DOW_FUT` | 道指期货 | 「道琼斯」/「道指」+「期货」 |

### 2.2 USD/CNY 外汇（需求 1.2）

候选接口：

| 优先级 | AkShare 接口 | 说明 | 时效 |
|--------|-------------|------|------|
| 主选 | `fx_quote_baidu(symbol="美元")` | 百度外汇实时报价（USD/CNY 最新价 + 涨跌幅） | 实时 ✅ |
| 备选 | `currency_boc_sina(symbol="美元")` | 中行人民币牌价（折算价），取最新一行 | 日频 ⚠️ |
| 历史/校验 | `forex_hist_em()` / `currency_boc_safe()` | 历史与外管局口径，用于 fixture | 日频 |

> 若主选实时接口本环境不可用，可用中行牌价兜底（仍是真实汇率，非编造），并在 `data_source_status` 上标注其时效偏差；绝不填占位常量。

### 2.3 可行性实跑验证方案（实现阶段第一任务）

在 `apps/api/scripts/diagnose_us_market.py`（仿 `diagnose_sector_quotes.py`）中分别调用上述主选接口，断言：返回 DataFrame 非空、含目标品种行、数值列可转 `float`。三项任一不可用，则在设计/任务备注更新「降级矩阵」并对应数据项默认 `unavailable`。该脚本同时产出离线 fixture（JSON）供 pytest stub 使用（沿用 `tests/fixtures/` 约定）。

**风险登记：** 期货/外汇真实源在本环境的可达性是本特性最大风险；功能必须在「无真实数据」时仍优雅降级（需求 7），不得以任何收盘价或常量伪装可用。

### 2.4 QDII 列表来源与估算方法（需求 8.3）

**列表来源（混合，避免慢请求）：**

1. **种子清单（主）：** 在 `us_qdii_seeds.py` 维护一份对标竞品的 QDII 基金种子表，每条含 `fund_code` / `fund_name` / `tracking_target`（跟踪标的）/ `tracking_symbol`（映射到 §2.1 的内部期货 symbol，如 `NASDAQ_FUT`）/ `tracking_factor`（跟踪系数，默认 `1.0`）。涵盖纳指、标普500、道指及全球科技等主流跟踪方向。
2. **可选增量（次，本期不强制）：** AkShare QDII 分类（`fund_open_fund_rank_em` 过滤「QDII」或基金名含「纳斯达克/标普/美国/全球」关键字）扩充候选；本期默认仅用种子表以控制响应时延，留接口位。

> 与竞品口径一致：QDII 列表是**精选/种子清单**，不是交易所全量名单；文案需说明「精选跟踪美股/全球指数的 QDII」。

**`Reference_Change_Percent` 估算方法（需求 2.2 / 2.3）：**

```
ref% = round(premarket_change(tracking_symbol) × tracking_factor, 2)
```

- `premarket_change(tracking_symbol)`：该 QDII 跟踪标的对应**指数期货的盘前涨跌幅**（取自 §2.1 期货卡的同一 snapshot 数据，零额外请求）。
- `tracking_factor`：种子表中的跟踪系数（默认 1.0；用于近似杠杆/汇率/跟踪误差，可后续校准）。
- 若跟踪标的期货 `unavailable` 或无映射 → 该条 `reference_change_percent = None`，不参与编造。
- 每条携带 `estimate_basis`（如 `"基于纳指期货盘前涨跌估算，非实时净值"`），前端据此标注**非承诺性预估**。

---

## 3. 架构总览

```text
AkShare subprocess（清代理 + JSON stdout）
  ├─ us_futures_client.fetch_us_index_futures()   → futures_global_em
  └─ us_forex_client.fetch_usd_cny()              → fx_quote_baidu / currency_boc_sina
          ↓
us_market_session.detect_us_session(when?)        → US_Session_Kind（America/New_York, DST）
          ↓
us_market_service.get_us_market_snapshot(force_refresh)
   - 并行拉取期货 + 汇率（ThreadPoolExecutor，预算 ~10s）
   - 读 QDII 种子表 → 用期货盘前涨跌估算 reference_change_percent
   - 聚合 UsMarketSnapshot（含每源 Data_Source_Status + updated_at）
   - 时段感知 TTL + stale-while-revalidate
          ↓
sector_quote_cache（复用 spot 快照表）
   cache_key: market:us_overview:v1:{session_kind_bucket}:{et_date}
   TTL: 60s（pre_market/regular）/ 1800s（after_hours/closed）
          ↓
GET /api/market/us-overview?force_refresh=bool   (main.py)
          ↓
前端 UsMarketOverview（MarketTab 第三子 Tab「美股」）
   - useCachedFetch + session 缓存
   - 时段感知自动刷新（可见时；pre/regular 30–60s，其余更长）
   - 指标卡 + QDII 列表 + 时段标签 + 更新时间 + stale/unavailable 状态
```

### 3.1 新增 / 改动文件清单

| 层 | 文件 | 职责 |
|----|------|------|
| API 服务 | `apps/api/app/services/us_market_session.py`（新建） | `US_Session_Detector`：ET/DST 时段判定 |
| API 服务 | `apps/api/app/services/us_market_service.py`（新建） | snapshot 聚合、QDII 估算、时段 TTL、缓存读写、降级 |
| API 服务 | `apps/api/app/services/us_futures_client.py`（新建） | `futures_global_em` 子进程拉取 + 品种映射 |
| API 服务 | `apps/api/app/services/us_forex_client.py`（新建） | `fx_quote_baidu` / `currency_boc_sina` 子进程拉取 |
| API 数据 | `apps/api/app/services/us_qdii_seeds.py`（新建） | QDII 种子表（代码/名称/跟踪标的/symbol/系数/估算依据） |
| API 模型 | `apps/api/app/models.py`（改动） | `UsFuturesQuote` / `UsdCnyQuote` / `QdiiPremarketItem` / `UsMarketSnapshot` + `DataSourceStatus` / `UsSessionKind` |
| 路由 | `apps/api/app/main.py`（改动） | `GET /api/market/us-overview` |
| 诊断 | `apps/api/scripts/diagnose_us_market.py`（新建） | 数据源可行性 spike + fixture 产出 |
| 测试 | `apps/api/tests/test_us_market_session.py`（新建） | 时段/DST 边界 |
| 测试 | `apps/api/tests/test_us_market_service.py`（新建） | snapshot 结构、降级、估算、TTL |
| 测试 | `apps/api/tests/test_api.py` + `conftest.py`（改动） | smoke + 网络 stub |
| 前端 | `apps/web/src/lib/api.ts`（改动） | 类型 + `fetchUsMarketOverview` |
| 前端 | `apps/web/src/lib/usMarketOverview.ts`（新建） | usable/acceptFresh、子 Tab 持久化扩展、刷新间隔与时段标签 helper |
| 前端 | `apps/web/src/components/UsMarketOverview.tsx`（新建） | 指标卡 + QDII 列表 + 状态渲染 |
| 前端 | `apps/web/src/components/MarketTab.tsx`（改动） | 新增「美股」子 Tab + 时段感知刷新 |
| 文档 | `docs/PROJECT_CONTEXT.md`（改动） | 能力清单 + API 表 |

---

## 4. 数据模型

### 4.1 后端 Pydantic 模型（`models.py`）

```python
DataSourceStatus = Literal["ok", "stale", "unavailable"]
UsSessionKind = Literal["pre_market", "regular", "after_hours", "closed"]


class UsFuturesQuote(BaseModel):
    symbol: str                       # NASDAQ_FUT | SP500_FUT | DOW_FUT
    display_name: str                 # 纳指期货 / 标普期货 / 道指期货
    last_price: float | None = None   # status != ok 时可为 None（禁止占位值）
    change_percent: float | None = None
    quote_time: str | None = None     # 数据时间戳（ISO，源采集时刻）
    status: DataSourceStatus = "unavailable"


class UsdCnyQuote(BaseModel):
    last_price: float | None = None
    change_percent: float | None = None
    quote_time: str | None = None
    status: DataSourceStatus = "unavailable"


class QdiiPremarketItem(BaseModel):
    fund_code: str
    fund_name: str
    tracking_target: str              # 跟踪标的（如「纳斯达克100」）
    tracking_symbol: str | None = None  # 映射到期货 symbol
    reference_change_percent: float | None = None
    estimate_basis: str | None = None  # 非承诺性预估说明


class UsMarketSnapshot(BaseModel):
    session_kind: UsSessionKind
    session_label: str                # 盘前交易中 / 盘中 / 盘后 / 休市
    et_date: str                      # 美东日期
    updated_at: str                   # 采集时刻 ISO 时间戳（需求 4.6）
    futures: list[UsFuturesQuote]     # 固定 3 条
    usd_cny: UsdCnyQuote
    qdii: list[QdiiPremarketItem]
    qdii_status: DataSourceStatus     # QDII 列表整体状态
    futures_status: DataSourceStatus  # 期货整体状态（任一可用即 ok/stale）
    forex_status: DataSourceStatus
    available: bool                   # 任一数据源可用即 True
    from_cache: bool = False
    stale: bool = False
    message: str | None = None
```

> 关键不变量（需求 7.5）：`last_price` / `change_percent` / `reference_change_percent` 仅在数据**真实采集成功**（`status == "ok"`）或**为上次真实缓存值**（`status == "stale"`）时才有值；`status == "unavailable"` 时一律为 `None`。任何来自收盘价或占位常量的替代值都**禁止**写入这些字段。

### 4.2 前端类型（`api.ts`）

```ts
export type UsDataSourceStatus = "ok" | "stale" | "unavailable";
export type UsSessionKind = "pre_market" | "regular" | "after_hours" | "closed";

export interface UsFuturesQuote {
  symbol: string;
  display_name: string;
  last_price?: number | null;
  change_percent?: number | null;
  quote_time?: string | null;
  status: UsDataSourceStatus;
}
export interface UsdCnyQuote { /* last_price / change_percent / quote_time / status */ }
export interface QdiiPremarketItem {
  fund_code: string;
  fund_name: string;
  tracking_target: string;
  reference_change_percent?: number | null;
  estimate_basis?: string | null;
}
export interface UsMarketSnapshot {
  session_kind: UsSessionKind;
  session_label: string;
  et_date?: string | null;
  updated_at?: string | null;
  futures: UsFuturesQuote[];
  usd_cny: UsdCnyQuote;
  qdii: QdiiPremarketItem[];
  qdii_status: UsDataSourceStatus;
  futures_status: UsDataSourceStatus;
  forex_status: UsDataSourceStatus;
  available: boolean;
  from_cache?: boolean;
  stale?: boolean;
  message?: string | null;
}
```

---

## 5. API 设计

### `GET /api/market/us-overview`

| 参数 | 默认 | 说明 |
|------|------|------|
| `force_refresh` | `false` | 跳过服务端缓存重新聚合（需求 4.5） |

- **命名空间：** 落在 `GET /api/market/*`（需求 4.2），与 `sector-boards` / `theme-boards` 并列。
- **鉴权：** 无需 JWT（与其它 market 接口一致）。
- **响应：** `UsMarketSnapshot.model_dump(mode="json")`，HTTP 200。
- **降级语义：** 任何数据源失败都返回 200（不抛 5xx），通过各 `*_status` + `available` + `stale` + `message` 表达（需求 7）。

**示例响应（盘前，期货 ok / 汇率 stale / 部分 QDII 估算）：**

```json
{
  "session_kind": "pre_market",
  "session_label": "盘前交易中",
  "et_date": "2026-06-17",
  "updated_at": "2026-06-17T08:12:30-04:00",
  "futures": [
    { "symbol": "NASDAQ_FUT", "display_name": "纳指期货", "last_price": 19850.5, "change_percent": 0.62, "quote_time": "2026-06-17T08:12:00-04:00", "status": "ok" },
    { "symbol": "SP500_FUT", "display_name": "标普期货", "last_price": 5510.25, "change_percent": 0.41, "quote_time": "2026-06-17T08:12:00-04:00", "status": "ok" },
    { "symbol": "DOW_FUT", "display_name": "道指期货", "last_price": 40120.0, "change_percent": 0.28, "quote_time": "2026-06-17T08:12:00-04:00", "status": "ok" }
  ],
  "usd_cny": { "last_price": 7.245, "change_percent": -0.05, "quote_time": "2026-06-16T16:00:00-04:00", "status": "stale" },
  "qdii": [
    { "fund_code": "270042", "fund_name": "广发纳斯达克100", "tracking_target": "纳斯达克100", "reference_change_percent": 0.62, "estimate_basis": "基于纳指期货盘前涨跌估算，非实时净值" }
  ],
  "futures_status": "ok",
  "forex_status": "stale",
  "qdii_status": "ok",
  "available": true,
  "from_cache": false,
  "stale": true,
  "message": "汇率数据更新失败，展示上次缓存值"
}
```

---

## 6. 核心组件设计

### 6.1 `us_market_session.py`（US_Session_Detector）

仿 `trading_session.py`，以 `ZoneInfo("America/New_York")` 自动处理夏令时（DST）。

```python
US_TZ = ZoneInfo("America/New_York")
PRE_MARKET_OPEN  = time(4, 0)
REGULAR_OPEN     = time(9, 30)
REGULAR_CLOSE    = time(16, 0)
AFTER_HOURS_CLOSE = time(20, 0)

def detect_us_session(when: datetime | None = None) -> dict:
    moment = (when or datetime.now(US_TZ)).astimezone(US_TZ)
    et_date = moment.date()
    if not _is_us_trading_day(et_date):
        kind = "closed"
    else:
        t = moment.time()
        if REGULAR_OPEN <= t < REGULAR_CLOSE:
            kind = "regular"
        elif PRE_MARKET_OPEN <= t < REGULAR_OPEN:
            kind = "pre_market"
        elif REGULAR_CLOSE <= t < AFTER_HOURS_CLOSE:
            kind = "after_hours"
        else:
            kind = "closed"
    return {"session_kind": kind, "session_label": _LABELS[kind], "et_date": et_date.isoformat()}
```

时段中文标签：`pre_market→盘前交易中`、`regular→盘中`、`after_hours→盘后`、`closed→休市`。

#### 6.1.1 美股交易日 / 节假日（务实简化，需求 8 显式标注）

- **本期 MVP：** `_is_us_trading_day` 仅排除**周末**（周六/周日，按 ET 墙钟日期判定）。美股法定节假日（如感恩节、独立日）本期**不接入权威日历**。
- **理由与影响：** 节假日当天会被误判为 `pre_market/regular/after_hours`；但由于数据源在休市时**自然返回空/陈旧数据**，降级逻辑（§6.2）会将其标为 `stale/unavailable`，不会编造数值，用户仍看到「数据未更新」而非错误数值。
- **后续增强（非本期）：** 可接入静态节假日表或 `pandas-market-calendars`（NYSE 日历）精确判定 `closed`。该简化在设计与任务中显式登记为已知限制。

### 6.2 `us_market_service.py`（聚合 + 降级 + 缓存）

沿用 `sector_board_snapshot.py` / `theme_board_snapshot.py` 的：`build_trading_session→TTL→cache_key→get_spot_snapshot/any_age→fetch→save` 主线，复用 `sector_quote_cache`。

```python
_LIVE_TTL = 60.0       # pre_market / regular
_CLOSED_TTL = 1800.0   # after_hours / closed
_CACHE_VERSION = "v1"

def _ttl_for(kind: str) -> float:
    return _LIVE_TTL if kind in {"pre_market", "regular"} else _CLOSED_TTL

def get_us_market_snapshot(*, force_refresh: bool = False) -> dict:
    session = detect_us_session()
    kind = session["session_kind"]
    bucket = "live" if kind in {"pre_market", "regular"} else "rest"
    cache_key = f"market:us_overview:{_CACHE_VERSION}:{bucket}:{session['et_date']}"

    if not force_refresh:
        cached = get_spot_snapshot(cache_key, ttl_seconds=_ttl_for(kind))
        if cached and cached.get("available"):
            return {**cached, "from_cache": True, "stale": False}

    prev = get_spot_snapshot_any_age(cache_key)  # 用于 stale 回退与「最后真实值」

    futures = _fetch_futures_with_fallback(prev)   # 每源 ok / stale / unavailable
    usd_cny = _fetch_forex_with_fallback(prev)
    qdii = _build_qdii_items(futures)              # 用期货盘前涨跌估算

    snapshot = _assemble(session, futures, usd_cny, qdii, prev)
    if snapshot["available"]:
        save_spot_snapshot(cache_key, snapshot)
    return snapshot
```

**降级与「最后真实值」策略（需求 7.1 / 7.2 / 7.5）：**

| 本次采集 | 历史缓存（真实值） | 结果 `status` | 数值 |
|----------|--------------------|---------------|------|
| 成功 | — | `ok` | 真实最新值 |
| 失败 | 有 | `stale` | **沿用缓存中的最后真实值** + `quote_time` 为旧时间 |
| 失败 | 无 | `unavailable` | `None`（省略，禁止占位） |

`available = futures_status != unavailable OR forex_status != unavailable OR qdii_status != unavailable`。QDII：当其全部跟踪期货均不可用且无缓存 → `qdii_status = unavailable` 且 `qdii = []`（需求 2.5）。

### 6.4 数据源 client（子进程约定）

`us_futures_client.py` / `us_forex_client.py` 完全复用 `akshare_subprocess.py` 的写法：`subprocess.run([sys.executable, "-c", script], timeout=60)`、子进程内清代理（`NO_PROXY=*`、剔除 `*proxy*`/`REQUESTS_CA_BUNDLE` 等）、`print(json.dumps(...))`、异常一律返回 `None`/空。返回结构示例：

```python
# us_futures_client.fetch_us_index_futures() -> list[dict] | None
[{"symbol": "NASDAQ_FUT", "display_name": "纳指期货", "last_price": 19850.5,
  "change_percent": 0.62, "quote_time": "2026-06-17T08:12:00-04:00"}]
```

---

## 7. 前端设计

### 7.1 `MarketTab.tsx` —— 第三子 Tab「美股」

- `MarketSubTab` 由 `"market" | "themes"` 扩展为 `"market" | "themes" | "us"`（在 `usMarketOverview.ts` 重新导出，或就地扩展 `marketThemeBoard.ts` 的类型）。
- segment 增加按钮「美股」，与「全市场 | 主题板块」并列（需求 6.1 / 6.2）。
- `us` 子 Tab 内渲染：`TradingSessionBar` 之外**额外**渲染美股时段标签（CN 的 `TradingSessionBar` 是 A 股口径，美股时段由 snapshot 的 `session_label` 单独展示，避免口径混淆）。
- `useCachedFetch`：`cacheKey = market-us-overview`，`storage = "session"`，`staleTimeMs` 随时段动态（见 7.3），`enabled: subTab === "us"`（需求 5.3 不可见暂停；`enabled` 关闭即不请求/不轮询）。
- `keepPreviousUnless: acceptUsMarketFresh`（仅当新数据 `available` 时替换，保留 stale-while-revalidate）。

### 7.2 `UsMarketOverview.tsx`

渲染顺序（需求 6.3）：时段标签 → 期货 + 汇率指标卡 → QDII 列表 → 更新时间。

- **指标卡（4 张）：** 3 期货 + USD/CNY，复用 `SectorPerformanceCard` 的 tile/`profit-up`/`profit-down`/`tabular-nums` 风格。
  - `status === "ok"`：展示最新价 + `+x.xx%`（红涨绿跌）。
  - `status === "stale"`：展示数值 + 角标「上次 {quote_time}」（需求 7.3）。
  - `status === "unavailable"`：展示「暂不可用」占位，**不渲染任何数值**（需求 7.4）。
- **QDII 列表：** 表格列「基金名称 / 跟踪标的 / 盘前参考涨跌」（需求 2.4）；`reference_change_percent == null` 显示 `—`；底部固定免责文案（来自 `estimate_basis`）：「参考涨跌基于盘前指数期货估算，非实时净值/承诺收益」（需求 2.3）。
- **加载态：** 无可用缓存且 `loading` 时展示加载指示（需求 6.4）。
- **更新时间：** 显著位置展示 `updated_at`（本地化），并按 `stale`/`from_cache` 加后缀（需求 6.5），复用 MarketTab 底部 footer 模式。

### 7.3 时段感知刷新（需求 5）

`usMarketOverview.ts` 提供：

```ts
export function usRefreshIntervalMs(kind?: UsSessionKind): number {
  return kind === "pre_market" || kind === "regular" ? 45_000 : 300_000; // 盘前/盘中 45s；其余 5min
}
export const US_SESSION_LABEL: Record<UsSessionKind, string> = {
  pre_market: "盘前交易中", regular: "盘中", after_hours: "盘后", closed: "休市",
};
```

- `MarketTab` 用 `useEffect` + `setInterval(refresh, usRefreshIntervalMs(data?.session_kind))`，依赖 `[subTab, data?.session_kind]`；`subTab !== "us"` 或 `document.hidden` 时清除定时器（需求 5.1 / 5.2 / 5.3）。
- `staleTimeMs` 同步取 `usRefreshIntervalMs(...)`，使 `useCachedFetch` 的新鲜度与轮询节奏一致（需求 5.4）。

---

## 8. 时序流程

```text
用户切到「美股」子 Tab
  → useCachedFetch 读 session 缓存（命中且新鲜：立即渲染，<100ms）
  → 否则 fetchUsMarketOverview() → GET /api/market/us-overview
        → get_us_market_snapshot(force_refresh=False)
             → detect_us_session()                    # 时段 + TTL bucket
             → 命中服务端缓存且 available → 直接返回 (from_cache)
             → 未命中：并行拉期货 + 汇率
                  ├─ 成功 → status=ok
                  └─ 失败 → 有缓存:status=stale(用最后真实值) / 无:unavailable
             → 用期货盘前涨跌估算 QDII reference_change_percent
             → 组装 snapshot；available 才写缓存
  → 渲染指标卡 / QDII / 时段标签 / 更新时间
  → 启动时段感知轮询（pre/regular 45s；其余 5min；不可见暂停）
强制刷新（用户点刷新）：force_refresh=true → 绕过缓存重聚合
```

---

## 9. 错误处理

| 场景 | 处理 | 需求 |
|------|------|------|
| 单个期货品种缺失 | 该条 `status=unavailable`，其余正常，`futures_status` 取整体（任一 ok 即 ok） | 1.5 |
| 期货整源失败有缓存 | 全部期货 `status=stale`，沿用最后真实值 | 7.1 |
| 期货整源失败无缓存 | 全部期货 `status=unavailable`，数值 None | 7.2 |
| 汇率失败 | 同上 stale/unavailable，绝不回退收盘价/常量 | 1.5 / 7.5 |
| QDII 跟踪期货不可用 | 该条 `reference_change_percent=None`；全不可用且无缓存 → `qdii_status=unavailable`，`qdii=[]` | 2.5 |
| 全部数据源失败且无缓存 | 200 + `available=false` + `message`，前端展示不可用态 | 7.4 |
| 子进程超时 / JSON 解析失败 | client 返回 None，按「采集失败」走降级 | 7.x |
| 节假日误判为交易时段 | 数据源自然空/陈旧 → stale/unavailable，不编造 | 6.1.1 / 7.5 |

---

## 10. Correctness Properties

*属性是对系统在所有合法执行下都应成立的行为的形式化陈述，是人类规格与可机器验证正确性之间的桥梁。*

### Property 1: 时段划分完备且互斥

*For any* 美东时刻（任意 UTC 瞬时换算到 America/New_York 墙钟时间），`US_Session_Detector` 返回的 `US_Session_Kind` 恰为 `pre_market`/`regular`/`after_hours`/`closed` 之一，且与时间窗口规则一致：交易日 09:30–16:00 ET→`regular`、04:00–09:30 ET→`pre_market`、16:00–20:00 ET→`after_hours`、其余→`closed`。

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

### Property 2: 夏令时墙钟一致性

*For any* 两个分别落在夏令时与标准时但具有相同美东**墙钟时间**的瞬时，`US_Session_Detector` 返回相同的 `US_Session_Kind`（即判定基于 DST 感知的 ET 墙钟，而非固定 UTC 偏移）。

**Validates: Requirements 3.1**

### Property 3: 非交易日恒为休市

*For any* 落在周末（或后续接入的节假日）的美东时刻，`US_Session_Detector` 返回 `closed`。

**Validates: Requirements 3.5**

### Property 4: 时段感知 TTL 单调性

*For any* `US_Session_Kind`，缓存有效期满足 `ttl(pre_market) == ttl(regular) ≤ 60s < ttl(after_hours) ≤ ttl(closed)`。

**Validates: Requirements 4.3, 4.4**

### Property 5: 禁止编造数值（核心安全不变量）

*For any* 聚合产出的 `US_Market_Snapshot`，其中任一报价条目（期货 / USD_CNY / QDII 参考涨跌）：当其 `Data_Source_Status == "ok"` 时数值来自本次真实采集；当 `== "stale"` 时数值等于该源上一次真实采集缓存值；当 `== "unavailable"` 时数值字段为 `None`。在任何情形下，数值字段都不等于由指数收盘价或占位常量推导的替代值。

**Validates: Requirements 1.5, 2.5, 7.5**

### Property 6: 采集失败且有历史 → 陈旧回退

*For any* 本次采集失败但缓存中存在该数据源历史真实值的情形，该数据源的 `Data_Source_Status` 为 `stale`，且返回数值等于其最后一次真实采集值。

**Validates: Requirements 7.1**

### Property 7: 采集失败且无历史 → 不可用

*For any* 本次采集失败且无任何历史缓存的数据源，其 `Data_Source_Status` 为 `unavailable` 且数值字段被省略（QDII 此时返回空列表）。

**Validates: Requirements 1.5, 2.5, 7.2**

### Property 8: QDII 参考涨跌估算

*For any* QDII 种子条目，若其映射的跟踪期货盘前涨跌为 `c` 且跟踪系数为 `k`，则 `reference_change_percent == round(c × k, 2)` 且 `estimate_basis` 非空；若跟踪期货不可用或无映射，则 `reference_change_percent` 为 `None`。

**Validates: Requirements 2.2, 2.3**

---

## 11. 测试策略

**双轨测试：** 单测覆盖具体示例/边界/错误；属性测试覆盖普遍属性（≥100 次迭代，标注 `Feature: us-market-overview, Property N: ...`，引用 §10）。Python 用 `hypothesis`（若仓库未引入，则以参数化 + 多随机样本近似，并在任务中标注）。

### 11.1 后端（pytest，网络全 stub）

`conftest.py` 的 `_stub_market_data_fetches` 增补：`monkeypatch` 掉 `us_futures_client.fetch_us_index_futures`、`us_forex_client.fetch_usd_cny`（默认返回 fixture / None），并对 `app.main.get_us_market_snapshot` 提供确定性 stub 供 API smoke 用（沿用既有 sector/theme stub 模式）。

| 测试文件 | 覆盖 | 需求 |
|----------|------|------|
| `test_us_market_session.py` | Property 1/2/3：盘前/盘中/盘后/休市边界（含 09:30、16:00、20:00 边界）、DST 切换日（3 月/11 月）、周末→closed | 9.1 |
| `test_us_market_service.py` | Property 5/6/7：各源成功/stale/unavailable 的 snapshot 结构与 `*_status`；Property 4：TTL 选择；Property 8：QDII 估算；force_refresh 重聚合（4.5）；snapshot 结构完整（4.1/4.6） | 9.2, 9.3 |
| `test_api.py`（增补） | smoke：`GET /api/market/us-overview` 200 + 关键字段；`force_refresh=true` 路径 | 9.2 |

边界用例：09:29:59→pre_market、09:30:00→regular、15:59:59→regular、16:00:00→after_hours、19:59:59→after_hours、20:00:00→closed；DST「春进」3 月第二个周日 02:00→03:00、「秋退」11 月第一个周日。

### 11.2 前端（lint / typecheck / build + 组件渲染）

- `npm run lint`、`tsc --noEmit`、`npm run build` 通过（需求 9.4）。
- `UsMarketOverview` 渲染断言：`loading`（无缓存）、`ok`（展示数值）、`stale`（展示数值 + 旧时间角标）、`unavailable`（不渲染数值、展示占位）三态；QDII 免责文案存在；时段标签按 `session_kind` 正确。
- `usRefreshIntervalMs` 单测：pre_market/regular → 短间隔；after_hours/closed → 长间隔（需求 5.1/5.2）。

### 11.3 性能预算

| 场景 | 目标 |
|------|------|
| 服务端缓存命中 | API < 50ms |
| 冷启动（期货 + 汇率并行） | ≤ 10s；用户先见 loading / 陈旧缓存 |
| 前端 session 缓存命中 | 切换子 Tab < 100ms |

---

## 12. 验收标准

1. 市场 Tab 出现第三子 Tab「美股」，与「全市场 | 主题板块」并列切换正常，刷新后记住上次子 Tab。
2. 顶部展示纳指/标普/道指**期货**与 USD/CNY 四张指标卡；数据源不可用时展示「暂不可用」而非数值，且不回退收盘价。
3. QDII 列表展示名称/跟踪标的/盘前参考涨跌，并含「非承诺性预估」免责说明。
4. 时段标签随美东时段（含夏令时）正确切换；盘前/盘中高频刷新、休市低频，不可见暂停。
5. 某数据源失败时展示陈旧/不可用提示与采集时间，绝无编造数值。
6. 设计文档含数据源可行性结论、候选 AkShare 接口、验证方案与风险（本文件 §2）。
7. `pytest` 全绿（含时段/降级/TTL/估算覆盖）；`web` lint / typecheck / build 通过。
8. `docs/PROJECT_CONTEXT.md` 同步能力清单与 API 表。
