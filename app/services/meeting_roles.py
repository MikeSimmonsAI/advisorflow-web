"""
Role-slot resolution: turning "a Discovery + Demo needs an Opportunity Owner, a
Sales Manager and a Product Specialist" into three actual people.

WHY THIS INDIRECTION EXISTS
---------------------------
Decision: meeting types must not hardcode participants to individual names. If
DISCOVERY_DEMO said "Blake, Michael, Mike", the type would be useless the moment
BookaBoost Sales runs its own discovery calls, and would silently break the day
Blake leaves.

So a meeting type asks for ROLES, and this module resolves them for one specific
opportunity in one specific brand:

    opportunity_owner   -> the opportunity's owner
    sales_manager       -> the brand's sales managers
    product_specialist  -> the brand's product specialists
    any_rep             -> any member of the brand sales org

For EvoSys Pro today that lands on Blake / Michael / Mike. For the next brand it
lands on whoever holds those seats there, with no code change.

PRODUCT SPECIALIST
------------------
There is no `product_specialist` membership role yet, and inventing a whole
global role for one seat was explicitly not wanted. Until one exists the slot
resolves to the brand's god-level owner participant — Mike — which is exactly
who fills it today. `resolve_slot` returns candidates plus a note saying so, so
the UI can show how the seat was filled rather than presenting it as configured.
"""
from datetime import datetime
from typing import List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from app.models.models import User
from app.models.sales_models import (
    Membership, BrandSalesOrg, Opportunity,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP,
)
from app.models.scheduling_models import (
    SLOT_OPPORTUNITY_OWNER, SLOT_SALES_MANAGER, SLOT_PRODUCT_SPECIALIST,
    SLOT_ANY_REP, SLOT_LABELS, LEADERSHIP_REPORTING_CHAIN,
)


def brand_members(db: Session, brand_sales_org_id: str,
                  role: Optional[str] = None) -> List[User]:
    """Active users holding a membership in this brand sales org."""
    q = (db.query(User)
         .join(Membership, Membership.user_id == User.id)
         .filter(Membership.scope_type == SCOPE_BRAND_SALES_ORG,
                 Membership.scope_id == brand_sales_org_id,
                 Membership.is_active.is_(True),
                 User.is_active.is_(True)))
    if role:
        q = q.filter(Membership.role == role)
    return q.order_by(User.full_name.asc()).all()


def resolve_slot(db: Session, slot: str, brand_sales_org_id: str,
                 opportunity: Optional[Opportunity] = None) -> Tuple[List[User], Optional[str]]:
    """Candidate users for one role slot, plus a note when the fill is inferred.

    Returns candidates rather than a single person: the caller (or the
    salesperson) picks. Auto-selecting one manager out of three and presenting
    it as "the" required attendee would be a guess wearing a fact's clothing.
    """
    if slot == SLOT_OPPORTUNITY_OWNER:
        if opportunity and opportunity.owner_user_id:
            u = db.query(User).filter(User.id == opportunity.owner_user_id,
                                      User.is_active.is_(True)).first()
            if u:
                return [u], None
        return [], "This opportunity has no owner assigned yet."

    if slot == SLOT_SALES_MANAGER:
        mgrs = brand_members(db, brand_sales_org_id, ROLE_SALES_MANAGER)
        return mgrs, None if mgrs else "No sales manager holds a membership in this brand."

    if slot == SLOT_PRODUCT_SPECIALIST:
        # No dedicated membership role exists yet — see the module docstring.
        # The brand's god-level owner is who actually fills this seat today.
        owners = [u for u in brand_members(db, brand_sales_org_id)
                  if getattr(u, "role", None) == "god_admin"]
        if owners:
            return owners, ("Filled by the platform owner. There is no dedicated "
                            "product-specialist role yet.")
        mgrs = brand_members(db, brand_sales_org_id, ROLE_SALES_MANAGER)
        return mgrs, ("No product specialist is defined; showing sales managers "
                      "as candidates.")

    if slot == SLOT_ANY_REP:
        return brand_members(db, brand_sales_org_id), None

    return [], "Unknown role slot '%s'." % slot


