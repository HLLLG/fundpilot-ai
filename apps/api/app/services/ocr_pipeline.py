from __future__ import annotations

import logging
from pathlib import Path

from app.config import get_settings
from app.models import PortfolioSummary
from app.database import save_portfolio_summary
from app.services.fund_profile import FundProfileService
from app.services.holding_validation import build_holding_review, enrich_portfolio_summary_source
from app.services.holdings_extractor import ExtractionResult, extract_holdings
from app.services.fund_code_resolver import (
    UNRESOLVED_FUND_CODE_HINT,
    is_provisional_fund_code,
    lookup_fund_name_by_code,
    resolve_holding_fund_code,
)
from app.services.fund_name_utils import sanitize_fund_name
from app.services.ocr_parser import detect_ocr_source
from app.services.trading_session import build_trading_session
from app.services.overview_pipeline import enrich_holdings_from_profiles, process_overview_holdings
from app.services.portfolio_parser import parse_portfolio_summary_from_text
from app.services.portfolio_snapshot import get_previous_holdings_for_review, save_daily_snapshot

logger = logging.getLogger(__name__)


def _cleanup_upload_artifacts(upload_path: Path | None) -> None:
    """OCR 完成后删除落盘原图及 Paddle 预处理副本，避免 uploads 目录堆积。"""
    if upload_path is None:
        return

    upload_dir = get_settings().upload_dir.resolve()
    candidates = (
        upload_path,
        upload_path.with_name(f"{upload_path.stem}.ocr-prepared.jpg"),
    )
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            if resolved.parent != upload_dir:
                continue
            if resolved.is_file():
                resolved.unlink()
        except OSError:
            logger.warning("failed to delete upload artifact %s", candidate, exc_info=True)


