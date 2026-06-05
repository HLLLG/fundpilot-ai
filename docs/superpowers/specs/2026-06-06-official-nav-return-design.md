# 设计文档：基金官方净值涨幅替换板块估算涨幅

**日期：** 2026-06-06  
**状态：** 已审批

---

## 背景

当前系统从东财获取关联板块的分时涨幅作为基金当日表现的估算依据。板块涨幅（如商业航天 BK0963 +1.36%）与基金官方净值涨幅（如 015945 -2.45%）是两个不同口径的数据，在收盘后官方净值公布后应以官方净值为准。

---

## 目标

- 交易中：继续使用板块实时分时涨幅（现有逻辑不变）
- 收盘后（15:00）、官方净值未公布前：锁定并显示板块收盘涨幅，标注"收盘估算"
- 官方净值公布后（通常 ~21:00）：自动替换为官方净值涨幅，标注"官方净值"

---

## 数据流

```
用户上传截图 → OCR提取基金代码+板块名
    ↓
后端判断当前状态（按优先级）：
  1. 官方净值已公布（AkShare 返回今日净值日期）→ source: "official_nav"
  2. 交易中（09:30-15:00，交易日）              → source: "realtime"
  3. 其他（收盘后净值未公布、非交易日）          → source: "closing_estimate"
    ↓
后端返回统一响应结构，前端根据 source 字段显示标签
```

---

## Source 状态定义

| source | 含义 | 触发条件 |
|---|---|---|
| `realtime` | 板块实时分时涨幅（估算） | 交易日 09:30–15:00，官方净值未公布 |
| `closing_estimate` | 板块收盘涨幅（锁定估算） | 交易日 15:00 后，或非交易日，官方净值未公布 |
| `official_nav` | 官方基金净值涨幅（真实） | AkShare 返回的最新净值日期 == 当前交易日 |

**判断顺序：** official_nav 优先于其他两个状态。

---

## 后端实现

### 新增服务：`fund_nav_service.py`

职责：查询单只基金的官方 T 日净值涨幅。

```python
def get_official_nav_return(fund_code: str, trade_date: str) -> float | None:
    """
    返回 trade_date 当日的官方净值涨幅（%），未公布时返回 None。
    使用 AkShare fund_open_fund_info_em(fund=fund_code, indicator="单位净值走势")
    取最新一条记录：若净值日期 == trade_date，返回日增长率；否则返回 None。
    """
```

**缓存策略（SQLite sector_spot_cache 同库）：**

| 状态 | cache key | TTL |
|---|---|---|
| 净值未公布（返回 None） | `nav_return:{fund_code}:{trade_date}` | 5 分钟 |
| 净值已公布（返回 float） | `nav_return:{fund_code}:{trade_date}` | 24 小时 |

### 修改：`fund_profile.py` 或调用层

在构建基金详情响应时，对每个基金：
1. 调用 `get_official_nav_return(fund_code, today_trade_date)`
2. 若返回非 None → `change_percent = nav_return`, `source = "official_nav"`
3. 若返回 None，且当前时间在 09:30–15:00 交易时段 → `source = "realtime"`（现有逻辑）
4. 其他 → `source = "closing_estimate"`（现有板块收盘涨幅，锁定不再更新）

### 响应结构新增字段

现有 `change_percent` 字段含义扩展，新增 `change_percent_source`：

```json
{
  "change_percent": -2.45,
  "change_percent_source": "official_nav",
  "nav_return": -2.45
}
```

```json
{
  "change_percent": 1.36,
  "change_percent_source": "closing_estimate",
  "nav_return": null
}
```

---

## 前端实现

### 标签显示规则

| change_percent_source | 标签文字 | 标签样式 |
|---|---|---|
| `realtime` | `实时估算` | 灰色小标签 |
| `closing_estimate` | `收盘估算` | 灰色小标签 |
| `official_nav` | `官方净值` | 蓝色小标签 |

### Tooltip

- `official_nav`：tooltip 显示"已更新为基金官方公布净值涨幅"
- `closing_estimate`：tooltip 显示"基于板块收盘涨幅估算，官方净值公布后自动更新"
- `realtime`：tooltip 显示"基于关联板块实时涨幅估算"

### 自动刷新

前端现有的 `FUND_AI_SECTOR_QUOTES_AUTO_INTERVAL_SECONDS`（默认 120s）自动轮询已涵盖此功能，无需额外修改——轮询时后端会重新判断 source，净值公布后下一次轮询即自动替换。

---

## 边界情况

| 情况 | 处理方式 |
|---|---|
| 基金代码无法从 OCR 提取 | 跳过官方净值查询，使用现有板块逻辑，source = "realtime" / "closing_estimate" |
| AkShare 请求超时或报错 | 降级为 source = "closing_estimate"，不阻塞响应 |
| 非交易日（周末/节假日） | source = "closing_estimate"，使用最近交易日板块收盘涨幅 |
| 基金当日停牌或净值未更新 | AkShare 返回日期 != 今日 → source = "closing_estimate" |

---

## 不在本次范围内

- 批量基金净值预加载（当前按需查询，有缓存）
- WebSocket 实时推送净值公布事件
- 历史净值走势图表

---

## 影响范围

| 层 | 改动 |
|---|---|
| 后端 | 新增 `fund_nav_service.py`；修改基金详情构建逻辑；扩展响应结构 |
| 前端 | `YangjibaoFundDetail.tsx` 或对应组件：读 `change_percent_source` 显示标签+tooltip |
| 测试 | 新增 `test_fund_nav_service.py`；更新现有基金详情相关测试 |
