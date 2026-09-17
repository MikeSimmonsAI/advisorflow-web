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

THE RELATIONSHIP IS SHARED WITH COMPENSATION. THE STOPPING RULE IS NOT.
----------------------------------------------------------------------
`compensation.upline()` walks this same column for commission overrides, and
the two must agree about WHO IS ABOVE WHOM - a rep whose manager earns an
override on their deals should be the same manager who attends their meetings.
`test_leadership_chain.py` asserts that agreement directly on a healthy chart,
so the two cannot drift.

They deliberately DIVERGE on one point: what to do at a seat that is no longer
active.

    compensation stops.   Paying an override to somebody who has left the
                          company is money the business does not owe, and
                          continuing past them would pay their manager twice.

    a meeting continues.  The question here is "who from this rep's line can
                          actually be in the room", and the answer when the
                          direct manager has gone is their manager - NOT
                          "nobody", and emphatically not somebody from another
                          line. Stopping would take a rep's booking link
                          offline the moment their manager left, which is
                          exactly when inbound leads must not be dropped.

That is why the walk below is written here rather than delegated. It is the
same column, the same brand scope and the same cycle guard; only the stopping
rule differs, and it differs for a stated reason. A dead seat that is walked
THROUGH is still recorded in `broken`, so an operator can see the chart needs
fixing even though bookings kept working.

WHAT ELSE THIS ADDS THAT COMPENSATION DOES NOT NEED
---------------------------------------------------
  * every returned leader must hold an ACTIVE seat AND an active account
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


def _any_membership(db: Session, user_id: Optional[str],
                    brand_sales_org_id: str) -> Optional[Membership]:
    """This person's seat here whether or not it is still active.

    Used ONLY to read `reports_to_user_id` so the walk can continue past a
    manager who has left. It is never the source of a leader - `resolve` checks
    `active_membership` separately before adding anybody. Prefers the active row
    when both exist, matching `sales_staff.get_membership`.
    """
    if not user_id or not brand_sales_org_id:
        return None
    rows = (db.query(Membership)
            .filter(Membership.user_id == user_id,
                    Membership.scope_type == SCOPE_BRAND_SALES_ORG,
                    Membership.scope_id == brand_sales_org_id,
                    Membership.role.in_(BRAND_SALES_ROLES))
            .order_by(Membership.created_at.desc()).all())
    for m in rows:
        if m.is_active:
            return m
    return rows[0] if rows else None


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

    # A self-reference produces an empty walk rather than a chain of one, so it
    # is detected up front rather than inferred from an empty result.
    if owner_membership.reports_to_user_id == owner_user_id:
        chain.status = CHAIN_CYCLE
        chain.broken.append({"user_id": owner_user_id, "reason": "reports_to_self"})
        return chain

    # ── the walk ────────────────────────────────────────────────────────────
    # Same column, same brand scope and same cycle guard as compensation's;
    # different stopping rule at a dead seat. See the module docstring.
    #
    # `seen` is the cycle guard and it is seeded with the owner, so a chart that
    # points back at the person it started from terminates on the first step
    # rather than on the depth limit.
    seen = {owner_user_id}
    current = owner_user_id
    hit_cycle = False

    for _ in range(depth):
        seat = _any_membership(db, current, brand_sales_org_id)
        nxt = getattr(seat, "reports_to_user_id", None) if seat else None
        if not nxt:
            break
        if nxt in seen:
            # A loop. Stop and say so - walking it again would return the same
            # people a second time and, at a large depth, do it repeatedly.
            hit_cycle = True
            break
        seen.add(nxt)
        current = nxt

        m = active_membership(db, nxt, brand_sales_org_id)
        u = _active_user(db, nxt)
        if m is None or u is None:
            # NAMED IN THE CHART, NOT AVAILABLE TO ATTEND. Recorded, walked
            # through, and never replaced with somebody from another line -
            # which is the behaviour this whole module exists to guarantee.
            chain.broken.append({
                "user_id": nxt,
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
        elif hit_cycle:
            chain.status = CHAIN_CYCLE
        elif not owner_membership.reports_to_user_id:
            chain.status = CHAIN_NO_LEADERSHIP
        else:
            chain.status = CHAIN_BROKEN_LINK
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