def run_ocr_upload_pipeline(
    *,
    text: str = "",
    file_bytes: bytes | None = None,
    filename: str | None = None,
    preview: bool = False,
) -> dict:
    settings = get_settings()
    upload_path: Path | None = None

    if file_bytes and filename:
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
        upload_path = settings.upload_dir / Path(filename).name
        upload_path.write_bytes(file_bytes)

    try:
        extraction: ExtractionResult = extract_holdings(file_bytes=file_bytes, text=text)
    except Exception as exc:  # noqa: BLE001 — 识别异常不应让端点 500
        _cleanup_upload_artifacts(upload_path)
        return {
            "raw_text": "",
            "upload_path": str(upload_path) if upload_path else None,
            "holdings": [],
            "error": f"OCR 识别失败：{exc}",
            "extraction_provider": "none",
        }

    text = extraction.raw_text or text
    parsed_holdings = extraction.holdings
    profile_service = FundProfileService()
    ocr_source = extraction.ocr_source
    # 文本可用时用 detect_ocr_source 补全更细的来源（如 alipay_transactions）。
    if ocr_source == "unknown" and text:
        ocr_source = detect_ocr_source(text)
    # 兜底：能解析出持仓即视为支付宝持有页，避免页眉漏读时错误提示「未识别为支付宝持有页」。
    if parsed_holdings and ocr_source != "alipay_holdings":
        ocr_source = "alipay_holdings"
    holdings, fund_code_resolutions = _resolve_fund_codes(parsed_holdings, profile_service)
    holdings = profile_service.resolve_holdings(holdings)
    previous_holdings = get_previous_holdings_for_review()
    profile_sync = (
        profile_service.sync_profiles_from_holdings(holdings).model_dump()
        if not preview
        else {"updated": 0, "created": 0, "skipped": True}
    )

    portfolio_summary = parse_portfolio_summary_from_text(text)
    if portfolio_summary is not None:
        portfolio_summary = enrich_portfolio_summary_source(portfolio_summary, holdings)
        portfolio_summary = portfolio_summary.model_copy(
            update={"holding_count": len(holdings)}
        )

    sector_refresh: dict | None = None
    if holdings:
        if preview:
            # 养基宝式确认页：仅 OCR + 查码，不拉板块（确认后再刷新）
            holdings = enrich_holdings_from_profiles(holdings)
            sector_refresh = {
                "ok": True,
                "skipped": True,
                "message": "预览模式：确认后将刷新板块涨跌并估算当日收益。",
                "holdings": [holding.model_dump() for holding in holdings],
                "items": [],
                "summary": {"matched": 0, "unresolved": 0, "needs_mapping": 0},
            }
        else:
            try:
                holdings, sector_refresh, portfolio_summary = process_overview_holdings(
                    holdings,
                    portfolio_summary=portfolio_summary,
                    force_sector_refresh=True,
                    from_user_upload=True,
                )
            except Exception as exc:
                logger.exception("sector refresh failed during OCR")
                sector_refresh = {
                    "ok": False,
                    "message": f"持仓已识别，但板块刷新失败：{exc}。请点刷新按钮重试。",
                    "holdings": [holding.model_dump() for holding in holdings],
                    "items": [],
                    "summary": {"matched": 0, "unresolved": len(holdings), "needs_mapping": 0},
                }
            if portfolio_summary is not None:
                save_portfolio_summary(portfolio_summary)

    holding_review = build_holding_review(
        holdings,
        previous_holdings=previous_holdings,
        portfolio_summary=portfolio_summary,
    )

    if holdings and not preview:
        save_daily_snapshot(holdings, portfolio_summary)

    trading_session = build_trading_session()
    amount_semantics = _ocr_amount_semantics(ocr_source, trading_session)

    result = {
        "raw_text": text,
        "upload_path": str(upload_path) if upload_path else None,
        "holdings": [holding.model_dump() for holding in holdings],
        "cache_hit": False,
        "extraction_provider": extraction.provider,
        "preview": preview,
        "ocr_source": ocr_source,
        "fund_code_resolutions": fund_code_resolutions,
        "amount_semantics": amount_semantics,
        "trading_session": trading_session,
        "profile_sync": profile_sync,
        "sector_refresh": sector_refresh,
        "portfolio_summary": (
            portfolio_summary.model_dump(mode="json") if portfolio_summary else None
        ),
        **holding_review,
    }

    _cleanup_upload_artifacts(upload_path)
    return result


def _resolve_fund_codes(
    holdings: list,
    profile_service: FundProfileService,
) -> tuple[list, list[dict]]:
    resolved_holdings = []
    resolutions: list[dict] = []

    for holding in holdings:
        clean_name = sanitize_fund_name(holding.fund_name)
        if clean_name and clean_name != holding.fund_name:
            holding = holding.model_copy(update={"fund_name": clean_name})

        profile = profile_service.find_match(holding.fund_name)
        profile_code = profile.fund_code if profile and profile.fund_code != "000000" else None
        if profile and profile.is_provisional:
            profile_code = None
        if profile_code and is_provisional_fund_code(profile_code):
            profile_code = None
        code, source = resolve_holding_fund_code(
            holding.fund_name,
            existing_code=profile_code,
        )

        if code and code != holding.fund_code:
            holding = holding.model_copy(update={"fund_code": code})

        resolved = holding.fund_code != "000000"
        resolved_holdings.append(holding)
        resolutions.append(
            {
                "fund_name": holding.fund_name,
                "fund_code": holding.fund_code if resolved else None,
                "source": source,
                "resolved": resolved,
                "message": None if resolved else UNRESOLVED_FUND_CODE_HINT,
            }
        )

    return resolved_holdings, resolutions


