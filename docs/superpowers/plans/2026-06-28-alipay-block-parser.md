# 支付宝块解析 + 多策略择优 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 以块锚列无关解析为主路径、多策略评分为兜底，提升 OCR 结构化容错且保持纯本地毫秒级解析。

**Architecture:** 新增 `alipay_block_parser.py` 负责块切分/数字分类/策略编排；`parse_alipay_holdings_page` 委托 `parse_alipay_holdings_multi_strategy`。VLM 仍只做纯文本 OCR。

**Tech Stack:** Python 3.12, pytest, 现有 `alipay_holdings_parser` legacy 函数复用

---

## 已完成

- [x] 设计 spec：`docs/superpowers/specs/2026-06-28-alipay-block-parser-design.md`
- [x] `alipay_block_parser.py` — 块解析 + 评分 + 并集补漏
- [x] `parse_alipay_holdings_page` 改委托多策略
- [x] footer 截断防余额宝污染末块
- [x] `test_alipay_block_parser.py` + 全 fixture 回归（20 passed）

## 后续（可选）

- [ ] preview 响应 `holding_warnings` 增加低置信提示（识别数 < 锚点数）
- [ ] 「深度识别」按钮 + LLM 读 raw_text fallback（Phase 3）