def resolve_meeting_slots(db: Session, meeting_type, brand_sales_org_id: str,
                          opportunity: Optional[Opportunity] = None) -> dict:
    """Resolve every slot on a meeting type into candidates.

    The shape returned drives the Find Team Time UI: each slot renders as a row
    with its candidates pre-selected where unambiguous, so the salesperson
    confirms rather than assembles.
    """
    def block(slots: Sequence[str], required: bool):
        out = []
        for s in slots:
            users, note = resolve_slot(db, s, brand_sales_org_id, opportunity)
            out.append({
                "slot": s,
                "label": SLOT_LABELS.get(s, s),
                "required": required,
                "note": note,
                # Exactly one candidate means there is nothing to choose, so
                # pre-select it. More than one is a real decision left to the user.
                "auto_selected_user_id": users[0].id if len(users) == 1 else None,
                "candidates": [{"id": u.id, "full_name": u.full_name, "email": u.email}
                               for u in users],
            })
        return out

    required = block(meeting_type.required_slot_list(), True)
    optional = block(meeting_type.optional_slot_list(), False)
    unresolved = [s["label"] for s in required if not s["candidates"]]
    return {"required": required, "optional": optional, "unresolved": unresolved}


# ── seeded meeting types ────────────────────────────────────────────────────
# A template, not a hardcoding: these are created per brand sales org on first
# use and are editable rows from then on. Changing a brand's meeting types must
# never need a migration.
# `requires_video` (Checkpoint 4): every CUSTOMER-FACING type gets a video
# meeting; the internal one deliberately does not. An internal pipeline review
# does not need a Zoom room, and creating one anyway burns a concurrent-meeting
# slot and fills the host's Zoom account with rooms nobody joins.
DEFAULT_MEETING_TYPES = [
    {"key": "discovery", "name": "Discovery", "duration_minutes": 30,
     "required_slots": SLOT_OPPORTUNITY_OWNER,
     "optional_slots": SLOT_SALES_MANAGER,
     "description": "Qualify the business and capture discovery answers.",
     "requires_video": True,
     "sort_order": 1},
    {"key": "discovery_60", "name": "Discovery (60 min)", "duration_minutes": 60,
     "required_slots": SLOT_OPPORTUNITY_OWNER,
     "optional_slots": ",".join([SLOT_SALES_MANAGER, SLOT_PRODUCT_SPECIALIST]),
     "description": "Longer discovery for a more complex operation.",
     "requires_video": True,
     "sort_order": 2},
    # ── THE INBOUND TYPE. The only one open to the public by default. ──────
    #
    # `required_slots` is UNCHANGED and still drives the internal "Find Team
    # Time" screen, where a human reads a candidate list and chooses. The
    # leadership columns beside it drive the PUBLIC path, where nobody reads
    # anything - and they are a quorum, not an intersection: the owner plus at
    # least one leader from that owner's own reporting chain, with any other
    # available leader invited too.
    #
    # The two coexist on one row on purpose. A brand's Discovery + Demo is one
    # meeting whether a rep books it or a stranger does; what differs is who
    # decides the attendees, not what the meeting is.
    {"key": "discovery_demo", "name": "Discovery + Demo", "duration_minutes": 60,
     "required_slots": ",".join([SLOT_OPPORTUNITY_OWNER, SLOT_SALES_MANAGER,
                                 SLOT_PRODUCT_SPECIALIST]),
     "description": "The three-person call this scheduling engine was built for.",
     "requires_video": True,
     "leadership_policy": LEADERSHIP_REPORTING_CHAIN,
     "owner_required": True,
     "leadership_minimum": 1,
     "leadership_depth": 2,
     "include_additional_leaders": True,
     "public_bookable": True,
     "sort_order": 3},
    {"key": "demo", "name": "Product Demo", "duration_minutes": 60,
     "required_slots": ",".join([SLOT_OPPORTUNITY_OWNER, SLOT_PRODUCT_SPECIALIST]),
     "optional_slots": SLOT_SALES_MANAGER,
     "description": "Tailored demo built from the discovery answers.",
     "requires_video": True,
     "sort_order": 4},
    {"key": "proposal", "name": "Proposal Review", "duration_minutes": 30,
     "required_slots": SLOT_OPPORTUNITY_OWNER,
     "optional_slots": SLOT_SALES_MANAGER,
     "requires_video": True,
     "sort_order": 5},
    {"key": "closing", "name": "Closing Call", "duration_minutes": 60,
     "required_slots": ",".join([SLOT_OPPORTUNITY_OWNER, SLOT_SALES_MANAGER]),
     "requires_video": True,
     "sort_order": 6},
    {"key": "internal", "name": "Internal Sales Meeting", "duration_minutes": 30,
     "required_slots": SLOT_ANY_REP, "is_internal": True,
     "description": "Team meeting. No prospect attends.",
     "requires_video": False,
     "sort_order": 7},
]


