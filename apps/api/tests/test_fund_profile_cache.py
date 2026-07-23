from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.models import FundProfile
from app.request_context import reset_request_user_id, set_request_user_id
from app.services import fund_profile


@pytest.fixture(autouse=True)
def _clear_shared_profile_cache():
    with fund_profile.FundProfileService._shared_cache_lock:
        fund_profile.FundProfileService._shared_cache.clear()
        fund_profile.FundProfileService._user_load_locks.clear()
    yield
    with fund_profile.FundProfileService._shared_cache_lock:
        fund_profile.FundProfileService._shared_cache.clear()
        fund_profile.FundProfileService._user_load_locks.clear()


def _profile(code: str) -> FundProfile:
    return FundProfile(fund_code=code, fund_name=f"基金{code}")


def test_profile_cache_is_scoped_by_user_and_invalidated_after_write(
    monkeypatch,
) -> None:
    calls: list[int] = []

    def load() -> list[FundProfile]:
        user_id = fund_profile.get_request_user_id()
        calls.append(user_id)
        return [_profile(f"{user_id:06d}")]

    monkeypatch.setattr(fund_profile, "list_fund_profiles", load)
    service = fund_profile.FundProfileService()

    assert service.list_profiles()[0].fund_code == "000001"
    assert service.list_profiles()[0].fund_code == "000001"

    user_two = set_request_user_id(2)
    try:
        assert service.list_profiles()[0].fund_code == "000002"
        assert service.list_profiles()[0].fund_code == "000002"
    finally:
        reset_request_user_id(user_two)

    fund_profile.invalidate_fund_profile_cache()
    assert service.list_profiles()[0].fund_code == "000001"
    assert calls == [1, 2, 1]


def test_profile_cache_single_flights_concurrent_misses(monkeypatch) -> None:
    started = threading.Event()
    release = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    def load() -> list[FundProfile]:
        nonlocal calls
        with calls_lock:
            calls += 1
        started.set()
        assert release.wait(timeout=5)
        return [_profile("000001")]

    monkeypatch.setattr(fund_profile, "get_request_user_id", lambda: 1)
    monkeypatch.setattr(fund_profile, "list_fund_profiles", load)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(fund_profile.FundProfileService().list_profiles)
            for _ in range(8)
        ]
        assert started.wait(timeout=2)
        release.set()
        results = [future.result(timeout=5) for future in futures]

    assert calls == 1
    assert all(result[0].fund_code == "000001" for result in results)
