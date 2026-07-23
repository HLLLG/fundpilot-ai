from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services import (
    akshare_subprocess,
    discovery_judge,
    report_judge,
    shared_executors,
)
from app.services.streaming_heartbeat import StreamCancelled


def test_shared_executors_are_singletons_and_pipeline_isolated() -> None:
    shared_executors.close_shared_executors()
    try:
        io = shared_executors.get_shared_io_executor()
        analysis = shared_executors.get_analysis_context_executor()
        discovery = shared_executors.get_discovery_context_executor()

        assert shared_executors.get_shared_io_executor() is io
        assert shared_executors.get_analysis_context_executor() is analysis
        assert shared_executors.get_discovery_context_executor() is discovery
        assert analysis is not discovery
        assert io._max_workers == 32
        assert analysis._max_workers == 2
        assert discovery._max_workers == 2
    finally:
        shared_executors.close_shared_executors()


@pytest.mark.parametrize(
    ("module", "executor_attribute", "call"),
    [
        (
            report_judge,
            "get_analysis_context_executor",
            lambda module, stop: module._llm_judge_with_budget(
                {},
                {},
                {},
                stop_event=stop,
            ),
        ),
        (
            discovery_judge,
            "get_discovery_context_executor",
            lambda module, stop: module._llm_judge_with_budget(
                {},
                [],
                {},
                {},
                stop_event=stop,
            ),
        ),
    ],
)
def test_budgeted_judges_stop_waiting_after_disconnect(
    monkeypatch,
    module,
    executor_attribute: str,
    call,
) -> None:
    started = threading.Event()
    release = threading.Event()
    executor = ThreadPoolExecutor(max_workers=1)

    def blocked(*_args):
        started.set()
        release.wait(timeout=5)
        return {}

    monkeypatch.setattr(module, "_llm_judge", blocked)
    monkeypatch.setattr(module, executor_attribute, lambda: executor)
    stop = threading.Event()
    timer = threading.Timer(0.1, stop.set)
    timer.start()
    started_at = time.monotonic()
    try:
        with pytest.raises(StreamCancelled):
            call(module, stop)
        assert started.wait(timeout=1)
        assert time.monotonic() - started_at < 1.0
    finally:
        release.set()
        timer.cancel()
        executor.shutdown(wait=True, cancel_futures=True)


def test_akshare_worker_reuses_process_and_restores_task_environment() -> None:
    worker = akshare_subprocess._AkshareWorker(
        max_tasks=2,
        max_lifetime_seconds=60,
    )
    try:
        first = worker.execute(
            'import json,os; os.environ["FUND_PILOT_POOL_TEST"]="dirty"; '
            'print(json.dumps({"value": 1}))',
            timeout=30,
        )
        assert worker._process is not None
        first_pid = worker._process.pid
        second = worker.execute(
            'import json,os; print(json.dumps({"value": 2, "clean": '
            'os.environ.get("FUND_PILOT_POOL_TEST") is None}))',
            timeout=30,
        )

        assert worker._process is not None
        assert worker._process.pid == first_pid
        assert json.loads(first.stdout) == {"value": 1}
        assert json.loads(second.stdout) == {"value": 2, "clean": True}
        assert worker.should_retire is True
    finally:
        worker.close()
