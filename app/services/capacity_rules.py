"""Pure (stdlib-only) capacity rules.

One authoritative decision for "is there room, and why not", shared by the hold
path (lead_capacity), the user-facing refusal and the Billing screen, so the
three can never disagree. No DB, no network.
"""

from __future__ import annotations

from typing import Iterable, Optional

SOURCE_CATALOGUE = "catalogue_plan"
SOURCE_OVERRIDE = "tenant_override"
SOURCE_UNLIMITED = "unlimited"

# Ceilings that limit_for() reads as "unlimited" when <= 0. Recording 0 for one
# of these would silently remove the cap it appears to set.
CEILING_DIMENSIONS = ("max_leads", "max_users", "max_locations")


def normalize_ceiling(raw) -> Optional[int]:
    """A stored ceiling as an enforceable int, or None for unlimited/malformed."""
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def validate_snapshot_ceilings(data: dict, unlimited: Optional[Iterable[str]],
                               dimensions: Iterable[str]) -> list:
    """Reasons a tenant-override write must be refused (empty list = ok)."""
    errors = []
    unlimited = set(unlimited or [])
    for dim in dimensions:
        if dim not in data or data[dim] is None:
            continue
        try:
            value = int(data[dim])
        except (TypeError, ValueError):
            errors.append("%s must be a whole number." % dim)
            continue
        if value < 0:
            errors.append("%s cannot be negative." % dim)
        elif value == 0 and dim in CEILING_DIMENSIONS:
            errors.append("%s of 0 would be treated as unlimited. Enter a "
                          "positive ceiling, or name it in `unlimited`." % dim)
        if dim in unlimited:
            errors.append("%s is both given a ceiling and listed as unlimited."
                          % dim)
    return errors


def decide(limit: Optional[int], used: int, adding: int = 1,
           source: str = SOURCE_CATALOGUE, plan_key: Optional[str] = None,
           bought: int = 0) -> dict:
    """The single capacity decision. `limit` None means unlimited."""
    if limit is None:
        return {"allowed": True, "limit": None, "used": used, "unlimited": True,
                "source": SOURCE_UNLIMITED if source != SOURCE_OVERRIDE else source,
                "plan_key": plan_key, "reason": None}
    allowed = (used + adding) <= limit
    reason = None
    if not allowed:
        reason = ("%d of %d used (%s%s); adding %d would exceed the ceiling."
                  % (used, limit,
                     "explicit tenant override" if source == SOURCE_OVERRIDE
                     else "catalogue plan %s" % (plan_key or "unknown"),
                     ", incl. %d purchased" % bought if bought else "", adding))
    return {"allowed": allowed, "limit": limit, "used": used, "adding": adding,
            "unlimited": False, "source": source, "plan_key": plan_key,
            "reason": reason}
