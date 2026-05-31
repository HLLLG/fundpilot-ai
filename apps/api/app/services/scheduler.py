from __future__ import annotations

import threading
import time
from datetime import datetime

from app.config import get_settings
from app.models import AnalysisRequest, InvestorProfile
from app.services.inbox_store import create_inbox_event, list_inbox_events
from app.services.job_store import create_analysis_job

_thread: threading.Thread | None = None
_stop_event = threading.Event()
_last_reminder_date: str | None = None
_last_auto_date: str | None = None


def start_scheduler() -> None:
    global _thread
    settings = get_settings()
    if not settings.schedule_enabled:
        return
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_schedule_loop, name="fund-ai-schedule", daemon=True)
    _thread.start()


def stop_scheduler() -> None:
    _stop_event.set()


def _schedule_loop() -> None:
    while not _stop_event.is_set():
        settings = get_settings()
        if settings.schedule_enabled:
            _tick(settings)
        _stop_event.wait(30)


def _tick(settings) -> None:
    global _last_reminder_date, _last_auto_date
    now = datetime.now()
    today = now.date().isoformat()
    if settings.schedule_weekdays_only and now.weekday() >= 5:
        return

    hour, minute = _parse_time(settings.schedule_time)
    if now.hour != hour or now.minute != minute:
        return

    if _last_reminder_date != today:
        create_inbox_event(
            kind="schedule_reminder",
            payload={
                "message": (
                    f"交易日 {settings.schedule_time} 提醒："
                    f"请将养基宝总览截图保存到 {settings.inbox_dir}"
                ),
                "inbox_path": str(settings.inbox_dir),
            },
        )
        _last_reminder_date = today

    if settings.schedule_auto_analyze and _last_auto_date != today:
        _maybe_auto_analyze()
        _last_auto_date = today


def _maybe_auto_analyze() -> None:
    events = list_inbox_events(status="pending", limit=5)
    for event in events:
        if event["kind"] != "ocr_ready":
            continue
        payload = event["payload"]
        holdings = payload.get("holdings") or []
        if not holdings:
            continue
        request = AnalysisRequest(
            holdings=holdings,
            profile=InvestorProfile(),
            ocr_text=payload.get("raw_text"),
            analysis_mode="fast",
        )
        create_analysis_job(request)
        from app.services.inbox_store import update_inbox_event_status

        update_inbox_event_status(event["id"], "consumed")
        return


def _parse_time(value: str) -> tuple[int, int]:
    parts = value.strip().split(":")
    if len(parts) != 2:
        return 14, 25
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return 14, 25
