"""存量持仓关联板块一次性补跑。

背景：板块推断规则（语义名称泛化/业绩基准兜底/持仓穿透放开白名单/DeepSeek 兜底分类）
上线后，历史已录入的持仓里那些之前被规则拒绝、板块列一直空着的基金（多为海外/QDII/
冷门主题基金）不会自动重新解析——用户不会主动点"精确刷新"。这里在应用启动后延迟
一段时间、异步跑一次全量用户持仓扫描，把仍然缺板块的基金过一遍完整规则链（含 LLM
兜底），解析成功就写回持仓快照。跨用户按 fund_code 去重解析，避免同一只基金被重复
命中规则/网络请求/LLM 调用。只在从未成功完成过时自动跑一次；可通过 force=True 手动
重跑（例如规则升级后想再补一轮）。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from app.config import get_settings
from app.database import get_fund_primary_sector, list_distinct_portfolio_user_ids
from app.models import Holding
from app.request_context import reset_request_user_id, set_request_user_id
from app.services.fund_profile import _is_valid_sector_label
from app.services.portfolio_holdings_service import load_persisted_holdings
from app.services.portfolio_persistence import persist_holdings_after_sector_refresh

logger = logging.getLogger(__name__)

_PER_CODE_SLEEP_SECONDS = 0.05


def _status_path() -> Path:
    return get_settings().db_path.parent / "fund_primary_sector_backfill_status.json"


def _load_status() -> dict:
    path = _status_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_status(payload: dict) -> None:
    path = _status_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.info("failed to persist backfill status: %s", exc)


def has_backfill_completed() -> bool:
    return bool(_load_status().get("completed_at"))


def _is_missing_sector(holding: Holding) -> bool:
    code = (holding.fund_code or "").strip()
    if not code or code == "000000":
        return False
    return not _is_valid_sector_label(holding.sector_name)


def _needs_backfill(holding: Holding) -> bool:
    """是否值得重新解析：板块缺失，或已有标签但来源不是人工/OCR详情这类可信来源。

    像"alipay_overview"总览推断、"semantic_name_freeform"自由主题猜测这类来源即使
    格式上"看起来合法"（_is_valid_sector_label 通过），也可能是历史误判（例如把基金
    自身营销短语当成板块），值得再用完整规则链+LLM 兜底重新尝试一次；只要新结果的
    来源优先级更高，_record_should_override_holding_sector 会负责实际是否采用。
    """
    code = (holding.fund_code or "").strip()
    if not code or code == "000000":
        return False
    if _is_missing_sector(holding):
        return True
    from app.services.fund_primary_sector_service import _HIGH_TRUST_SECTOR_SOURCES

    row = get_fund_primary_sector(code)
    existing_source = str((row or {}).get("source") or "")
    return existing_source not in _HIGH_TRUST_SECTOR_SOURCES


def backfill_primary_sectors_for_existing_holdings(*, force: bool = False) -> dict:
    """返回统计信息 dict；`force=False` 且历史已完成过时直接跳过（幂等，可重复调用）。"""
    if not force and has_backfill_completed():
        return {"skipped": "already_completed", **_load_status()}

    from app.services.fund_primary_sector_service import (
        _record_should_override_holding_sector,
        _usable_intraday_index_name,
        resolve_primary_sector,
    )

    user_ids = list_distinct_portfolio_user_ids()
    per_user_holdings: dict[int, list[Holding]] = {}
    pending_codes: dict[str, str] = {}
    # resolve_primary_sector 内部会按"当前用户"查/写 fund_primary_sectors 缓存；
    # 按 fund_code 去重解析时，借用第一个持有该基金的用户 id 作为上下文，
    # 让解析结果落到一个真实持有者的记录里，而不是凭空关联到无关用户。
    code_first_holder: dict[str, int] = {}

    for user_id in user_ids:
        token = set_request_user_id(user_id)
        try:
            holdings, *_ = load_persisted_holdings(fetch_benchmark=False)
            if not holdings:
                continue
            per_user_holdings[user_id] = holdings
            # _needs_backfill 内部会查询 fund_primary_sectors（按当前用户），必须在
            # set_request_user_id 生效期间调用，否则会抛"未设置当前用户上下文"。
            for holding in holdings:
                code_first_holder.setdefault(holding.fund_code, user_id)
                if _needs_backfill(holding) and holding.fund_name:
                    pending_codes.setdefault(holding.fund_code, holding.fund_name)
        except Exception as exc:
            logger.info("backfill scan holdings failed for user=%s: %s", user_id, exc)
        finally:
            reset_request_user_id(token)

    resolved: dict[str, object] = {}
    for code, name in pending_codes.items():
        token = set_request_user_id(code_first_holder.get(code, user_ids[0] if user_ids else 0))
        try:
            record = resolve_primary_sector(
                code,
                fund_name=name,
                allow_name_infer=True,
                fetch_benchmark=True,
                fetch_holdings_infer=True,
            )
        except Exception as exc:
            logger.info("backfill resolve failed for %s: %s", code, exc)
            record = None
        finally:
            reset_request_user_id(token)
        if record is not None and record.sector_name:
            resolved[code] = record
        if _PER_CODE_SLEEP_SECONDS > 0:
            time.sleep(_PER_CODE_SLEEP_SECONDS)

    fixed_users = 0
    fixed_holdings = 0
    cleaned_intraday_index_names = 0
    for user_id, holdings in per_user_holdings.items():
        token = set_request_user_id(user_id)
        try:
            changed = False
            updated_holdings: list[Holding] = []
            for holding in holdings:
                record = resolved.get(holding.fund_code)
                # _record_should_override_holding_sector 内部会查询 fund_primary_sectors
                # （按当前用户），必须在 set_request_user_id 生效期间调用。
                if record is not None and (
                    _is_missing_sector(holding)
                    or _record_should_override_holding_sector(holding, record)
                ):
                    if holding.sector_name != record.sector_name:
                        holding = holding.model_copy(update={"sector_name": record.sector_name})
                        changed = True
                        fixed_holdings += 1
                # 轻量数据清理：不需要重新走完整规则链——已经落库的 sector_name 如果
                # 本身有行情源，但 intraday_index_name 是业绩基准原文抠出来、查不到
                # 行情的指数名（如"中证高端装备制造指数"），直接清掉即可，让详情页
                # 分时图和列表日涨幅统一退回可用的板块短名。
                cleaned_index_name = _usable_intraday_index_name(
                    holding.intraday_index_name, holding.sector_name
                )
                if cleaned_index_name != holding.intraday_index_name:
                    holding = holding.model_copy(
                        update={"intraday_index_name": cleaned_index_name}
                    )
                    changed = True
                    cleaned_intraday_index_names += 1
                updated_holdings.append(holding)
            if not changed:
                continue
            persist_holdings_after_sector_refresh(updated_holdings, with_official_nav=False)
            fixed_users += 1
        except Exception as exc:
            logger.info("backfill persist failed for user=%s: %s", user_id, exc)
        finally:
            reset_request_user_id(token)

    stats = {
        "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "users_scanned": len(user_ids),
        "codes_pending": len(pending_codes),
        "codes_resolved": len(resolved),
        "users_fixed": fixed_users,
        "holdings_fixed": fixed_holdings,
        "intraday_index_names_cleaned": cleaned_intraday_index_names,
    }
    _save_status(stats)
    logger.info("fund primary sector backfill done: %s", stats)
    return stats


def _enabled() -> bool:
    return bool(get_settings().fund_primary_sector_backfill_enabled)


def _startup_delay_seconds() -> float:
    return float(max(0, int(get_settings().fund_primary_sector_backfill_startup_delay_seconds)))


def run_fund_primary_sector_backfill_once_at_startup() -> None:
    """daemon 线程入口：延迟一段时间后跑一次存量补跑，已完成过则立即返回。"""
    if not _enabled() or has_backfill_completed():
        return
    delay = _startup_delay_seconds()
    if delay > 0:
        logger.info("primary sector backfill sleeping %ss before running once", int(delay))
        time.sleep(delay)
    try:
        backfill_primary_sectors_for_existing_holdings()
    except Exception as exc:
        logger.info("primary sector backfill failed: %s", exc)