def _edited_by_a_person(row, spec=None) -> bool:
    """Has a HUMAN changed this meeting type?

    THE BUG THIS REPLACES, because it was silent and it was permanent.

    This used to be `updated_at == created_at`, read as "nobody has touched the
    row". But `ensure_meeting_types` runs SEVERAL system backfills over these
    same rows, and each one moves `updated_at`. The requires_video backfill runs
    first and writes to exactly the rows the quorum backfill cares about - so
    from the next call onward the quorum backfill saw a row that looked
    hand-edited, skipped it, and skipped it forever. The brand kept a correctly
    seeded Discovery + Demo that its own website answered with "not available
    right now", and no screen anywhere could show why.

    A system write must not be able to masquerade as a person's decision. So the
    system stamps `system_defaults_at` whenever it writes, and only a change
    AFTER that stamp is a person.

    NULL `system_defaults_at` means the system has never stamped this row. Those
    rows are treated as never edited by a person, and that is provable rather
    than optimistic: until the god control shipped, NO endpoint, screen or
    service in this codebase could write to a meeting type at all except this
    function. Every `updated_at` on a legacy row is therefore a system write by
    construction. The control that CAN edit one stamps `system_defaults_at`
    itself, so a real human decision is protected from the first moment it is
    possible to make one.
    """
    stamp = getattr(row, "system_defaults_at", None)
    if stamp is not None:
        return row.updated_at is not None and row.updated_at > stamp

    # UNSTAMPED: the column is newer than the row, so there is no stamp to
    # compare against and `updated_at` on its own is worthless - it moved for
    # every legacy row the moment any backfill ran.
    #
    # So ask the question a different way: does this row still look like the one
    # this module seeds? The system owns the shipped name and duration. A row
    # that still carries both is the system's own, whatever its timestamps say,
    # and may receive the defaults it missed. A row somebody has made their own -
    # renamed, re-timed - is theirs, and the system keeps out of it even though
    # it cannot prove when that happened.
    if spec is None:
        return False
    if (row.name or "") != spec.get("name"):
        return True
    if row.duration_minutes != spec.get("duration_minutes"):
        return True
    return False


