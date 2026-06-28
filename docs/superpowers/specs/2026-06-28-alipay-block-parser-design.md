# 支付宝 OCR 块解析 + 多策略择优

**日期：** 2026-06-28  
**状态：** 实现中  
**目标：** 在保持 VLM 纯文本 OCR（~3s）的前提下，提高本地结构化解析容错，减少「每遇新版式打补丁」。

## 背景

- VLM（qwen-vl-ocr）只做纯文本 OCR，不做 JSON 结构化（实测列错位）。
- 本地 `alipay_holdings_parser.py` 多路径 if-else 路由脆弱：页眉缺失、占比列位置变化、QDII 括号等导致漏识别。
- 用户要求：**快**（默认链路不加 LLM）+ **准**。

## 方案

### Phase 2 — 块锚 + 列无关（主路径）

1. 以 `looks_like_fund_product_name` / `COMPLETE_FUND_NAME_RE` 切 block。
2. 块内忽略标签行、占比行、footer/余额宝噪声。
3. 收集所有数字与「非占比」百分比，启发式分类：
   - 最大合理正数 → `holding_amount`
   - 其余按顺序 → 日收益 / 持有收益 / 累计收益
   - 最后一个非占比 `%` → `holding_return_percent`（若有）
4. 列顺序无关：兼容「金额→占比→三列收益」与「金额→三列→占比→收益率」。

### Phase 1 — 多策略并行 + 评分择优（兜底）

同时运行：

| 策略 | 说明 |
|------|------|
| `block_anchored` | 新版块解析（主） |
| `overview` | 原 `_parse_alipay_overview_holdings` |
| `name_anchored` | 原 `_parse_my_holdings_name_anchored` |
| `percent_blocks` | 原 percent 切块 |

评分维度：识别数量、基金名合法性、金额>0、是否含噪声词、与锚点数量差距。

若最高分结果仍少于锚点数，按基金名并集补漏（优先 block_anchored 字段）。

### 不做（本期）

- LLM 文本结构化 fallback（Phase 3，仅未来「深度识别」按钮）
- 更强 VLM 直出 JSON

## 文件

- 新增 `apps/api/app/services/alipay_block_parser.py` — 块解析 + 评分 + 编排
- 修改 `alipay_holdings_parser.py` — `parse_alipay_holdings_page` 委托编排器
- 测试：`test_alipay_block_parser.py` + 现有 fixture 全绿

## 验收

- 现有 `test_alipay_holdings_parser.py` 全通过
- `alipay_user_image1/2_vlm_ocr.txt` 回归
- `smoke_vlm_ocr.py` 对用户截图 6+2 只
