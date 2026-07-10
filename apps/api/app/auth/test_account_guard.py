from __future__ import annotations

import os
import re

from app.config import get_settings

# 与 pytest / Playwright / smoke 脚本里常见的测试账号格式对齐
BLOCKED_TEST_ACCOUNT_PATTERNS = (
    r"@example\.com$",
    r"@t\.com$",
    r"^cloudbase-test-",
    r"^e2e-",
    r"^uitest\+",
    r"^debug_test@",
    r"^debugtest",
    r"^migration@local$",
    r"^auth-[a-f0-9]+@example\.com$",
    r"^dup-[a-f0-9]+@example\.com$",
    r"^[ab]-[a-f0-9]+@example\.com$",
    r"^user-[a-f0-9]+@example\.com$",
    r"^sect_[a-f0-9]+@t\.com$",
)


def is_production_api() -> bool:
    """CloudBase 部署或显式 production 环境视为生产 API。"""
    if os.getenv("FUND_AI_ALLOW_TEST_ACCOUNTS", "").strip().lower() in {"1", "true", "yes"}:
        return False
    if os.getenv("FUND_AI_APP_ENV", "").strip().lower() == "production":
        return True
    return bool((get_settings().cloudbase_env_id or "").strip())


def is_blocked_test_account(account: str) -> bool:
    normalized = account.strip().lower()
    return any(re.search(pattern, normalized) for pattern in BLOCKED_TEST_ACCOUNT_PATTERNS)


def assert_register_allowed(account: str) -> None:
    if is_production_api() and is_blocked_test_account(account):
        raise ValueError("该邮箱不可用于注册")
