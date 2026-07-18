from __future__ import annotations

import argparse
import os

from app.services.admin_user_management import (
    AdminManagementError,
    promote_bootstrap_admin,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Promote one explicit existing account to initial administrator."
    )
    parser.add_argument(
        "--account",
        default=os.getenv("FUND_AI_BOOTSTRAP_ADMIN_ACCOUNT", ""),
        help="Existing account email (or FUND_AI_BOOTSTRAP_ADMIN_ACCOUNT).",
    )
    args = parser.parse_args()
    try:
        changed = promote_bootstrap_admin(args.account)
    except AdminManagementError as exc:
        parser.error(str(exc))
    print("Administrator bootstrap completed." if changed else "Administrator already configured.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
