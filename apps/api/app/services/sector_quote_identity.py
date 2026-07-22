from __future__ import annotations

import re

from app.services.sector_registry_data import THEME_BOARD_PROVIDER_IDENTITIES


def requires_provider_identity_check(sector_label: str | None) -> bool:
    return str(sector_label or "").strip() in THEME_BOARD_PROVIDER_IDENTITIES


def provider_identity_matches(
    sector_label: str | None,
    *,
    expected_source_code: str | None,
    actual_security_name: str | None,
    actual_security_code: str | None = None,
) -> bool:
    """Validate a provider security before accepting any market value.

    Only explicitly registered high-risk mappings are strict.  Missing provider
    identity is a failure for those rows: availability may degrade, but an
    unrelated valid security can never be relabelled as the requested sector.
    """

    label = str(sector_label or "").strip()
    policy = THEME_BOARD_PROVIDER_IDENTITIES.get(label)
    if policy is None:
        return True

    allowed_codes = {
        _normalize_code(value) for value in policy.get("source_codes", ()) if value
    }
    expected_code = _normalize_code(expected_source_code)
    if not expected_code or expected_code not in allowed_codes:
        return False

    actual_code = _normalize_code(actual_security_code)
    if not actual_code or actual_code not in allowed_codes:
        return False

    actual_name = _normalize_name(actual_security_name)
    if not actual_name:
        return False
    allowed_names = {
        _normalize_name(value)
        for value in policy.get("security_names", ())
        if _normalize_name(value)
    }
    return any(
        actual_name == allowed
        or (len(allowed) >= 2 and allowed in actual_name)
        for allowed in allowed_names
    )


def _normalize_code(value: object) -> str:
    return str(value or "").strip().upper()


def _normalize_name(value: object) -> str:
    return re.sub(r"[\s（）()·\-—_]", "", str(value or "").strip()).casefold()
