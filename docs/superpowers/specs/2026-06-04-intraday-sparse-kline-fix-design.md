# 分时图稀疏 K 线修复设计（2026-06-04）

## 背景

两个基金的详情页「关联板块」Tab 分时图异常：

| 基金 | 代码 | 现象 | 根因 |
|------|------|------|------|
| 银河创新成长混合A | 519674 | 「暂无分时数据」 | kline 接口返回 0 点，AkShare fallback 静默失败 |
| 华夏中证电网设备主题ETF联接A | 025856 | 只有一条斜线（仅2点） | kline 接口返回 2 个骨架点即被认定"成功"，trends2 未被调用 |

## 根因分析

### Bug 2（025856）— 稀疏骨架点被误判为成功

`fetch_eastmoney_intraday_trends()` 内部逻辑：

```
对每个 secid 候选:
  先试 kline → 拿到点 → 立即返回（只要 points 非空）
  再试 trends2（kline 为空才会执行）
```

东财 kline 接口有时会返回"骨架响应"：仅含 `09:31`（开盘点）和 `15:00`（收盘点）共 2 个点。
这 2 点满足 `if points:` 条件，代码认为"成功"并立即返回，绕过了能返回 240 个完整分钟点的 trends2 接口。

前端把 2 个点连成一条斜线，即用户看到的效果。

### Bug 1（519674）— AkShare 指数分钟 fallback 静默失败

`_fetch_index_intraday("中证半导体")` 流程：

1. `fetch_eastmoney_intraday_trends("2.931865")` → 返回 0 点
2. `_call_akshare_index_min("931865")` → 当前调用 AkShare `index_zh_a_hist_min_em(symbol="931865", period="1")`，但 AkShare 该接口要求上证指数格式（如 `000001`），对中证指数（9 开头 6 位）返回空 DataFrame → 静默失败
3. 返回空 points → 「暂无分时数据」

## 修复设计

### 改动 1：引入有效分时阈值（`eastmoney_trends_client.py`）

新增常量：

```python
_MIN_RICH_INTRADAY_POINTS = 30
```

修改 `fetch_eastmoney_intraday_trends()` 中对 kline 结果的判断：

**修改前（伪代码）：**
```python
points = _fetch_kline_intraday(...)
if points:
    return points  # 2 点也返回
points = _fetch_trends2_intraday(...)
if points:
    return points
```

**修改后（伪代码）：**
```python
kline_points = _fetch_kline_intraday(...)
trends2_points = []

if len(kline_points) >= _MIN_RICH_INTRADAY_POINTS:
    return kline_points  # 充足，直接用

# kline 返回稀疏（<30 点），继续尝试 trends2
trends2_points = _fetch_trends2_intraday(...)

# 取点数更多的结果；都为空则返回空
if len(trends2_points) >= len(kline_points):
    return trends2_points or kline_points
return kline_points or trends2_points
```

**注意：** 此逻辑在外层 `for candidate in _secid_candidates(...)` 循环内，每个 secid 候选仍然完整地走 kline+trends2 组合，只是调整了二者之间的取舍条件。如果某个候选组合的最终结果 >= 30 点则立即返回；不满足则继续下一个候选。

### 改动 2：修复 AkShare 中证指数分钟数据 fallback（`sector_intraday_provider.py`）

当前 `_call_akshare_index_min(symbol)` 使用 AkShare `index_zh_a_hist_min_em`，该接口仅支持上证/深证主流指数（如 `000001`、`399001`）。中证指数（`931865`、`931994`）**不在支持范围内**，调用会静默返回空。

修复策略：
- 在 `_fetch_index_intraday()` 中，如果 AkShare 调用返回空，添加 `logger.debug` 日志，方便后续排查
- **不再依赖** AkShare `index_zh_a_hist_min_em` 作为中证指数 fallback（因为它不支持）
- 保留 AkShare 调用作为上证/深证指数的 fallback，但对中证指数（code 以 `93` 开头）跳过此 fallback

### 改动 3：新增测试（`test_eastmoney_trends_client.py`）

新增两个测试：

1. **`test_sparse_kline_falls_through_to_trends2`**
   - kline 接口返回 2 个点（骨架）
   - trends2 接口返回 30+ 个点
   - 预期：`fetch_eastmoney_intraday_trends` 返回 trends2 的结果

2. **`test_rich_kline_does_not_call_trends2`**
   - kline 接口返回 240 个点
   - 预期：不调用 trends2，直接返回 kline 结果

## 不在本次范围内

- 前端 `IntradayPercentChart` X 轴午休间距调整（可独立迭代）
- sector-relay 中继服务部署
- Browser Command 链路配置（已有机制，不做改动）

## 验收标准

1. `pytest tests/test_eastmoney_trends_client.py -q` 全部通过（含新增 2 个测试）
2. 访问 `GET /api/sector-quotes/intraday?source_type=index&source_name=中证电网设备&force_refresh=true` → `points` 数量 ≥ 30
3. 025856 详情页「关联板块」Tab 显示完整分时折线（非斜线）
4. 519674 若东财仍不稳定则显示「数据源暂不可用」提示（而非静默空白），push2his 可用时有完整折线