def apply_confirmed_holdings(
    holdings: list,
) -> dict:
    """用户确认 OCR 草稿 / 手动新增后：快速写库并立即返回。

    历史实现会在本请求内同步做全量板块刷新 + per-fund 官方净值/估值拉取（无超时预算），
    在 CloudBase 云托管下极易超过网关超时（~60s）返回 504（且 504 不经 CORS 中间件，
    浏览器二次报 CORS）。这里改为「快速写入」：只做 查码 + 档案/板块映射（DB）+ 展示层估算，
    板块涨跌与当日收益由前端在 apply 成功后调用 `POST /api/holdings/refresh-sector-quotes`
    异步刷新（该端点带 8s 预算并会回写快照）。
    """
    from app.models import Holding
    from app.services.portfolio_persistence import enrich_loaded_holdings

    profile_service = FundProfileService()
    typed = [Holding.model_validate(item) if isinstance(item, dict) else item for item in holdings]
    typed = _finalize_confirmed_holdings(typed, profile_service)
    from app.services.fund_primary_sector_service import apply_primary_sector_to_holdings

    typed = apply_primary_sector_to_holdings(typed)

    profile_sync = profile_service.sync_profiles_from_holdings(typed).model_dump()
    merged = enrich_holdings_from_profiles(typed)
    processed = enrich_loaded_holdings(merged, with_network=False)

    total_assets = round(sum(item.holding_amount for item in processed), 2)
    portfolio_summary = PortfolioSummary(
        total_assets=total_assets,
        holding_count=len(processed),
    )
    save_portfolio_summary(portfolio_summary)
    save_daily_snapshot(processed, portfolio_summary)
    return {
        "holdings": [holding.model_dump() for holding in processed],
        "portfolio_summary": portfolio_summary.model_dump(mode="json"),
        "profile_sync": profile_sync,
        "sector_refresh": None,
    }


def _finalize_confirmed_holdings(holdings: list, profile_service: FundProfileService) -> list:
    """确认入库前：尊重用户手填代码，其余走名称查码。"""
    finalized = []
    for holding in holdings:
        clean_name = sanitize_fund_name(holding.fund_name)
        updates: dict = {}
        if clean_name and clean_name != holding.fund_name:
            updates["fund_name"] = clean_name

        code = holding.fund_code
        manual_code = (
            code
            and code != "000000"
            and not is_provisional_fund_code(code)
        )
        if manual_code:
            table_name = lookup_fund_name_by_code(code)
            if table_name and not updates.get("fund_name"):
                updates["fund_name"] = table_name
        else:
            profile = profile_service.find_match(clean_name or holding.fund_name)
            profile_code = profile.fund_code if profile and profile.fund_code != "000000" else None
            if profile and profile.is_provisional:
                profile_code = None
            if profile_code and is_provisional_fund_code(profile_code):
                profile_code = None
            resolved_code, _ = resolve_holding_fund_code(
                clean_name or holding.fund_name,
                existing_code=profile_code,
            )
            if resolved_code:
                updates["fund_code"] = resolved_code

        finalized.append(holding.model_copy(update=updates) if updates else holding)

    return finalized


def _ocr_amount_semantics(ocr_source: str, trading_session: dict) -> dict:
    session_kind = trading_session.get("session_kind", "")
    if ocr_source != "alipay_holdings":
        return {
            "source": ocr_source,
            "holding_amount": "unknown",
            "daily_profit": "sector_estimate_after_refresh",
            "note": "未识别为支付宝持有页：金额按截图原值保留，当日收益需刷新板块后估算。",
        }

    if session_kind == "non_trading_day":
        return {
            "source": "alipay_holdings",
            "holding_amount": "settled_includes_latest_trade_day",
            "daily_profit": "sector_or_official_nav_after_refresh",
            "note": "非交易日截图中的持有金额通常已含最近交易日结算，无需再改金额；当日收益由板块/官方净值计算。",
        }

    return {
        "source": "alipay_holdings",
        "holding_amount": "last_trade_day_settlement",
        "daily_profit": "sector_estimate_after_refresh",
        "note": "交易日盘中截图的持有金额为上一交易日结算值；当日收益需刷新板块后估算。",
    }
