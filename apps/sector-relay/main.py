from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Allow importing eastmoney client from apps/api when running standalone.
REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from fastapi import FastAPI, Header, HTTPException, Query

from app.services.eastmoney_spot_client import (
    fetch_eastmoney_boards,
    fetch_eastmoney_quote_by_secid,
)

app = FastAPI(title="FundPilot Sector Quote Relay", version="0.1.0")

RELAY_TOKEN = os.environ.get("RELAY_TOKEN", "").strip()
CACHE_TTL_SECONDS = float(os.environ.get("CACHE_TTL_SECONDS", "60"))
_boards_cache: dict[str, object] = {"expires_at": 0.0, "boards": None}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/boards")
def boards(
    authorization: str | None = Header(default=None),
    x_relay_token: str | None = Header(default=None),
) -> dict:
    _require_token(authorization, x_relay_token)
    cached = _get_cached_boards()
    if cached is not None:
        return {"boards": cached, "cached": True}
    live = fetch_eastmoney_boards(timeout=25.0, max_retries=2, max_hosts=3)
    _set_cached_boards(live)
    return {"boards": live, "cached": False}


@app.get("/quote")
def quote(
    secid: str = Query(..., min_length=3),
    authorization: str | None = Header(default=None),
    x_relay_token: str | None = Header(default=None),
) -> dict:
    _require_token(authorization, x_relay_token)
    name, change = fetch_eastmoney_quote_by_secid(secid, timeout=8.0, max_retries=2)
    if change is None:
        raise HTTPException(status_code=404, detail=f"quote not found for secid={secid}")
    return {"secid": secid, "name": name, "change_percent": change}


def _require_token(authorization: str | None, x_relay_token: str | None) -> None:
    if not RELAY_TOKEN:
        return
    bearer = ""
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization[7:].strip()
    token = x_relay_token or bearer
    if token != RELAY_TOKEN:
        raise HTTPException(status_code=401, detail="invalid relay token")


def _get_cached_boards() -> dict | None:
    expires_at = float(_boards_cache.get("expires_at") or 0)
    if time.time() < expires_at:
        boards = _boards_cache.get("boards")
        if isinstance(boards, dict):
            return boards
    return None


def _set_cached_boards(boards: dict) -> None:
    _boards_cache["boards"] = boards
    _boards_cache["expires_at"] = time.time() + CACHE_TTL_SECONDS
