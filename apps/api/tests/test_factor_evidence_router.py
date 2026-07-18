from __future__ import annotations

from pathlib import Path

from app.main import app


FACTOR_EVIDENCE_METHODS = {
    "/api/internal/factor-ic-snapshots": {"POST"},
    "/api/internal/factor-ic-universe-snapshots": {"GET", "POST"},
    "/api/internal/factor-ic-nav-observations": {"POST"},
    "/api/internal/factor-ic-nav-observations/query": {"POST"},
    "/api/diagnostics/factor-ic-status": {"GET"},
    "/api/diagnostics/factor-ic-nav-observations": {"GET"},
    "/api/diagnostics/factor-live-calibration": {"GET"},
    "/api/diagnostics/decision-score-shadow": {"GET"},
    "/api/diagnostics/evidence-maturity": {"GET"},
}


def test_factor_evidence_router_is_registered_once_without_public_internal_docs() -> None:
    for path, methods in FACTOR_EVIDENCE_METHODS.items():
        matching = [
            route
            for route in app.routes
            if getattr(route, "path", None) == path
        ]
        for method in methods:
            assert sum(method in (route.methods or set()) for route in matching) == 1

    openapi_paths = app.openapi()["paths"]
    for path in FACTOR_EVIDENCE_METHODS:
        if path.startswith("/api/internal/"):
            assert path not in openapi_paths
        else:
            assert path in openapi_paths


def test_main_module_keeps_factor_domain_behind_router_boundary() -> None:
    main_path = Path(__file__).parents[1] / "app" / "main.py"
    source = main_path.read_text(encoding="utf-8")

    assert "from app.routes.factor_evidence import" in source
    assert "from app.services.factor_ic_" not in source
    assert '@app.get("/api/diagnostics/factor-ic-status")' not in source
    assert '@app.post("/api/internal/factor-ic-snapshots"' not in source
