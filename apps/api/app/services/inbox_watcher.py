from __future__ import annotations

import threading
import time
from pathlib import Path

from app.config import get_settings
from app.services.inbox_processor import _IMAGE_SUFFIXES, process_inbox_file

_seen_lock = threading.Lock()
_seen_files: set[str] = set()
_thread: threading.Thread | None = None
_stop_event = threading.Event()


def start_inbox_watcher() -> None:
    global _thread
    settings = get_settings()
    if not settings.inbox_enabled:
        return
    if _thread and _thread.is_alive():
        return
    settings.inbox_dir.mkdir(parents=True, exist_ok=True)
    (settings.inbox_dir / "processed").mkdir(parents=True, exist_ok=True)
    _stop_event.clear()
    _thread = threading.Thread(target=_watch_loop, name="fund-ai-inbox", daemon=True)
    _thread.start()


def stop_inbox_watcher() -> None:
    _stop_event.set()


def _watch_loop() -> None:
    while not _stop_event.is_set():
        settings = get_settings()
        if not settings.inbox_enabled:
            time.sleep(settings.inbox_poll_seconds)
            continue
        _scan_inbox(settings.inbox_dir)
        _stop_event.wait(settings.inbox_poll_seconds)


def _scan_inbox(inbox_dir: Path) -> None:
    if not inbox_dir.exists():
        return
    for path in sorted(inbox_dir.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _IMAGE_SUFFIXES:
            continue
        key = f"{path.name}:{path.stat().st_mtime_ns}"
        with _seen_lock:
            if key in _seen_files:
                continue
            _seen_files.add(key)
        try:
            if path.stat().st_size < 1024:
                continue
            process_inbox_file(path)
        except Exception:
            with _seen_lock:
                _seen_files.discard(key)
