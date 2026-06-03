from __future__ import annotations

from pathlib import Path

from app.config import get_settings
from app.database import import_database_file


def maybe_auto_import_database() -> dict[str, str] | None:
    settings = get_settings()
    source = settings.db_auto_import_path
    if source is None:
        return None

    path = Path(source)
    if not path.exists():
        return None

    return import_database_file(path, backup_current=True)
