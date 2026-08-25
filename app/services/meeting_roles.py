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
from typing import List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from app.models.models import User
from app.models.sales_models import (
    Membership, BrandSalesOrg, Opportunity,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP,
)
from app.models.scheduling_models import (
    SLOT_OPPORTUNITY_OWNER, SLOT_SALES_MANAGER, SLOT_PRODUCT_SPECIALIST,
    SLOT_ANY_REP, SLOT_LABELS,
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
DEFAULT_MEETING_TYPES = [
    {"key": "discovery", "name": "Discovery", "duration_minutes": 30,
     "required_slots": SLOT_OPPORTUNITY_OWNER,
     "optional_slots": SLOT_SALES_MANAGER,
     "description": "Qualify the business and capture discovery answers.",
     "sort_order": 1},
    {"key": "discovery_60", "name": "Discovery (60 min)", "duration_minutes": 60,
     "required_slots": SLOT_OPPORTUNITY_OWNER,
     "optional_slots": ",".join([SLOT_SALES_MANAGER, SLOT_PRODUCT_SPECIALIST]),
     "description": "Longer discovery for a more complex operation.",
     "sort_order": 2},
    {"key": "discovery_demo", "name": "Discovery + Demo", "duration_minutes": 60,
     "required_slots": ",".join([SLOT_OPPORTUNITY_OWNER, SLOT_SALES_MANAGER,
                                 SLOT_PRODUCT_SPECIALIST]),
     "description": "The three-person call this scheduling engine was built for.",
     "sort_order": 3},
    {"key": "demo", "name": "Product Demo", "duration_minutes": 60,
     "required_slots": ",".join([SLOT_OPPORTUNITY_OWNER, SLOT_PRODUCT_SPECIALIST]),
     "optional_slots": SLOT_SALES_MANAGER,
     "description": "Tailored demo built from the discovery answers.",
     "sort_order": 4},
    {"key": "proposal", "name": "Proposal Review", "duration_minutes": 30,
     "required_slots": SLOT_OPPORTUNITY_OWNER,
     "optional_slots": SLOT_SALES_MANAGER,
     "sort_order": 5},
    {"key": "closing", "name": "Closing Call", "duration_minutes": 60,
     "required_slots": ",".join([SLOT_OPPORTUNITY_OWNER, SLOT_SALES_MANAGER]),
     "sort_order": 6},
    {"key": "internal", "name": "Internal Sales Meeting", "duration_minutes": 30,
     "required_slots": SLOT_ANY_REP, "is_internal": True,
     "description": "Team meeting. No prospect attends.",
     "sort_order": 7},
]


def ensure_meeting_types(db: Session, brand_sales_org_id: str) -> List:
    """Create the default catalog for a brand the first time it is asked for.

    Idempotent by (brand_sales_org_id, key), so an edited or deleted type is
    never resurrected on top of the user's change.
    """
    from app.models.scheduling_models import MeetingType
    existing = {m.key for m in db.query(MeetingType).filter(
        MeetingType.brand_sales_org_id == brand_sales_org_id).all()}
    created = False
    for spec in DEFAULT_MEETING_TYPES:
        if spec["key"] in existing:
            continue
        db.add(MeetingType(brand_sales_org_id=brand_sales_org_id, **spec))
        created = True
    if created:
        db.flush()
    return (db.query(MeetingType)
            .filter(MeetingType.brand_sales_org_id == brand_sales_org_id,
                    MeetingType.is_active.is_(True))
            .order_by(MeetingType.sort_order.asc()).all())
