"""
Slot selection when a meeting needs the rep plus SOME of their leadership.

THE RULE THAT REPLACED "EVERYONE MUST BE FREE"
----------------------------------------------
`availability.find_shared_slots` intersects every required participant: a slot
survives only if all of them are free. For a three-person chain that is the
wrong operation. Requiring the rep AND their manager AND their manager's
manager loses a slot whenever any one of the three is busy, which on real
calendars is most of the week - and the prospect, who only ever needed one
decision-maker in the room, is shown nothing.

The rule is a QUORUM:

    the opportunity owner (if the policy requires them)
    + AT LEAST `leadership_minimum` leaders from THAT owner's reporting chain

    owner free, direct manager free,  skip-level busy  → BOOKABLE, owner + direct
    owner free, direct manager busy,  skip-level free  → BOOKABLE, owner + skip
    owner free, both leaders free                      → BOOKABLE, all three
                                                         when the policy says so
    owner free, no leader free                         → NOT OFFERED
    owner busy, both leaders free                      → NOT OFFERED

WHAT THIS MODULE DOES NOT DO, AND MUST NOT
------------------------------------------
It does not compute availability. Every rule about when a person is free -
working hours, lunch, PTO, buffers, minimum notice, booking horizon, existing
meetings, external Outlook and Google commitments, DST - lives in
`availability.free_intervals_for_user` and is reached through exactly one call
per person here. A second implementation of any of those rules would mean the
public website and the internal scheduler disagreeing about whether somebody is
free, and the public one would be the version nobody tests against a real
calendar.

It also does not fall back. If the owner's chain yields no free leader, the
answer is no slot. There is deliberately no path from here to "well, some other
manager in the brand is free" - see leadership_chain for why that is a defect
rather than a convenience.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from app.models.models import User
from app.services import availability as av
from app.services import leadership_chain as lc

Interval = av.Interval

# The most raw candidate starts to consider when the caller asks for an
# uncapped answer. Generous enough to cover a two-month window at a 15-minute
# grid, bounded so an unbounded range cannot be turned into unbounded work.
CANDIDATE_CEILING = 5000


# ── the policy, read off a meeting type ─────────────────────────────────────

class QuorumPolicy(object):
    """What a meeting type asks for, with the defaults spelled out.

    Built from a MeetingType row, but constructible directly so the rules can be
    tested without a database. Reading the columns through here rather than
    inline at the call sites means the legacy/NULL case is decided in ONE place.
    """

    __slots__ = ("policy", "owner_required", "minimum", "depth",
                 "include_additional")

    def __init__(self, policy=None, owner_required=True, minimum=0, depth=0,
                 include_additional=False):
        self.policy = policy
        self.owner_required = bool(owner_required)
        self.minimum = max(0, int(minimum or 0))
        self.depth = max(0, int(depth or 0))
        self.include_additional = bool(include_additional)

    @property
    def active(self) -> bool:
        """Does this meeting type use leadership quorum at all?

        NULL policy means the type predates this feature and keeps its existing
        required/optional-slot behaviour untouched. That is what makes shipping
        this safe for every meeting type already configured in every brand.
        """
        from app.models.scheduling_models import LEADERSHIP_POLICIES
        return self.policy in LEADERSHIP_POLICIES

    @classmethod
    def from_meeting_type(cls, mt) -> "QuorumPolicy":
        if mt is None:
            return cls(policy=None)
        return cls(
            policy=getattr(mt, "leadership_policy", None),
            owner_required=getattr(mt, "owner_required", True),
            minimum=getattr(mt, "leadership_minimum", 0),
            depth=getattr(mt, "leadership_depth", 0),
            include_additional=getattr(mt, "include_additional_leaders", False),
        )

    def describe(self) -> dict:
        return {"policy": self.policy, "owner_required": self.owner_required,
                "leadership_minimum": self.minimum, "leadership_depth": self.depth,
                "include_additional_leaders": self.include_additional}


# ── the computation ─────────────────────────────────────────────────────────

def _covers(free: Sequence[Interval], start: datetime, end: datetime) -> bool:
    """Is this whole slot inside one continuous free interval for this person?

    One interval, not several adjacent ones. `normalize` in the availability
    engine already merges touching intervals, so two separate intervals here
    means a genuine gap in the middle of the meeting.
    """
    return any(fs <= start and end <= fe for fs, fe in free)


def quorum_slots(db: Session,
                 owner: User,
                 leaders: Sequence[User],
                 policy: QuorumPolicy,
                 start_utc: datetime,
                 end_utc: datetime,
                 duration_minutes: int,
                 now_utc: Optional[datetime] = None,
                 step_minutes: int = av.DEFAULT_SLOT_STEP_MINUTES,
                 exclude_appointment_id: Optional[str] = None,
                 limit: int = 200) -> dict:
    """Bookable openings, each carrying the exact people who would be invited.

    Every returned slot is a complete answer: the participant list is decided
    HERE, at the same moment and from the same free/busy data that made the slot
    bookable. The booking call does not get to re-derive it from a name or a
    role, which is what stops a leader who was busy at 2pm ending up on the 2pm
    invitation.
    """
    now_utc = now_utc or datetime.utcnow()

    people: List[User] = []
    if owner is not None:
        people.append(owner)
    for u in leaders:
        if u is not None and u.id != getattr(owner, "id", None):
            people.append(u)

    free: Dict[str, List[Interval]] = {}
    for u in people:
        if u.id in free:
            continue
        free[u.id] = av.free_intervals_for_user(
            db, u, start_utc, end_utc, now_utc=now_utc,
            duration_minutes=duration_minutes,
            exclude_appointment_id=exclude_appointment_id)

    # THE CANDIDATE GRID COMES FROM THE OWNER when the owner must attend.
    # Generating candidates from the union of everybody and then filtering would
    # offer times the rep can never make, and would grow the grid with the size
    # of the chain for no benefit.
    if policy.owner_required and owner is not None:
        base = free.get(owner.id, [])
    else:
        base = av.normalize([iv for u in people for iv in free.get(u.id, [])])

    # CANDIDATES ARE GENERATED GENEROUSLY, THEN FILTERED.
    #
    # More raw candidates than the caller's limit, because the quorum filter
    # below discards the ones where no leader is free - taking exactly `limit`
    # candidates would return fewer than `limit` bookable slots and look like a
    # thin calendar.
    #
    # `limit=0` from the caller means "do not cap the ANSWER", and it must not
    # be forwarded: `slots_from_intervals` returns after its first slot when
    # given 0, because its guard is `len(out) >= limit`. Passing it through was
    # a real defect - the booking path calls this with limit=0 to re-check one
    # specific time, and would have found only the first candidate of the day,
    # so every booking except the earliest offered one was refused as
    # unavailable. Caught by a test that books a second slot.
    candidate_cap = (limit * 4) if limit else CANDIDATE_CEILING
    raw = av.slots_from_intervals(base, duration_minutes, step_minutes,
                                  limit=candidate_cap)

    ordered_leaders = [u for u in leaders if u is not None
                       and u.id != getattr(owner, "id", None)]

    slots = []
    for s, e in raw:
        available = [u for u in ordered_leaders if _covers(free.get(u.id, []), s, e)]
        if len(available) < policy.minimum:
            continue
        if policy.include_additional:
            chosen = available
        else:
            # Nearest-first. The rep's own manager is a more appropriate
            # attendee than somebody two levels up who happens to be free.
            chosen = available[:policy.minimum]
        participants = ([owner.id] if (owner is not None and policy.owner_required)
                        else [])
        participants += [u.id for u in chosen]
        slots.append({
            "starts_at": s,
            "ends_at": e,
            "owner_user_id": getattr(owner, "id", None),
            "leader_user_ids": [u.id for u in chosen],
            "participant_user_ids": participants,
            # Every leader who COULD have made it, for internal diagnostics.
            # Never returned to a public caller.
            "available_leader_user_ids": [u.id for u in available],
        })
        if limit and len(slots) >= limit:
            break

    return {
        "slots": slots,
        "policy": policy.describe(),
        "free_by_user": free,
        "owner_user_id": getattr(owner, "id", None),
        "leader_user_ids": [u.id for u in ordered_leaders],
    }


def explain_empty(owner: Optional[User], leaders: Sequence[User],
                  policy: QuorumPolicy, free: Dict[str, List[Interval]]) -> str:
    """Why there is nothing to offer. Internal wording - it names people.

    "No times available" with no reason is the least useful thing a scheduler
    can say, and it is worse here than usual: the three causes - the rep's week
    is full, the chain is misconfigured, nobody's free hours overlap - need
    three different people to do three different things.
    """
    if owner is None:
        return "No salesperson could be resolved for this booking."
    if policy.owner_required and not free.get(owner.id):
        return "%s has no free time in this window." % (owner.full_name or owner.email)
    if policy.minimum and not leaders:
        return ("No leader could be resolved from this salesperson's reporting "
                "chain, and this meeting requires at least %d." % policy.minimum)
    if policy.minimum and not any(free.get(u.id) for u in leaders):
        names = ", ".join(u.full_name or u.email for u in leaders)
        return ("No leader in this salesperson's chain has free time in this "
                "window (%s)." % names)
    return ("Everyone has free time in this window, but never enough of them at "
            "the same moment to meet the meeting's requirements.")


def slot_is_still_open(db: Session,
                       participants: Sequence[User],
                       starts_at: datetime,
                       ends_at: datetime,
                       now_utc: Optional[datetime] = None,
                       exclude_appointment_id: Optional[str] = None) -> List[User]:
    """Re-ask the availability engine about ONE specific slot. Returns who is NOT free.

    THE RECHECK IS NOT OPTIONAL AND IT IS NOT THE SAME AS THE CONFLICT CHECK.
    The slot list a visitor is looking at was computed against an external-busy
    cache with a ten-minute TTL and may have been on their screen for far longer
    than that while they typed their company name. `find_conflicts` in the
    booking path catches a clash with one of OUR appointments; this catches
    everything else the engine knows about - a manager who booked PTO, a lunch
    block that moved, a working-hours change, a horizon that has since rolled.

    `ignore_notice=False` is deliberate: a slot that has drifted inside the
    minimum-notice window since it was offered is genuinely no longer bookable.
    """
    now_utc = now_utc or datetime.utcnow()
    duration = int((ends_at - starts_at).total_seconds() // 60)
    unavailable = []
    for u in participants:
        free = av.free_intervals_for_user(
            db, u, starts_at - timedelta(hours=1), ends_at + timedelta(hours=1),
            now_utc=now_utc, duration_minutes=0,
            exclude_appointment_id=exclude_appointment_id)
        if not _covers(free, starts_at, ends_at):
            unavailable.append(u)
    return unavailable


def resolve_and_find(db: Session,
                     owner_user_id: Optional[str],
                     brand_sales_org_id: str,
                     policy: QuorumPolicy,
                     start_utc: datetime,
                     end_utc: datetime,
                     duration_minutes: int,
                     now_utc: Optional[datetime] = None,
                     limit: int = 200) -> dict:
    """Chain resolution plus slot search, which is what every caller wants.

    Returns a `chain_status` that is NOT "ok" whenever the configuration cannot
    support the policy, with no slots. A public caller turns that into "we
    cannot take bookings for this person right now"; a setup screen turns it
    into the specific instruction in CHAIN_STATUS_MESSAGES.
    """
    chain = lc.resolve(db, owner_user_id, brand_sales_org_id,
                       policy.depth if policy.active else 0)

    if chain.owner is None:
        return {"slots": [], "chain_status": chain.status, "chain": chain,
                "reason": chain.message, "policy": policy.describe()}

    if policy.active and not chain.satisfies(policy.minimum):
        # Not enough leadership EXISTS. No point searching calendars - and
        # saying "no times available" here would send somebody looking at
        # diaries for a problem that lives in the org chart.
        status = chain.status if chain.status != lc.CHAIN_OK else lc.CHAIN_NO_LEADERSHIP
        return {"slots": [], "chain_status": status, "chain": chain,
                "reason": lc.CHAIN_STATUS_MESSAGES.get(status, ""),
                "policy": policy.describe()}

    leaders = chain.leaders if policy.active else []
    found = quorum_slots(db, chain.owner, leaders, policy, start_utc, end_utc,
                         duration_minutes, now_utc=now_utc, limit=limit)
    found["chain_status"] = chain.status
    found["chain"] = chain
    if not found["slots"]:
        found["reason"] = explain_empty(chain.owner, leaders, policy,
                                        found.get("free_by_user", {}))
    else:
        found["reason"] = None
    return found
