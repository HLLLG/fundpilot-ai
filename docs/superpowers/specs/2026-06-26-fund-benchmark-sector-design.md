# 基金业绩基准 → 关联板块自动解析

**日期：** 2026-06-26  
**状态：** 已落地（2026-06-26）

## 问题

指数型基金（如 021533 天弘半导体设备指数 C）跟踪 **中证半导体材料设备主题指数（931743）**，养基宝展示为「半导体材料」。好基灵因基金名含「半导体」走名称推断，落到泛概念「半导体」（BK1036 / 931865），涨跌与基金净值偏差大。

逐只 `GLOBAL_FUND_SECTOR_SEEDS` 不可扩展。

## 方案

```
fund_code → 拉取业绩比较基准文案（AkShare 雪球概况等）
         → 解析指数代码/名称（如 931743、中证半导体材料设备主题指数）
         → 指数主表 THEME_BOARD_INDEX（code → 展示板块名）
         → sector_name + intraday 走 canonical（931743）
```

### 解析优先级（`resolve_primary_sector`）

| 优先级 | source | 说明 |
|--------|--------|------|
| 100 | ocr_detail | 用户详情 OCR |
| 90 | alipay_overview | 档案/表沉淀 |
| 85 | manual | 手动 |
| 70 | holdings_infer | 季报重仓投票（主动基） |
| **65** | **benchmark_index** | **业绩基准/跟踪指数（新增）** |
| 60 | seed | 极少数兜底 |
| 20 | name_infer | 基金名子串（最后） |

命中 `benchmark_index` 时写入 `fund_primary_sectors` 缓存。

### 规则修复

- `get_canonical_sector`：标签**按长度降序**子串匹配，避免「半导体材料」命中「半导体」。
- 新增 canonical：**半导体材料** → `2.931743`（与主题榜一致）。

### 非目标

- 不改用户 OCR 已沉淀的板块（高优先级仍覆盖）。
- 本期不做全市场指数主表离线构建（复用 `THEME_BOARD_INDEX`）。

## 验收

- 021533 → `sector_name=半导体材料`，涨跌走 931743（6/25 约 +3.00%，非 +3.38%）。
- `get_canonical_sector("半导体材料")` → 931743，非 BK1036。
- 单测覆盖解析函数 + resolve 集成（mock 基准文案）。

## 落地记录

**实现文件：**

| 模块 | 路径 |
|------|------|
| 基准拉取与解析 | `apps/api/app/services/fund_benchmark_sector.py` |
| 解析优先级接入 | `apps/api/app/services/fund_primary_sector_service.py`（source=`benchmark_index`，优先级 65） |
| 子串匹配修复 | `apps/api/app/services/sector_canonical.py`（最长匹配优先） |
| 注册表补全 | `apps/api/app/services/sector_registry_data.py`（半导体材料 canonical） |
| 单测 | `apps/api/tests/test_fund_benchmark_sector.py` |

**关键实现细节：**

- `fetch_fund_benchmark_text` 经 AkShare 子进程拉雪球概况「业绩比较基准」；Windows 下子进程 stdout 须 `json.dumps(..., ensure_ascii=True)`，父进程 `json.loads` 解码，否则中文乱码导致解析失败。
- `parse_benchmark_index` 从文案提取指数代码（如 `931743`）或名称；`resolve_sector_from_benchmark` 映射 `THEME_BOARD_INDEX` → 展示板块名。
- `apply_primary_sector_to_holding`：命中 benchmark 时可覆盖 `alipay_overview` / `name_infer` / `seed` 的错误板块；高信任 OCR 沉淀仍优先。
- 不再依赖 per-fund `GLOBAL_FUND_SECTOR_SEEDS` 扩展指数型基金映射。

**验证：** 重启 API 后刷新 021533 持仓；板块应显示「半导体材料」，日内/图表跟 931743。

## 二次补强（2026-06-26）

| 问题 | 修复 |
|------|------|
| `alipay_overview` 板块挡住业绩基准 | 仅 `ocr_detail`/`manual` 为高信任；`resolve_holding` 与板块刷新前优先 `benchmark_index` |
| 涨跌仍走泛化「半导体」BK1036 | `sector_quote_lookup_label` 优先 canonical 指数标签；指数型基金清除名称推断板块后再解析 |
| AkShare 拉基准失败 | `_KNOWN_BENCHMARK_BY_CODE` 兜底（如 021533） |
| 支付宝名「半导体材料设备」查码失败 | `normalize_fund_name_for_lookup`：`半导体材料设备`→`半导体设备` |
