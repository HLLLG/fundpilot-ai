from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from app.config import get_settings
from app.database import get_ocr_text_cache, save_ocr_text_cache, save_portfolio_summary
from app.services.fund_profile import FundProfileService
from app.services.holding_validation import build_holding_review, enrich_portfolio_summary_source
from app.services.ocr_engine import OcrEngine
from app.services.ocr_parser import parse_holdings_from_text
from app.services.overview_pipeline import process_overview_holdings
from app.services.portfolio_parser import parse_portfolio_summary_from_text
from app.services.portfolio_snapshot import get_previous_holdings_for_review, save_daily_snapshot

logger = logging.getLogger(__name__)


def run_ocr_upload_pipeline(
    *,
    text: str = "",
    file_bytes: bytes | None = None,
    filename: str | None = None,
) -> dict:
    settings = get_settings()
    upload_path: Path | None = None
    cache_hit = False

    if file_bytes and filename:
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
        upload_path = settings.upload_dir / Path(filename).name
        upload_path.write_bytes(file_bytes)
        cache_key = hashlib.sha256(file_bytes).hexdigest()
        if not text:
            cached_text = get_ocr_text_cache(cache_key)
            if cached_text is not None:
                text = cached_text
                cache_hit = True
            else:
                try:
                    text = OcrEngine().extract_text(upload_path)
                    save_ocr_text_cache(cache_key, text)
                except Exception as exc:
                    return {
                        "raw_text": "",
                        "upload_path": str(upload_path),
                        "holdings": [],
                        "error": f"OCR 识别失败：{exc}",
                    }

    profile_service = FundProfileService()
    holdings = profile_service.resolve_holdings(parse_holdings_from_text(text))
    previous_holdings = get_previous_holdings_for_review()
    profile_sync = profile_service.sync_profiles_from_holdings(holdings).model_dump()

    portfolio_summary = parse_portfolio_summary_from_text(text)
    if portfolio_summary is not None:
        portfolio_summary = enrich_portfolio_summary_source(portfolio_summary, holdings)
        portfolio_summary = portfolio_summary.model_copy(
            update={"holding_count": len(holdings)}
        )

    sector_refresh: dict | None = None
    if holdings:
        try:
            holdings, sector_refresh, portfolio_summary = process_overview_holdings(
                holdings,
                portfolio_summary=portfolio_summary,
                force_sector_refresh=True,
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

    if holdings:
        save_daily_snapshot(holdings, portfolio_summary)

    return {
        "raw_text": text,
        "upload_path": str(upload_path) if upload_path else None,
        "holdings": [holding.model_dump() for holding in holdings],
        "cache_hit": cache_hit,
        "profile_sync": profile_sync,
        "sector_refresh": sector_refresh,
        "portfolio_summary": (
            portfolio_summary.model_dump(mode="json") if portfolio_summary else None
        ),
        **holding_review,
    }
