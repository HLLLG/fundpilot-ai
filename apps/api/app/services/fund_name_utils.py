from __future__ import annotations


def normalize_fund_name(name: str) -> str:
    return (
        name.replace("...", "")
        .replace(".", "")
        .replace("·", "")
        .replace(" ", "")
        .strip()
    )


def is_fund_name_match(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return left in right or right in left
