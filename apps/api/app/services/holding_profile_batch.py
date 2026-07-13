from __future__ import annotations

from collections.abc import Sequence

from app.models import FundProfile, Holding


class ProfilesNotProvided:
    pass


PROFILES_NOT_PROVIDED = ProfilesNotProvided()
ProfilesSnapshotArg = Sequence[FundProfile] | None | ProfilesNotProvided
MatchedProfilesArg = Sequence[FundProfile | None] | None | ProfilesNotProvided


def resolve_matched_profiles(
    holdings: list[Holding],
    *,
    profiles_snapshot: ProfilesSnapshotArg = PROFILES_NOT_PROVIDED,
    matched_profiles: MatchedProfilesArg = PROFILES_NOT_PROVIDED,
) -> list[FundProfile | None]:
    """Resolve one request-local profile per holding without point queries.

    Omitting both optional inputs performs one bulk profile read. Explicit ``None``
    or an empty sequence means the caller has supplied an empty snapshot/match set,
    so no database fallback is attempted.
    """

    if not holdings:
        return []

    if not isinstance(matched_profiles, ProfilesNotProvided):
        if matched_profiles is None or len(matched_profiles) == 0:
            return [None] * len(holdings)
        resolved = list(matched_profiles)
    else:
        if isinstance(profiles_snapshot, ProfilesNotProvided):
            from app.database import list_fund_profiles

            profiles = list_fund_profiles()
        else:
            profiles = list(profiles_snapshot or [])
        from app.services.fund_profile import match_profiles_to_holdings

        resolved = match_profiles_to_holdings(holdings, profiles)

    if len(resolved) != len(holdings):
        raise ValueError("matched_profiles must align one-to-one with holdings")
    return resolved
