"""
The opaque public identifier on a salesperson's booking link.

WHAT A PROSPECT SEES
--------------------
    https://<brand>/book/pQ7mHk2xR9tLvN4wYc3bZa

and nothing else. Not a user id, not an email, not a name, not an organization
id, not a membership id, and above all nothing that says who manages whom.

WHY THAT MATTERS MORE THAN IT LOOKS
-----------------------------------
A public booking link is an unauthenticated endpoint that resolves to a specific
employee. If the identifier were a user id, anyone could walk the id space and
enumerate the brand's sales team; if it were derived from an email or a name,
the same is true with less effort. And the moment a booking page can be pointed
at an arbitrary person, an outsider can put meetings on the calendar of anybody
in the company - including people who do not take inbound bookings at all.

So the code is CSPRNG bytes with no structure, the same choice
`AppointmentConfirmationToken` makes for the same class of problem, and it maps
to a membership rather than to a user.

THE FOUR REFUSALS
-----------------
A code resolves only if ALL of these hold. They are separate checks because
they are separate facts, and collapsing them would let one cover for another:

    the code exists                        - otherwise it is a typo or a probe
    it has not been revoked                - a link posted somewhere it should
                                             not have been dies on its own,
                                             without removing anyone from the team
    the membership is still active         - somebody who left stops taking
                                             bookings whether or not anyone
                                             remembered to revoke their link
    the membership is in THE BRAND ASKED   - a code is scoped to one brand's
                                             sales org and cannot be replayed
                                             against another brand's website

That last one is the cross-brand boundary, and it is enforced by comparing the
membership's scope to the brand the REQUEST resolved to server-side. The browser
never says which brand it is - see public_booking.
"""

import secrets
from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app.models.models import User
from app.models.sales_models import (
    BrandSalesOrg, Membership, SCOPE_BRAND_SALES_ORG, BRAND_SALES_ROLES,
    INBOUND_DEFAULT_OWNER,
)

# 32 bytes of CSPRNG, URL-safe. Long enough that guessing is not a strategy and
# short enough to survive being pasted into an email signature.
CODE_BYTES = 24

# ── resolution outcomes ─────────────────────────────────────────────────────
# Returned, never raised: the public endpoint answers a stranger and must not
# leak which of these it was. Internally they are distinct because they need
# different fixes.
CODE_OK           = "ok"
CODE_UNKNOWN      = "unknown_code"
CODE_REVOKED      = "code_revoked"
CODE_INACTIVE     = "member_inactive"
CODE_WRONG_BRAND  = "wrong_brand"

# What a PUBLIC caller is told, for every failure above. One sentence, the same
# sentence, deliberately. Telling a prospect "that code was revoked" versus "no
# such code" is telling an outsider which codes exist.
PUBLIC_REFUSAL = ("That booking link is no longer valid. Please use the booking "
                  "page on our website, or ask for a new link.")


def generate_code() -> str:
    return secrets.token_urlsafe(CODE_BYTES)


def issue_code(db: Session, membership: Membership,
               now: Optional[datetime] = None, rotate: bool = False) -> str:
    """Give this seat a booking code, or return the one it has.

    `rotate=True` replaces it. Rotation is safe by construction: an appointment
    records its OWNER and its opportunity, never the code that introduced them,
    so no history is touched. The old link simply stops resolving.
    """
    now = now or datetime.utcnow()
    if membership.booking_code and not rotate and not membership.booking_code_revoked_at:
        return membership.booking_code
    # Unique index on the column; a collision at 24 CSPRNG bytes is not a
    # realistic event, but retrying costs nothing and guessing is not allowed.
    for _ in range(5):
        candidate = generate_code()
        clash = (db.query(Membership)
                 .filter(Membership.booking_code == candidate).first())
        if clash is None:
            membership.booking_code = candidate
            membership.booking_code_issued_at = now
            membership.booking_code_revoked_at = None
            db.flush()
            return candidate
    raise RuntimeError("could not generate a unique booking code")


def revoke_code(db: Session, membership: Membership,
                now: Optional[datetime] = None) -> None:
    """Kill this link without touching the seat.

    The code VALUE is kept rather than nulled, so a later question about a
    booking that arrived through it can still be answered.
    """
    membership.booking_code_revoked_at = now or datetime.utcnow()
    db.flush()


