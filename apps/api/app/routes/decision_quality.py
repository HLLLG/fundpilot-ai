"""Read-only operations surface for precomputed decision-quality snapshots."""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse, Response

from app.config import get_settings
from app.services.decision_quality_snapshot import (
    DecisionQualitySnapshotContractError,
    DecisionQualitySnapshotStorageError,
    read_latest_decision_quality_snapshot,
)


router = APIRouter(tags=["decision-quality"])


def require_decision_quality_read_token(
    supplied: Annotated[
        str | None,
        Header(alias="X-Decision-Quality-Read-Token"),
    ] = None,
) -> None:
    """Authorize the isolated read-only operations surface."""

    no_store = {"Cache-Control": "private, no-store, max-age=0"}
    expected = (get_settings().decision_quality_read_token or "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="决策质量快照只读接口未配置",
            headers=no_store,
        )
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=401,
            detail="决策质量快照只读凭证无效",
            headers=no_store,
        )


def _etag_matches(if_none_match: str | None, etag: str) -> bool:
    if not if_none_match:
        return False
    for candidate in if_none_match.split(","):
        normalized = candidate.strip()
        if normalized == "*":
            return True
        if normalized.startswith("W/"):
            normalized = normalized[2:].strip()
        if normalized == etag:
            return True
    return False


@router.get(
    "/api/internal/decision-quality/evaluations/latest",
    include_in_schema=False,
)
def get_latest_decision_quality_evaluation(
    user_id: str | None = None,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
    _authorized: None = Depends(require_decision_quality_read_token),
) -> Response:
    """Return one precomputed, redacted snapshot without running evaluation."""

    response_headers = {
        "Cache-Control": "private, no-store, max-age=0",
        "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff",
    }
    if user_id is None or not user_id.strip().isdigit() or int(user_id) <= 0:
        raise HTTPException(
            status_code=422,
            detail="user_id 必须为正整数",
            headers=response_headers,
        )
    normalized_user_id = int(user_id)
    try:
        payload = read_latest_decision_quality_snapshot(user_id=normalized_user_id)
    except (
        DecisionQualitySnapshotContractError,
        DecisionQualitySnapshotStorageError,
    ) as exc:
        raise HTTPException(
            status_code=503,
            detail="决策质量快照暂不可用",
            headers=response_headers,
        ) from exc
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail="尚无预计算的决策质量快照",
            headers=response_headers,
        )
    content_hash = str(payload.get("content_hash") or "").strip().lower()
    if len(content_hash) != 64 or any(
        character not in "0123456789abcdef" for character in content_hash
    ):
        raise HTTPException(
            status_code=503,
            detail="决策质量快照暂不可用",
            headers=response_headers,
        )
    etag = f'"{content_hash}"'
    response_headers["ETag"] = etag
    if _etag_matches(if_none_match, etag):
        return Response(status_code=304, headers=response_headers)
    return JSONResponse(content=payload, headers=response_headers)
