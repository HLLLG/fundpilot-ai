# 东财 push2 分时与指数映射（2026-06-04）

> 供换机自测、后续改 `eastmoney_trends_client` / `sector_canonical` 时查阅。

## 问题现象

- 基金 **025856**（华夏中证电网设备主题 ETF 联接 A）关联板块折线显示「暂无分时数据」。
- 与养基宝对比：Y 轴尺度、曲线形态、tooltip 与右上角日涨跌不一致。
- 浏览器打开 [quote.eastmoney.com](https://quote.eastmoney.com/) 正常，但 Console 大量 `push2` 报错。

## 根因结论（已确认）

### 1. 指数代码映射错误（必须修）

| 代码 | 指数 | 与 025856 关系 |
|------|------|----------------|
| **931994** | 中证电网设备**主题** | 官方跟踪标的（[华夏基金 025856](https://www.chinaamc.com/fund/025856/index.shtml)） |
| **931151** | 中证**光伏**产业 | 误用；东财页为 [光伏产业](https://quote.eastmoney.com/unify/r/2.931151) |

正确行情页：[电网设备主题 931994](https://quote.eastmoney.com/zz/2.931994.html)（`unify/r/2.931994` → `zz/2.931994.html`）。

旧链 `zs931151.html` 会 404，且代码本身不是电网设备主题。

**东财 API：** 分钟 K 线优先 `secid=2.931994`（`kline/get`），与 spot 用的 `0.xxx` 可能不同。

### 2. 分时语义（养基宝对齐）

- **折线**：相对 **当日开盘价**（首根分钟 K 的 `open`），不是 K 线第 8 列「相对昨收」累计涨跌幅。
- **右上角涨跌**：仍为 **相对昨收** 的板块日涨跌（持仓刷新链路），与 tooltip 数值可以不同（养基宝亦如此）。
- **Y 轴**：`max(|最高|, |最低|)` 相对 0 对称，不跳过开盘点。

### 3. push2 网络行为（非「普通电脑不能上东财」）

- 主站 `quote.eastmoney.com` 与 API 子域 `push2.eastmoney.com` **线路独立**；主站能开 ≠ push2 稳定。
- 浏览器 Console 典型错误：**`net::ERR_EMPTY_RESPONSE`** — 连接建立后对端 **未返回 HTTP 体** 即断开（无 Status Code）。
- 东财页 `zz2.js` 用 **`setInterval` 轮询** 多条 `ulist/get`（大盘、期指、成分股），并发高时 **部分成功、部分空响应**；与扩展无关（无痕也会出现；扩展拦截多为 `ERR_BLOCKED_BY_CLIENT`）。
- 后端 Python 同类错误：`RemoteDisconnected` / `Connection aborted`。
- 本仓库曾对每个指数尝试 `2./1./0./47.` × 多 host × 重试，易加剧限流；已改为 **优先 `2.{code}`、少 host、失败不写空缓存**。

### 4. 缓存

- 拉取失败时不应 `save_spot_snapshot` 空 `points`，否则会冲掉此前有效分时；失败时回退 7 日内有数据的缓存。

## 换机自测清单

1. **浏览器**  
   - 打开 <https://quote.eastmoney.com/zz/2.931994.html>  
   - F12 → Network 过滤 `kline`，确认 `secid=2.931994` 返回含 `klines` 的 JSON。  
   - 侧边 `ulist/get` 偶发 `ERR_EMPTY_RESPONSE` 可忽略，以 `kline` 为准。

2. **命令行（PowerShell 用 `curl.exe`）**

```powershell
curl.exe -s "http://127.0.0.1:8000/api/sector-quotes/intraday?source_type=index&source_name=%E4%B8%AD%E8%AF%81%E7%94%B5%E7%BD%91%E8%AE%BE%E5%A4%87&force_refresh=true"
```

期望：`points` 长度 ≥ 200，`session_date` 为最近交易日。

3. **前端**  
   - `bash scripts/dev.sh` 重启 API  
   - 硬刷新 → 025856 详情 → 关联板块 tab 有折线，图下说明「分时按当日开盘价…」。

4. **pytest**

```bash
cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_eastmoney_trends_client.py tests/test_infer_intraday_index.py -q
```

## 相关代码

| 文件 | 职责 |
|------|------|
| `apps/api/app/services/sector_canonical.py` | 中证电网设备 → `931994` / `2.931994` |
| `apps/api/app/services/eastmoney_trends_client.py` | push2 `kline/get`、开盘基准解析 |
| `apps/api/app/services/sector_intraday_provider.py` | 分时缓存、收盘后仍拉取 |
| `apps/web/src/components/IntradayPercentChart.tsx` | 对称 Y 轴、细线 |
| `apps/web/src/components/YangjibaoFundDetail.tsx` | 关联板块 tab |

## 待观察（换机后）

- 若 push2 仍不稳：考虑 AkShare `index_zh_a_hist_min_em` 子进程备用、或 `sector-relay` 部署。
- 若仅浏览器扩展环境异常：普通窗口加白 `*.push2.eastmoney.com`（`ERR_BLOCKED_BY_CLIENT` 时）。
