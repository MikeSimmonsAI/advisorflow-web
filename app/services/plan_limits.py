"""
Plan limits — server-side enforcement of what a subscription actually buys.

═══════════════════════════════════════════════════════════════════════════
THE GAP THIS CLOSES
═══════════════════════════════════════════════════════════════════════════

`brand_billing_plans` has carried `max_leads` and `max_users` since the
catalogue was built. They were returned to the UI, rendered on plan cards, and
ENFORCED BY NOTHING - no query anywhere read them. A Starter customer whose
plan advertised "up to 2 users" could add fifty, and the only thing standing
between them and doing so was a number on a marketing card.

Frontend hiding is not enforcement.

═══════════════════════════════════════════════════════════════════════════
WHICH PLAN'S LIMIT APPLIES, WHICH IS THE WHOLE SUBTLETY
═══════════════════════════════════════════════════════════════════════════

`Organization.billing_plan_key` - what they are entitled to TODAY - and never
`billing_pending_plan_key`.

That distinction is the decided downgrade policy expressed in code. A customer
who schedules a downgrade from Professional to Starter has ALREADY PAID for
Professional through the end of the period. Reading the pending key would take
their fourth and fifth user away the moment they clicked the button, for a
change that has not happened and money they have not saved.

The webhook clears the pending marker when Stripe's schedule actually
advances, and at that moment - not before - this function starts returning the
lower limit.

═══════════════════════════════════════════════════════════════════════════
WHAT THIS DELIBERATELY DOES NOT DO
═══════════════════════════════════════════════════════════════════════════

NO OVERAGE BILLING. Nothing here charges for exceeding a limit, because no
metering or overage policy has been decided and inventing one would be
inventing revenue.

NO RETROACTIVE ENFORCEMENT. An organization already over its limit - because
it downgraded, or because the limit was introduced after they grew - is not
broken, locked, or pruned. Existing records keep working; the limit stops the
NEXT addition. Deleting or disabling a customer's users to make a number fit
is not a billing decision anyone would sanction.

NO LIMIT WHERE NONE IS CONFIGURED. NULL means unlimited, and a brand that has
not set a number gets no ceiling rather than a guessed one.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.models import Organization, User

log = logging.getLogger(__name__)


LIMIT_USERS = "max_users"
LIMIT_LEADS = "max_leads"


def effective_plan(db: Session, org: Optional[Organization]):
    """The plan whose limits apply RIGHT NOW.

    Deliberately `billing_plan_key`, never `billing_pending_plan_key` - see the
    module docstring. Returns None when the organization has no resolvable
    plan, which is a real and common state (never subscribed, mid-provisioning,
    brand catalogue not yet seeded) and must not be treated as "zero of
    everything".
    """
    if org is None:
        return None
    from app.services import billing_catalog
    return billing_catalog.resolve_plan(
        db, getattr(org, "platform_id", None),
        getattr(org, "billing_plan_key", None) or getattr(org, "plan", None))


def limit_for(db: Session, org: Optional[Organization], key: str) -> Optional[int]:
    """The configured ceiling, or None for unlimited / unconfigured."""
    plan = effective_plan(db, org)
    if plan is None:
        return None
    value = getattr(plan, key, None)
    if value is None:
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def usage_for(db: Session, org: Organization, key: str) -> int:
    """How many they are using today. Counts only what the limit is about."""
    if key == LIMIT_USERS:
        # ACTIVE users. A deactivated account is not consuming a seat, and
        # counting it would mean an organization could never recover from
        # hitting the ceiling except by deleting people.
        return (db.query(User)
                .filter(User.organization_id == org.id,
                        User.is_active == True)      # noqa: E712
                .count())
    if key == LIMIT_LEADS:
        from app.models.models import Lead
        return (db.query(Lead)
                .filter(Lead.organization_id == org.id)
                .count())
    return 0


def check(db: Session, org: Optional[Organization], key: str,
          adding: int = 1) -> dict:
    """Would adding `adding` more exceed this plan's ceiling?

    Returns a dict rather than a bool so a caller can report the real numbers.
    "You have reached your plan's limit of 2 users" is actionable; "forbidden"
    is not.
    """
    limit = limit_for(db, org, key)
    if limit is None or org is None:
        return {"allowed": True, "limit": None, "used": None, "unlimited": True}

    used = usage_for(db, org, key)
    return {
        "allowed": (used + adding) <= limit,
        "limit": limit,
        "used": used,
        "adding": adding,
        "unlimited": False,
    }


def require_capacity(db: Session, org: Optional[Organization], key: str,
                     adding: int = 1) -> None:
    """Refuse the addition if it would exceed the plan. 402, not 403.

    402 Payment Required rather than 403 Forbidden, matching the entitlement
    gate this sits beside: the caller is not unauthorized, their PLAN does not
    include this. The two are different problems with different fixes, and
    telling somebody they lack permission when they actually need a bigger plan
    sends them to the wrong person.
    """
    result = check(db, org, key, adding=adding)
    if result["allowed"]:
        return

    noun = "users" if key == LIMIT_USERS else "leads"
    raise HTTPException(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        detail=("This plan includes up to %d %s and %d are already in use. "
                "Upgrade the plan to add more."
                % (result["limit"], noun, result["used"])))


def report(db: Session, org: Optional[Organization]) -> dict:
    """Every limit and its usage, for the Billing screen and God Mode.

    Also reports the PENDING plan's limits where one is scheduled, so a
    customer can see what they will drop to before it happens rather than
    discovering it when an action starts failing.
    """
    plan = effective_plan(db, org)
    out = {
        "plan": getattr(plan, "key", None),
        "limits": {},
        "pending_plan": getattr(org, "billing_pending_plan_key", None) if org else None,
        "pending_effective_at": getattr(org, "billing_pending_effective_at", None) if org else None,
        "pending_limits": {},
    }
    for key in (LIMIT_USERS, LIMIT_LEADS):
        out["limits"][key] = check(db, org, key, adding=0)

    pending_key = out["pending_plan"]
    if pending_key and org is not None:
        from app.services import billing_catalog
        pending = billing_catalog.resolve_plan(
            db, getattr(org, "platform_id", None), pending_key)
        if pending is not None:
            for key in (LIMIT_USERS, LIMIT_LEADS):
                value = getattr(pending, key, None)
                out["pending_limits"][key] = {
                    "limit": int(value) if value else None,
                    "used": usage_for(db, org, key),
                    "unlimited": value is None,
                }
    return out
