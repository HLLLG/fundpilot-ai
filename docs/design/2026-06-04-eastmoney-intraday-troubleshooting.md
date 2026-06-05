# 东财 push2 分时与指数映射（2026-06-04，持续更新）

> 供换机自测、后续改 `eastmoney_trends_client` / `sector_canonical` 时查阅。

## 问题现象

- 基金 **025856**（华夏中证电网设备主题 ETF 联接 A）关联板块折线显示「暂无分时数据」。
- 基金 **519674**（银河创新成长混合 A）关联板块显示「暂无分时数据」。
- 基金 **015945**（易方达国防军工混合 C）关联板块显示错误数值（+0.01% 而非 +3.33%）。
- 与养基宝对比：Y 轴尺度、收盘涨跌与右上角数字不一致。
- 浏览器打开 [quote.eastmoney.com](https://quote.eastmoney.com/) 正常，但 Console 大量 `push2` 报错。

## 根因结论（已确认）

### 1. 指数代码映射错误（已修）

| 代码 | 指数 | 基金关联 |
|------|------|----------|
| **931994** | 中证电网设备**主题** | 025856 官方跟踪标的 |
| **931151** | 中证**光伏**产业 | 误用，已纠正 |
| **930713** | CS 人工智 | 008586 华夏人工智能联接 |
| **931865** | 中证半导体 | 519674 银河创新成长 |
| **BK0963** | 商业航天（概念板块） | 015945 易方达国防军工，无中证指数 |

- `2.{code}` 是中证指数分时拉取的正确前缀；`1.` 前缀 404。
- 015945 关联「商业航天」走 `90.BK0963`，优先 `trends2` + AkShare 概念分钟。
- 旧链 `zs931151.html` 会 404，且代码本身不是电网设备主题。

### 2. push2 主域被掐（已修：新增 push2delay）

所有 `push2his`、`push2` 子域在部分网络环境下返回 `RemoteDisconnected`（HTTP:000）。

**解决方案：** 在 `_KLINE_URLS` 和 `_TRENDS2_URLS` 首位加入 `push2delay.eastmoney.com`，这是东财的 CDN 延迟节点，在 push2 主域不可用时仍可正常响应：

```python
# eastmoney_trends_client.py
_KLINE_URLS = (
    "https://push2delay.eastmoney.com/api/qt/stock/kline/get",  # 首选
    "https://push2his.eastmoney.com/api/qt/stock/kline/get",
    ...
)
_TRENDS2_URLS = (
    "https://push2delay.eastmoney.com/api/qt/stock/trends2/get",  # 首选
    "https://push2his.eastmoney.com/api/qt/stock/trends2/get",
    ...
)
```

浏览器脚本 `sector-intraday-browser-command.mjs` 的 `hosts` 数组同步更新：
```javascript
const hosts = ["push2delay.eastmoney.com", "push2his.eastmoney.com", ...];
```

### 3. percent 值为小数形式而非百分比（已修：两处防御）

**现象：** 缓存或实时返回的 `percent` 值为 `-0.0188`（小数形式），而非 `-1.88`（百分比），导致图表 Y 轴缩放到 ±0.05% 范围、收盘数值显示为 `-0.02%`。

**根因：** kline API 在仅请求当日数据（`beg=end=trade_date`）时，`preKPrice` 字段常为 0 或缺失，代码回退至原始 f59 字段（`change_pct`）。该字段在部分 kline 响应中以小数形式返回（-0.0188 表示 -1.88%）。

**修复 1（上游）：** `_kline_beg_end` 改为往前取 7 天，使 klines 包含昨日收盘行，`_prior_close_from_klines` 即可正确解析，不再依赖 f59：

```python
def _kline_beg_end(trade_date):
    end_ymd = trade_date.replace("-", "")
    beg_ymd = (date.fromisoformat(trade_date) - timedelta(days=7)).strftime("%Y%m%d")
    return beg_ymd, end_ymd
```

**修复 2（下游防御）：** `sector_intraday_provider` 写缓存前检测：若所有 `|percent| < 0.1`，说明值为小数形式，**直接丢弃整批数据并触发 stale 缓存回退**，而不是写入脏缓存或返回错误数据：

```python
if max_abs < 0.1:
    logger.warning("fraction form detected, discarding")
    points = []   # 触发后续 stale cache 回退
```

stale 缓存回退也同样校验，防止旧的脏数据被当作有效备份返回。

### 4. 骨架点污染缓存（已修）

东财 kline 偶发只返回 2 个骨架点（09:31 开盘 + 15:00 收盘）。原代码会缓存并在下次当作有效 stale 数据使用。

**修复：** 引入 `_MIN_INTRADAY_POINTS_TO_CACHE = 30`，低于此阈值的数据既不写缓存，也不用作 stale 回退。

### 5. 分时语义（养基宝对齐）

- **折线**：相对 **昨收**（`data.preKPrice`），与养基宝一致：开盘约 -0.8%、收盘与右上角日涨跌一致；不再以当日开盘为 0 轴。
- **右上角涨跌**：相对昨收的板块日涨跌（持仓刷新链路），与 tooltip 可略有不同（养基宝亦如此）。
- **Y 轴**：`max(|最高|, |最低|)` 相对 0 对称，不跳过开盘点。

### 6. 网络诊断背景

- 主站 `quote.eastmoney.com` 与 API 子域 **线路独立**；主站能开 ≠ push2 稳定。
- 浏览器 Console 典型错误：**`net::ERR_EMPTY_RESPONSE`** — 连接建立后对端未返回 HTTP 体即断开。
- 后端 Python 同类错误：`RemoteDisconnected` / `Connection aborted`。

## 换机自测清单

1. **浏览器**  
   打开 <https://quote.eastmoney.com/zz/2.931994.html>，F12 → Network 过滤 `kline`，确认 `secid=2.931994` 返回含 `klines` 的 JSON。

2. **命令行验证**

```powershell
# 验证三只关键基金（PowerShell 用 curl.exe）
curl.exe -s "http://127.0.0.1:8000/api/sector-quotes/intraday?source_type=index&source_name=%E4%B8%AD%E8%AF%81%E7%94%B5%E7%BD%91%E8%AE%BE%E5%A4%87&force_refresh=true"
curl.exe -s "http://127.0.0.1:8000/api/sector-quotes/intraday?source_type=index&source_name=%E4%B8%AD%E8%AF%81%E5%8D%8A%E5%AF%BC%E4%BD%93&force_refresh=true"
curl.exe -s "http://127.0.0.1:8000/api/sector-quotes/intraday?source_type=concept&source_name=%E5%95%86%E4%B8%9A%E8%88%AA%E5%A4%A9&force_refresh=true"
```

期望：每个响应 `points` 长度 ≥ 200，`close_change_percent` 绝对值 > 0.1（非小数形式）。

3. **前端验证**  
   重启 API（`bash scripts/dev.sh`）→ 硬刷新 → 逐一打开 025856、519674、015945 → 关联板块 tab 收盘涨跌应与养基宝一致（误差 ≤ 0.02%）。

4. **pytest**

```bash
cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_sector_intraday.py tests/test_eastmoney_trends_client.py tests/test_infer_intraday_index.py -q
```

## 脏缓存清理

若发现数值偏小（如 -0.02% 而非 -1.88%），说明数据库中存有小数形式的脏数据：

```python
import sqlite3, json
conn = sqlite3.connect('data/app.db')
cur = conn.cursor()
cur.execute("SELECT cache_key, payload FROM sector_spot_cache WHERE cache_key LIKE 'intraday:v2:%'")
for key, val in cur.fetchall():
    pts = json.loads(val).get('points', [])
    if pts and max(abs(p.get('percent', 0) or 0) for p in pts) < 0.1:
        cur.execute('DELETE FROM sector_spot_cache WHERE cache_key=?', (key,))
        print(f'Deleted: {key}')
conn.commit()
```

然后重启 API 或触发 `force_refresh=true`。

## 相关代码

| 文件 | 职责 |
|------|------|
| `apps/api/app/services/sector_canonical.py` | 指数名 → source_code / eastmoney_secid 映射 |
| `apps/api/app/services/eastmoney_trends_client.py` | push2delay/push2his kline+trends2 拉取、preKPrice 解析、日期范围 |
| `apps/api/app/services/sector_intraday_provider.py` | 分时缓存、骨架点过滤、小数形式检测、stale 回退 |
| `apps/web/scripts/sector-intraday-browser-command.mjs` | Playwright 浏览器兜底拉取 |
| `apps/web/src/components/IntradayPercentChart.tsx` | 对称 Y 轴、细线渲染 |
| `apps/web/src/components/YangjibaoFundDetail.tsx` | 关联板块 tab |

## 分时浏览器拉取（push2 全部掐断时）

当 Python/curl 访问所有 push2 域均为 `RemoteDisconnected`，但浏览器能打开 zz 页时：

1. `.env` 设置 `FUND_AI_SECTOR_QUOTES_BROWSER_ENABLED=true`
2. API 在直连东财失败后自动执行 `node scripts/sector-intraday-browser-command.mjs`（仅 `klt=1` / `trends2`，不用日 K）
3. 自测（Bash）：
   ```bash
   cd apps/web
   FUND_AI_INTRADAY_SECID=2.931865 FUND_AI_INTRADAY_SOURCE_CODE=931865 \
   FUND_AI_INTRADAY_TRADE_DATE=2026-06-05 node scripts/sector-intraday-browser-command.mjs
   ```
   期望 stdout JSON 中 `points.length` ≥ 200，`points` 末尾 percent 绝对值 > 0.1。

## 已修复提交记录

| commit | 说明 |
|--------|------|
| `c5eed02` | kline 稀疏时回落 trends2（`_MIN_RICH_INTRADAY_POINTS = 30`） |
| `238fa39` | 跳过 AkShare 不支持的中证 93xxxx 指数，避免静默空返回 |
| `ff7cbb3` | 新增 push2delay 为首选 host；骨架点不写缓存不作 stale 回退 |
| `548b953` | 小数形式 percent 拒绝写缓存（旧防御，仍会返回错误数据给前端） |
| `9906e2d` | kline 日期范围扩至 7 天前确保 preKPrice 可解析；小数形式数据直接丢弃并触发 stale 回退 |
