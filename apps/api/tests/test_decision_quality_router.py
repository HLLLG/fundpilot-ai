from __future__ import annotations

from pathlib import Path

from app.main import app


PATH = "/api/internal/decision-quality/evaluations/latest"


def test_decision_quality_router_is_registered_once_and_hidden_from_openapi() -> None:
    matching = [route for route in app.routes if route.path == PATH]

    assert sum("GET" in (route.methods or set()) for route in matching) == 1
    assert PATH not in app.openapi()["paths"]


def test_main_module_keeps_decision_quality_reads_behind_router_boundary() -> None:
    main_path = Path(__file__).parents[1] / "app" / "main.py"
    source = main_path.read_text(encoding="utf-8")

    assert "from app.routes.decision_quality import" in source
    assert "from app.services.decision_quality_snapshot import" not in source
    assert '@app.get(\n    "/api/internal/decision-quality/' not in source
