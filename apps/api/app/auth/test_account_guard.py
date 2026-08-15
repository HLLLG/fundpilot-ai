from __future__ import annotations

import os
import re

# 与 pytest / Playwright / smoke 脚本里常见的测试账号格式对齐
BLOCKED_TEST_ACCOUNT_PATTERNS = (
    r"@example\.com$",
    r"@t\.com$",
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
    """显式 production 环境视为生产 API，禁止注册测试账号。"""
    if os.getenv("FUND_AI_ALLOW_TEST_ACCOUNTS", "").strip().lower() in {"1", "true", "yes"}:
        return False
    return os.getenv("FUND_AI_APP_ENV", "").strip().lower() == "production"


def is_blocked_test_account(account: str) -> bool:
    normalized = account.strip().lower()
    return any(re.search(pattern, normalized) for pattern in BLOCKED_TEST_ACCOUNT_PATTERNS)


def assert_register_allowed(account: str) -> None:
    if is_production_api() and is_blocked_test_account(account):
        raise ValueError("该邮箱不可用于注册")