def ensure_meeting_types(db: Session, brand_sales_org_id: str) -> List:
    """Create the default catalog for a brand the first time it is asked for.

    Idempotent by (brand_sales_org_id, key), so an edited or deleted type is
    never resurrected on top of the user's change.
    """
    from app.models.scheduling_models import MeetingType
    rows = (db.query(MeetingType)
            .filter(MeetingType.brand_sales_org_id == brand_sales_org_id).all())
    existing = {m.key: m for m in rows}
    created = False
    # Every row THIS call writes a system default to. Stamped after the flush
    # below so no backfill can leave a row looking hand-edited to the next one.
    system_touched = set()
    for spec in DEFAULT_MEETING_TYPES:
        if spec["key"] in existing:
            continue
        db.add(MeetingType(brand_sales_org_id=brand_sales_org_id, **spec))
        created = True

    # ── one-time backfill of requires_video (Checkpoint 4) ──────────────────
    # Idempotency by key means an ALREADY-SEEDED brand — which EvoSys Pro is in
    # production — would never pick up `requires_video`, and would silently
    # create no Zoom meetings forever. The column shipped with DEFAULT FALSE, so
    # on a pre-existing row FALSE means "predates the feature", not "somebody
    # turned it off".
    #
    # Guarded on updated_at == created_at, i.e. the row has NEVER been edited.
    # The moment anyone changes a meeting type by hand, their choice is
    # permanent and this never touches it again — which is what stops a
    # deliberate "no video on closing calls" being undone on every startup.
    for spec in DEFAULT_MEETING_TYPES:
        row = existing.get(spec["key"])
        if row is None or not spec.get("requires_video"):
            continue
        if getattr(row, "requires_video", False):
            continue
        if _edited_by_a_person(row, spec):
            continue
        row.requires_video = True
        system_touched.add(row)
        created = True

    # ── one-time backfill of the leadership quorum (2026-09-17) ────────────
    #
    # THE SAME PROBLEM THE VIDEO BACKFILL ABOVE SOLVES, FOR THE SAME REASON.
    # Idempotency by key means a brand that was seeded before this feature -
    # which every existing brand was - would never pick up the policy, and its
    # Discovery + Demo would stay unbookable from the website forever while
    # looking correctly configured.
    #
    # THE RULE FOR EXISTING ROWS, STATED EXPLICITLY.
    #
    #   * Only rows whose `leadership_policy` is still NULL are considered. A
    #     brand that has configured its own policy keeps it, full stop.
    #   * Only rows that have NEVER been edited by hand are touched, guarded on
    #     updated_at == created_at, exactly as the video backfill is. The moment
    #     anybody changes a meeting type, their choice is permanent and this
    #     never touches it again. That is what stops a deliberate "we do not
    #     take website bookings on this type" being undone on every startup.
    #   * `required_slots` is NOT modified. The internal Find Team Time screen
    #     keeps behaving exactly as it does today; the quorum applies to the
    #     public path only.
    #   * Nothing is turned on for any OTHER meeting type. Discovery, Demo,
    #     Proposal, Closing and Internal keep `leadership_policy` NULL and
    #     `public_bookable` FALSE, so their behaviour is byte-for-byte what it
    #     was before this shipped.
    #
    # A brand still has to have a reporting chain and a default inbound owner
    # configured before anything can actually be booked; this backfill makes the
    # meeting type ready, not the brand.
    _QUORUM_KEYS = {spec["key"] for spec in DEFAULT_MEETING_TYPES
                    if spec.get("leadership_policy")}
    for spec in DEFAULT_MEETING_TYPES:
        if spec["key"] not in _QUORUM_KEYS:
            continue
        row = existing.get(spec["key"])
        if row is None:
            continue
        if getattr(row, "leadership_policy", None):
            continue          # the brand has its own policy
        if _edited_by_a_person(row, spec):
            continue          # a person has decided this; their choice stands
        system_touched.add(row)
        row.leadership_policy = spec["leadership_policy"]
        row.owner_required = spec["owner_required"]
        row.leadership_minimum = spec["leadership_minimum"]
        row.leadership_depth = spec["leadership_depth"]
        row.include_additional_leaders = spec["include_additional_leaders"]
        row.public_bookable = spec["public_bookable"]
        created = True

    if created:
        db.flush()

    # STAMPED AFTER THE FLUSH, and `updated_at` is written explicitly alongside.
    # The flush is what fires `onupdate` and sets `updated_at`; stamping before
    # it would leave the stamp fractionally behind and make the system's own
    # write look like a person's on the very next call. Assigning both to one
    # instant is what keeps `_edited_by_a_person` false until somebody really
    # does edit it.
    if system_touched:
        stamp = datetime.utcnow()
        for row in system_touched:
            row.updated_at = stamp
            row.system_defaults_at = stamp
        db.flush()

    return (db.query(MeetingType)
            .filter(MeetingType.brand_sales_org_id == brand_sales_org_id,
                    MeetingType.is_active.is_(True))
            .order_by(MeetingType.sort_order.asc()).all())