def resolve_code(db: Session, code: Optional[str],
                 brand_sales_org_id: str) -> Tuple[Optional[User], Optional[Membership], str]:
    """(user, membership, status) for a public booking code in THIS brand.

    `brand_sales_org_id` is resolved server-side from the request's brand and is
    never taken from the browser. Passing it in is what makes a code from one
    brand unusable against another - the query below simply does not find it.
    """
    if not code or not str(code).strip():
        return None, None, CODE_UNKNOWN
    code = str(code).strip()

    m = db.query(Membership).filter(Membership.booking_code == code).first()
    if m is None:
        return None, None, CODE_UNKNOWN
    if m.booking_code_revoked_at is not None:
        return None, m, CODE_REVOKED
    if m.scope_type != SCOPE_BRAND_SALES_ORG or m.scope_id != brand_sales_org_id:
        # Found, but not here. Reported as wrong_brand internally; the public
        # caller gets PUBLIC_REFUSAL like every other failure.
        return None, m, CODE_WRONG_BRAND
    if not m.is_active or m.role not in BRAND_SALES_ROLES:
        return None, m, CODE_INACTIVE

    user = db.query(User).filter(User.id == m.user_id,
                                 User.is_active.is_(True)).first()
    if user is None:
        return None, m, CODE_INACTIVE
    return user, m, CODE_OK


# ── who takes a booking with no code ────────────────────────────────────────

OWNER_OK               = "ok"
OWNER_FROM_CODE        = "from_code"
OWNER_FROM_DEFAULT     = "from_default"
OWNER_NOT_CONFIGURED   = "no_inbound_owner_configured"
OWNER_DEFAULT_INVALID  = "default_owner_not_a_member"


def resolve_inbound_owner(db: Session, bso: BrandSalesOrg,
                          code: Optional[str] = None) -> dict:
    """The salesperson this booking belongs to, before any calendar is consulted.

    ORDER MATTERS AND IS FIXED: a code wins if it resolves. Somebody who
    followed a specific person's link is booking with that person, and silently
    routing them to the brand's default owner because their link had a typo
    would be worse than refusing.

    THE NO-CODE PATH FAILS CLOSED. With no code and no configured default owner
    this returns `no_inbound_owner_configured` and books nothing. There is no
    branch that picks a user - not the first manager, not the least busy rep,
    not the brand's creator. An arbitrary assignment puts a stranger's meeting
    on a real person's calendar and a real prospect into a pipeline nobody is
    watching, and it does it silently, which is how inbound leads disappear.

    The `source` in the result is what later reporting reads to answer "how many
    of our bookings came through a rep's own link" - the question that says
    whether the personal links are worth maintaining.
    """
    if code:
        user, membership, status = resolve_code(db, code, bso.id)
        if status == CODE_OK:
            return {"ok": True, "owner": user, "membership": membership,
                    "source": OWNER_FROM_CODE, "status": OWNER_OK,
                    "code_status": status}
        # A supplied-but-bad code is a refusal, not a reason to fall through to
        # the default owner.
        return {"ok": False, "owner": None, "membership": membership,
                "source": None, "status": status, "code_status": status,
                "message": PUBLIC_REFUSAL}

    mode = bso.inbound_assignment_mode or INBOUND_DEFAULT_OWNER
    if mode != INBOUND_DEFAULT_OWNER:
        # A mode this build does not implement. Refuse rather than guess - see
        # BrandSalesOrg.inbound_assignment_mode for why the column exists.
        return {"ok": False, "owner": None, "membership": None, "source": None,
                "status": OWNER_NOT_CONFIGURED, "code_status": None,
                "message": "This brand's inbound assignment is not configured."}

    owner_id = getattr(bso, "default_inbound_owner_user_id", None)
    if not owner_id:
        return {"ok": False, "owner": None, "membership": None, "source": None,
                "status": OWNER_NOT_CONFIGURED, "code_status": None,
                "message": "This brand has no default inbound salesperson configured."}

    from app.services import leadership_chain as lc
    membership = lc.active_membership(db, owner_id, bso.id)
    user = (db.query(User).filter(User.id == owner_id,
                                  User.is_active.is_(True)).first())
    if membership is None or user is None:
        # Configured, but the person named is no longer a seat here. A DIFFERENT
        # problem from "nothing configured", and it needs somebody to go and
        # name a new person rather than to discover the setting for the first
        # time.
        return {"ok": False, "owner": None, "membership": membership, "source": None,
                "status": OWNER_DEFAULT_INVALID, "code_status": None,
                "message": "This brand's default inbound salesperson no longer "
                           "holds an active seat in the sales organization."}

    return {"ok": True, "owner": user, "membership": membership,
            "source": OWNER_FROM_DEFAULT, "status": OWNER_OK, "code_status": None}
