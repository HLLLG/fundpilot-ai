from __future__ import annotations

from pathlib import Path

from app.main import app


MARKET_DIAGNOSTIC_PATHS = {
    "/api/diagnostics/sector-signal-backtest",
    "/api/diagnostics/market-breadth",
    "/api/diagnostics/fund-return-distribution",
    "/api/diagnostics/shadow-escalation-digest",
}


def test_market_diagnostic_routes_are_registered_once_and_documented() -> None:
    openapi_paths = app.openapi()["paths"]
    for path in MARKET_DIAGNOSTIC_PATHS:
        matching = [
            route
            for route in app.routes
            if getattr(route, "path", None) == path
        ]
        assert sum("GET" in (route.methods or set()) for route in matching) == 1
        assert path in openapi_paths


def test_main_module_keeps_market_diagnostics_behind_router_boundary() -> None:
    main_path = Path(__file__).parents[1] / "app" / "main.py"
    source = main_path.read_text(encoding="utf-8")

    assert "from app.routes.market_diagnostics import" in source
    assert "from app.services.market_breadth_signal import" not in source
    assert '@app.get("/api/diagnostics/market-breadth")' not in source
