"""Factor research publication and evidence diagnostics HTTP surface.

The handlers in this module are deliberately thin.  Validation, immutable
storage and financial readiness rules remain owned by their domain services;
this router only maps those typed outcomes to stable HTTP contracts.
"""

from __future__ import annotations

import secrets
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from app.config import get_settings
from app.database import list_discovery_reports
from app.request_context import get_request_user_id
from app.services.decision_score_shadow import build_decision_score_shadow_digest
from app.services.evidence_maturity import build_evidence_maturity_status
from app.services.factor_confidence import clear_ic_summary_cache
from app.services.factor_ic_nav_observation import (
    FactorIcNavObservationConflict,
    FactorIcNavObservationHistoryQuery,
    FactorIcNavObservationStorageUnavailable,
    publish_nav_observation_batch,
    read_nav_observation_history,
    read_nav_observation_status,
    validate_nav_observation_publish_request,
)
from app.services.factor_ic_snapshot import (
    FactorIcNewerSnapshotExists,
    FactorIcStorageUnavailable,
    build_factor_ic_status,
    publish_factor_ic_snapshot,
    validate_publish_request,
)
from app.services.factor_ic_universe_snapshot import (
    FactorIcUniverseConflict,
    FactorIcUniverseStorageUnavailable,
    publish_factor_ic_universe_snapshot,
    read_factor_ic_universe_history,
    validate_factor_ic_universe_publish_request,
)
from app.services.factor_live_calibration import (
    FactorLiveCalibrationStorageUnavailable,
    build_factor_live_calibration_status,
)
from app.services.portfolio_snapshot import clear_factor_facts_cache


router = APIRouter(tags=["factor-evidence"])


def require_factor_ic_publish_token(
    supplied: Annotated[
        str | None,
        Header(alias="X-Factor-IC-Publish-Token"),
    ] = None,
) -> None:
    """Authorize the isolated Factor IC publication and history surface."""

    expected = (get_settings().factor_ic_publish_token or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="因子 IC 发布未配置")
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="因子 IC 发布凭证无效")


@router.post("/api/internal/factor-ic-snapshots", include_in_schema=False)
def publish_factor_ic(
    body: dict,
    _authorized: None = Depends(require_factor_ic_publish_token),
) -> dict:
    try:
        request = validate_publish_request(body)
        result = publish_factor_ic_snapshot(request)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_context=False, include_url=False),
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FactorIcNewerSnapshotExists as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FactorIcStorageUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    clear_ic_summary_cache()
    clear_factor_facts_cache()
    return result


@router.post(
    "/api/internal/factor-ic-universe-snapshots",
    include_in_schema=False,
)
def publish_factor_ic_universe(
    body: dict,
    _authorized: None = Depends(require_factor_ic_publish_token),
) -> dict:
    try:
        request = validate_factor_ic_universe_publish_request(body)
        return publish_factor_ic_universe_snapshot(request)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_context=False, include_url=False),
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FactorIcUniverseConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FactorIcUniverseStorageUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get(
    "/api/internal/factor-ic-universe-snapshots",
    include_in_schema=False,
)
def get_factor_ic_universe_history(
    start_date: date | None = None,
    end_date: date | None = None,
    days: int = 365,
    max_snapshots: int = 60,
    stride_days: int = 7,
    include_members: bool = True,
    _authorized: None = Depends(require_factor_ic_publish_token),
) -> dict:
    try:
        return read_factor_ic_universe_history(
            start_date=start_date,
            end_date=end_date,
            days=days,
            max_snapshots=max_snapshots,
            stride_days=stride_days,
            include_members=include_members,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FactorIcUniverseStorageUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/api/internal/factor-ic-nav-observations",
    include_in_schema=False,
)
def publish_factor_ic_nav_observations(
    body: dict,
    _authorized: None = Depends(require_factor_ic_publish_token),
) -> dict:
    try:
        request = validate_nav_observation_publish_request(body)
        return publish_nav_observation_batch(request)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_context=False, include_url=False),
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FactorIcNavObservationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FactorIcNavObservationStorageUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/api/internal/factor-ic-nav-observations/query",
    include_in_schema=False,
)
def query_factor_ic_nav_observations(
    body: dict,
    _authorized: None = Depends(require_factor_ic_publish_token),
) -> dict:
    try:
        query = FactorIcNavObservationHistoryQuery.model_validate(body)
        return read_nav_observation_history(
            fund_codes=query.fund_codes,
            start_date=query.start_date,
            end_date=query.end_date,
            as_of=query.as_of,
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_context=False, include_url=False),
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FactorIcNavObservationStorageUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/diagnostics/factor-ic-status")
def factor_ic_status() -> dict:
    return build_factor_ic_status()


@router.get("/api/diagnostics/factor-ic-nav-observations")
def factor_ic_nav_observation_status() -> dict:
    try:
        return read_nav_observation_status()
    except FactorIcNavObservationStorageUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/diagnostics/factor-live-calibration")
def factor_live_calibration() -> dict:
    """Return current-user shadow calibration without changing decisions."""

    try:
        return build_factor_live_calibration_status(
            user_id=get_request_user_id()
        )
    except FactorLiveCalibrationStorageUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/diagnostics/decision-score-shadow")
def decision_score_shadow_digest(limit: int = 30) -> dict:
    """Summarize current-user DecisionScore coverage and Top-K differences."""

    bounded_limit = max(1, min(limit, 100))
    return build_decision_score_shadow_digest(
        list_discovery_reports(limit=bounded_limit)
    )


@router.get("/api/diagnostics/evidence-maturity")
def evidence_maturity_status() -> Response:
    """Return bounded evidence maturity without triggering evaluation."""

    payload = build_evidence_maturity_status(user_id=get_request_user_id())
    return JSONResponse(
        content=payload,
        headers={
            "Cache-Control": "private, no-store, max-age=0",
            "Pragma": "no-cache",
        },
    )


__all__ = ["require_factor_ic_publish_token", "router"]
