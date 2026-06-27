from __future__ import annotations

import ast
from pathlib import Path


def test_services_do_not_import_akshare_in_main_process():
    services_dir = Path(__file__).resolve().parents[1] / "app" / "services"
    offenders: list[str] = []

    for path in sorted(services_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name == "akshare" for alias in node.names):
                    offenders.append(f"{path.relative_to(services_dir)}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom):
                if node.module == "akshare":
                    offenders.append(f"{path.relative_to(services_dir)}:{node.lineno}")

    assert offenders == []
