"""
Who counts as leadership for ONE salesperson's meeting.

THE DEFECT THIS EXISTS TO PREVENT
---------------------------------
`meeting_roles.resolve_slot(SLOT_SALES_MANAGER, ...)` returns every user
holding a sales-manager membership in the brand. For an internal booking that
is fine: a human is looking at the list and picking. For an INBOUND booking
nobody is looking - the platform computes availability against whoever it
resolves and offers the result to a stranger on the website.

So a rep in Kentucky would have their public calendar intersected with managers
in Texas who have never met them, never manage them, and will not be told why a
meeting appeared. The bigger the brand gets, the more wrong the answer becomes,
and it degrades silently: the times on offer just get worse.

The correct source is the org chart the brand already maintains:
`Membership.reports_to_user_id`, scoped to one brand sales org.

    Blake  → Michael → Mike            Blake's leadership is [Michael, Mike]
    Josh   → KY Manager → KY Director  Josh's leadership is [KY Manager, KY Director]

Josh's booking never considers Michael or Mike, and no amount of growth in the
Texas team changes what Josh's link offers.

THOSE NAMES APPEAR IN THIS DOCSTRING AND NOWHERE ELSE IN THE MODULE.

WHY THIS IS NOT A SECOND TRAVERSAL
----------------------------------
`compensation.upline()` already walks this exact relationship - brand-scoped,
active-membership-only, cycle-guarded, depth-bounded - because commission
overrides have always needed the same answer. Writing a second walker here
would mean a rep whose manager is paid an override on their deals could be a
different person from the manager who attends their meetings, and the two would
drift apart the first time one was fixed.

So `upline()` IS the traversal. This module adds what a MEETING needs on top of
it, which compensation does not care about:

  * the chain member must still be an ACTIVE seat in this brand - compensation
    pays whoever the chart names, but a meeting needs somebody who can attend
  * the user account must be active
  * the result is Users, not ids, because the availability engine takes Users
  * an empty or broken chain is an explicit, named CONFIGURATION STATUS rather
    than an empty list the caller might read as "nobody is free"

THAT LAST POINT IS THE WHOLE POINT OF THE STATUS VOCABULARY. "No times
available" and "this rep has no manager configured" look identical to a visitor
and are completely different problems. One is a busy week; the other is a setup
error that will silently lose every inbound lead until somebody notices.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.models import User
from app.models.sales_models import (
    Membership, SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, BRAND_SALES_ROLES,
)

# ── Configuration statuses ───────────────────────────────────────────────────
# Returned rather than raised. A public endpoint answering a visitor must be
# able to say "we cannot take a booking right now" without a stack trace, while
# an internal setup screen needs the specific reason.

CHAIN_OK = "ok"
# The person a booking code resolved to has no active seat in this brand.
CHAIN_OWNER_NOT_A_MEMBER = "owner_not_a_member"
# They have a seat, but nobody above them. Not an error in itself - it is only a
# problem when the meeting policy requires a leader.
CHAIN_NO_LEADERSHIP = "no_leadership_configured"
# The chart names somebody who is not an active seat here. Distinguished from
# the above because it is a BROKEN configuration rather than an absent one, and
# the fix is different: somebody was deactivated without their reports being
# moved.
CHAIN_BROKEN_LINK = "broken_link"
# reports_to points at the person themselves, or a loop exists.
CHAIN_CYCLE = "cycle_detected"

CHAIN_STATUS_MESSAGES = {
    CHAIN_OK: "",
    CHAIN_OWNER_NOT_A_MEMBER:
        "This salesperson does not hold an active seat in this brand's sales "
        "organization, so no meeting can be scheduled for them here.",
    CHAIN_NO_LEADERSHIP:
        "This salesperson has no reporting manager configured in this brand, so "
        "there is no leadership chain to draw an attendee from.",
    CHAIN_BROKEN_LINK:
        "This salesperson's reporting manager no longer holds an active seat in "
        "this brand. Their reporting line needs to be reassigned.",
    CHAIN_CYCLE:
        "This brand's reporting chart contains a loop, so the leadership chain "
        "cannot be resolved.",
}


@dataclass
class LeadershipChain:
    """The resolved answer for one salesperson in one brand.

    `leaders` is nearest-first: index 0 is the direct manager. That order is
    load-bearing - when a policy books fewer leaders than are available, it
    books the nearest ones, because the person who manages this rep is a more
    appropriate attendee than their manager's manager.
    """
    owner: Optional[User] = None
    leaders: List[User] = field(default_factory=list)
    status: str = CHAIN_OK
    # Ids the chart named that could not be used, with why. Surfaced to setup
    # screens; never to a public visitor.
    broken: List[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == CHAIN_OK

    @property
    def message(self) -> str:
        return CHAIN_STATUS_MESSAGES.get(self.status, "")

    @property
    def leader_ids(self) -> List[str]:
        return [u.id for u in self.leaders]

    def satisfies(self, minimum: int) -> bool:
        """Enough leadership EXISTS for this policy - says nothing about free time."""
        return len(self.leaders) >= max(0, int(minimum or 0))


# ── membership lookup ───────────────────────────────────────────────────────

def active_membership(db: Session, user_id: str,
                      brand_sales_org_id: str) -> Optional[Membership]:
    """This person's live brand-sales seat here, or None.

    Restricted to BRAND_SALES_ROLES on purpose. A platform-scoped executive
    grant is a different kind of membership row in the same table, and it is not
    a seat that sells or attends sales meetings.
    """
    if not user_id or not brand_sales_org_id:
        return None
    return (db.query(Membership)
            .filter(Membership.user_id == user_id,
                    Membership.scope_type == SCOPE_BRAND_SALES_ORG,
                    Membership.scope_id == brand_sales_org_id,
                    Membership.role.in_(BRAND_SALES_ROLES),
                    Membership.is_active.is_(True))
            .first())


def _active_user(db: Session, user_id: Optional[str]) -> Optional[User]:
    if not user_id:
        return None
    return (db.query(User)
            .filter(User.id == user_id, User.is_active.is_(True))
            .first())


# ── the resolver ────────────────────────────────────────────────────────────

def resolve(db: Session, owner_user_id: Optional[str], brand_sales_org_id: str,
            depth: int) -> LeadershipChain:
    """The leadership chain above `owner_user_id`, inside this brand only.

    `depth` is how many levels to climb. 0 returns the owner with no leaders,
    which is the right answer for a meeting type that has no leadership policy -
    not an error.
    """
    chain = LeadershipChain()

    owner_membership = active_membership(db, owner_user_id, brand_sales_org_id)
    owner = _active_user(db, owner_user_id)
    if owner_membership is None or owner is None:
        chain.status = CHAIN_OWNER_NOT_A_MEMBER
        return chain
    chain.owner = owner

    depth = max(0, int(depth or 0))
    if depth == 0:
        return chain

    # THE TRAVERSAL IS COMPENSATION'S. Imported here rather than reimplemented -
    # see the module docstring. It returns ids nearest-first, walks only inside
    # this brand sales org, and stops on a repeat, which is the cycle guard.
    from app.services.compensation import upline
    raw_ids = upline(db, owner_user_id, brand_sales_org_id, depth)

    # A self-reference produces an empty walk rather than a chain of one, so it
    # is detected here rather than inferred from the result.
    if owner_membership.reports_to_user_id == owner_user_id:
        chain.status = CHAIN_CYCLE
        chain.broken.append({"user_id": owner_user_id, "reason": "reports_to_self"})
        return chain

    for uid in raw_ids:
        m = active_membership(db, uid, brand_sales_org_id)
        u = _active_user(db, uid)
        if m is None or u is None:
            # NAMED IN THE CHART, NOT USABLE IN A MEETING. Recorded and skipped -
            # never silently replaced with somebody else, which is the whole
            # behaviour this module exists to prevent.
            chain.broken.append({
                "user_id": uid,
                "reason": "no_active_membership" if m is None else "inactive_user",
            })
            continue
        chain.leaders.append(u)

    if not chain.leaders:
        # Distinguish "nobody was ever named" from "the person named is gone".
        # They need different fixes and a single "no leadership" message would
        # send somebody looking in the wrong place.
        if chain.broken:
            chain.status = CHAIN_BROKEN_LINK
        elif not owner_membership.reports_to_user_id:
            chain.status = CHAIN_NO_LEADERSHIP
        else:
            # reports_to is set, upline returned nothing, nothing was recorded
            # as broken: the only way to get here is a loop upline refused to
            # walk.
            chain.status = CHAIN_CYCLE
    return chain


def chain_summary(db: Session, owner_user_id: Optional[str],
                  brand_sales_org_id: str, depth: int) -> dict:
    """The same answer as a plain dict, for setup and diagnostic screens.

    INTERNAL ONLY. It names people, which is exactly what a public payload must
    not do - a visitor establishing who reports to whom inside a company from a
    booking page is the org chart leaking through an availability endpoint.
    """
    c = resolve(db, owner_user_id, brand_sales_org_id, depth)
    return {
        "status": c.status,
        "ok": c.ok,
        "message": c.message,
        "owner": ({"id": c.owner.id, "full_name": c.owner.full_name,
                   "email": c.owner.email} if c.owner else None),
        "leaders": [{"id": u.id, "full_name": u.full_name, "email": u.email,
                     "level": i + 1}
                    for i, u in enumerate(c.leaders)],
        "broken": list(c.broken),
        "depth_searched": max(0, int(depth or 0)),
    }


def is_manager_seat(db: Session, user_id: str, brand_sales_org_id: str) -> bool:
    """Does this person hold an ACTIVE MANAGER seat in this brand?

    Used when validating a reporting line, never to build one. Leadership for a
    meeting comes from the chart, not from the role - a manager somewhere in the
    brand is not thereby leadership for every rep in it.
    """
    m = active_membership(db, user_id, brand_sales_org_id)
    return bool(m is not None and m.role == ROLE_SALES_MANAGER)
