# Fund Discovery V2.0 + V2.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 合并交付 V2.0（历史、角色 Prompt、候选池面板、sector 错误态）与 V2.1（详情跳转、基金类型偏好、推荐复盘）。

**Architecture:** 后端 mirror 日报 `analysis-prompt` / `report_diff` / `recommendation_outcomes` 模式；前端扩展 `FundDiscoveryPanel` 布局（扫描区 + HistoryRail + 报告区），复用 `RolePromptEditor` 与 `YangjibaoFundDetail`。

**Tech Stack:** FastAPI, Pydantic v2, SQLite schema v6, Next.js/React/TypeScript

**Spec:** `docs/superpowers/specs/2026-06-14-fund-discovery-v2-design.md`

---

## Task 1: Schema v6 + discovery prompt persistence

**Files:** `db_migrations.py`, `mysql_bootstrap.py`, `database.py`, `discovery_prompt.py`, `models.py`

## Task 2: discovery_diff + discovery_outcomes + API routes

**Files:** `discovery_diff.py`, `discovery_outcomes.py`, `database.py`, `main.py`

## Task 3: fund_type_preference in candidate pool + pipeline

**Files:** `discovery_candidate_pool.py`, `discovery_pipeline.py`, `discovery_client.py`, `models.py`

## Task 4: Frontend api + storage + panels

**Files:** `api.ts`, `storage.ts`, `DiscoveryHistoryRail.tsx`, `DiscoveryCandidatePoolPanel.tsx`, `DiscoveryOutcomesPanel.tsx`

## Task 5: FundDiscoveryPanel + DiscoveryReportPanel integration

**Files:** `FundDiscoveryPanel.tsx`, `DiscoveryReportPanel.tsx`

## Task 6: Tests + verification

**Files:** `test_discovery_prompt.py`, `test_discovery_diff.py`, `test_discovery_outcomes.py`, `test_discovery_candidate_pool.py`, `test_api.py`

---

**Status:** ✅ Completed 2026-06-14（含稳定性修复：`job_status_service`、sector 并行、CORS 顺序）。后续 V2.2 见 spec §8。
