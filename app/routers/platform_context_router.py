"""PLATFORM OVERVIEW and CONTEXT SELECTION for the platform owner.

The owner operates three levels: the platform as a whole, one white-label brand,
and one customer inside that brand. Before this router the middle and bottom
levels existed only in the browser - `POST /god/orgs/{id}/impersonate` validated
that an org existed, wrote a line to the Python logger, and handed the client a
header value to store in localStorage. The server had no idea which customer the
owner was looking at, so nothing could be audited and the UI's idea of "where am
I" was authoritative. That is backwards.

Three things this router establishes.

CONTEXT IS RESOLVED SERVER-SIDE. `GET /god/platform/context` reports what the
backend actually resolved from the request, so the banner in the UI can be told
by the server whose records are about to be changed rather than trusting its own
localStorage.

ENTERING A CONTEXT CREATES NO MEMBERSHIP. This is the load-bearing rule of the
whole design and it is asserted by a gate, not just by intent. The owner viewing
SCI does not become an SCI user; they remain neutral and stop being in that
context the moment they exit. Nothing in this file constructs a Membership.

CONTEXT ACTIONS ARE AUDITED TO THE DATABASE. Entering and leaving a customer are
now `AuditLogEntry` rows carrying the acting owner, the platform, the brand and
the customer - not `log.info` lines that vanish with the container. The owner is
recorded as themselves; they are never turned into a fake tenant user to make
the audit schema fit.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.models.models import User, Organization, Platform
from app.models.sales_models import BrandSalesOrg, Membership
from app.routers.audit_log_router import log_action
from app.services import platform_owner as po

router = APIRouter(prefix="/god/platform", tags=["platform-context"])


# ── context resolution ──────────────────────────────────────────────────────

def _org_brief(db: Session, org: Organization) -> dict:
    return {
        "id": org.id,
        "name": org.name,
        "slug": org.slug,
        "plan": org.plan,
        "industry": org.industry,
        "is_active": bool(org.is_active),
        "platform_id": org.platform_id,
    }


def _resolved_context(db: Session, user: User) -> dict:
    """What the SERVER thinks the owner is currently looking at."""
    org_id = po.selected_org_id(user)
    org = db.query(Organization).filter(Organization.id == org_id).first() if org_id else None
    platform = (
        db.query(Platform).filter(Platform.id == org.platform_id).first()
        if org is not None and org.platform_id else None
    )
    return {
        "level": "customer" if org is not None else "platform",
        "platform": None if platform is None else {
            "id": platform.id, "name": platform.name, "slug": platform.slug,
        },
        "customer": None if org is None else _org_brief(db, org),
        # The banner copy, decided by the server so every client says the same
        # thing. "VIEWING: <customer> — <brand> customer".
        "banner": (
            None if org is None
            else "VIEWING: %s%s" % (
                org.name,
                ("  ·  %s customer" % platform.name) if platform is not None else "",
            )
        ),
        "is_neutral": org is None,
    }


@router.get("/context")
def current_context(db: Session = Depends(get_db), user: User = Depends(require_god)):
    """Server-side truth for the persistent context indicator."""
    return _resolved_context(db, user)


# ── platform overview ───────────────────────────────────────────────────────

@router.get("/overview")
def platform_overview(db: Session = Depends(get_db), user: User = Depends(require_god)):
    """The owner's default landing state. No customer selected, by design.

    The platform pseudo-org is excluded from every count and list here - it is
    not a customer, and counting it made 'how many customers do we have' wrong
    by one on every screen that asked.
    """
    platforms = db.query(Platform).order_by(Platform.name.asc()).all()

    orgs = po.exclude_platform_org(db.query(Organization)).all()
    brands = db.query(BrandSalesOrg).all()

    by_platform = {}
    for o in orgs:
        by_platform.setdefault(o.platform_id, []).append(o)
    brands_by_platform = {}
    for b in brands:
        brands_by_platform.setdefault(b.platform_id, []).append(b)

    unassigned = [o for o in orgs if not o.platform_id]

    return {
        "context": _resolved_context(db, user),
        "platforms": [
            {
                "id": p.id, "name": p.name, "slug": p.slug,
                "domain": p.domain, "is_active": bool(p.is_active),
                "customer_count": len(by_platform.get(p.id, [])),
                "active_customer_count": sum(
                    1 for o in by_platform.get(p.id, []) if o.is_active),
                "brand_sales_orgs": [
                    {"id": b.id, "name": b.name, "slug": b.slug,
                     "is_active": bool(b.is_active)}
                    for b in brands_by_platform.get(p.id, [])
                ],
            }
            for p in platforms
        ],
        "totals": {
            "platforms": len(platforms),
            "customers": len(orgs),
            "active_customers": sum(1 for o in orgs if o.is_active),
            "brand_sales_orgs": len(brands),
        },
        # Reported, not hidden. An org with no platform sits outside every
        # scoping decision in the system, so the owner should be able to see
        # that it exists and fix it rather than discovering it by accident.
        "unassigned_customers": [_org_brief(db, o) for o in unassigned],
    }


@router.get("/brands/{platform_id}/customers")
def brand_customers(platform_id: str, db: Session = Depends(get_db),
                    user: User = Depends(require_god)):
    """Customers belonging to one brand — the second step of context selection."""
    platform = db.query(Platform).filter(Platform.id == platform_id).first()
    if not platform:
        raise HTTPException(status_code=404, detail="Platform not found")
    orgs = po.exclude_platform_org(
        db.query(Organization).filter(Organization.platform_id == platform_id)
    ).order_by(Organization.name.asc()).all()
    return {
        "platform": {"id": platform.id, "name": platform.name, "slug": platform.slug},
        "customers": [_org_brief(db, o) for o in orgs],
    }


# ── entering and leaving a context ──────────────────────────────────────────

def _membership_count(db: Session, user_id: str) -> int:
    return db.query(Membership).filter(Membership.user_id == user_id).count()


class EnterResponse(BaseModel):
    context: dict
    header_name: str
    header_value: Optional[str]
    memberships_before: int
    memberships_after: int


@router.post("/context/customer/{org_id}", response_model=EnterResponse)
def enter_customer(org_id: str, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require_god)):
    """Enter a customer's context administratively.

    NO MEMBERSHIP IS CREATED. The response carries the membership count before
    and after precisely so that the claim is checkable from outside rather than
    being a promise in a docstring - if these two numbers ever differ, the
    design has been broken and the gate fails.
    """
    if po.is_platform_pseudo_org(org_id):
        raise HTTPException(
            status_code=400,
            detail="The AdvisorFlow platform account is not a customer organization.")

    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Customer organization not found")

    before = _membership_count(db, user.id)

    log_action(
        db, org.id, user.id,
        action="platform_owner.enter_customer",
        target_type="organization", target_id=org.id,
        platform_id=org.platform_id,
        details={
            "customer_name": org.name,
            "customer_slug": org.slug,
            "from_ip": request.client.host if request.client else "unknown",
            # Recorded because the whole point is that it stays zero.
            "membership_created": False,
        },
        note="Administrative context entry by the platform owner. No membership granted.",
    )

    after = _membership_count(db, user.id)

    # Build the response context by hand: `user` still reflects THIS request,
    # which had no override header on it.
    platform = (db.query(Platform).filter(Platform.id == org.platform_id).first()
                if org.platform_id else None)
    return EnterResponse(
        context={
            "level": "customer",
            "platform": None if platform is None else {
                "id": platform.id, "name": platform.name, "slug": platform.slug},
            "customer": _org_brief(db, org),
            "banner": "VIEWING: %s%s" % (
                org.name, ("  ·  %s customer" % platform.name) if platform else ""),
            "is_neutral": False,
        },
        header_name="X-Org-Override",
        header_value=org.id,
        memberships_before=before,
        memberships_after=after,
    )


@router.post("/context/exit", response_model=EnterResponse)
def exit_context(request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_god)):
    """Leave the current customer context and return to the platform level.

    After this the owner is neutral again: no org, no membership, no residue.
    """
    org_id = po.selected_org_id(user)
    org = db.query(Organization).filter(Organization.id == org_id).first() if org_id else None

    before = _membership_count(db, user.id)

    if org is not None:
        log_action(
            db, org.id, user.id,
            action="platform_owner.exit_customer",
            target_type="organization", target_id=org.id,
            platform_id=org.platform_id,
            details={
                "customer_name": org.name,
                "from_ip": request.client.host if request.client else "unknown",
            },
            note="Platform owner returned to the platform level.",
        )

    after = _membership_count(db, user.id)

    return EnterResponse(
        context={"level": "platform", "platform": None, "customer": None,
                 "banner": None, "is_neutral": True},
        header_name="X-Org-Override",
        header_value=None,
        memberships_before=before,
        memberships_after=after,
    )


# ── owner neutrality: audit, then repair, never silently ────────────────────

@router.get("/owner-state")
def owner_state(db: Session = Depends(get_db), user: User = Depends(require_god)):
    """Is the platform owner actually neutral, and what is attached to them?

    Read-only on purpose. The instruction was to audit the existing state before
    changing it and not to blindly null memberships, so this reports and the
    repair below is a separate call the owner has to make deliberately.
    """
    state = po.owner_state(db, user)
    problems = []
    if state["attached_to_pseudo_org"]:
        problems.append(
            "The owner account is attached to the platform pseudo-organization "
            "'%s'. Requests already treat this as neutral, but the stored row "
            "still says otherwise." % po.GOD_PLATFORM_ORG_ID)
    if state["attached_to_customer_org"]:
        problems.append(
            "The owner account is attached to a REAL customer organization (%s). "
            "That is a tenancy, not a control-plane authority, and it should be "
            "reviewed by hand rather than cleared automatically."
            % state["organization_id"])
    non_sales = [m for m in state["memberships"] if m["scope_type"] != "brand_sales_org"]
    if non_sales:
        problems.append(
            "%d membership(s) on the owner are not brand-sales seats and need a "
            "human decision." % len(non_sales))
    return {
        **state,
        "problems": problems,
        "safe_to_neutralize": state["attached_to_pseudo_org"] and not non_sales,
        "neutralize_hint": "POST /god/platform/owner-neutralize with "
                           "{\"confirm\": \"NEUTRALIZE PLATFORM OWNER\"}",
    }


class NeutralizeRequest(BaseModel):
    confirm: str


@router.post("/owner-neutralize")
def owner_neutralize(req: NeutralizeRequest, db: Session = Depends(get_db),
                     user: User = Depends(require_god)):
    """Detach the owner account from the platform pseudo-organization.

    Deliberately narrow. It clears `organization_id` ONLY when the current value
    is the pseudo-org, and it does not touch memberships at all - a brand-sales
    seat on the owner is legitimate (Mike genuinely sells), and a membership
    that is not is a judgement call, not a cleanup.

    If the owner is attached to a real customer organization this refuses and
    says so, because clearing that would be deleting a fact rather than
    correcting an artifact.
    """
    if req.confirm != "NEUTRALIZE PLATFORM OWNER":
        raise HTTPException(
            status_code=400,
            detail="Type the confirmation exactly: NEUTRALIZE PLATFORM OWNER")

    fresh = db.query(User).filter(User.id == user.id).first()
    if fresh is None:
        raise HTTPException(status_code=404, detail="User not found")

    before_org = fresh.organization_id

    if before_org is None:
        return {"changed": False, "organization_id": None,
                "message": "The owner account was already neutral."}

    if not po.is_platform_pseudo_org(before_org):
        raise HTTPException(
            status_code=409,
            detail="The owner is attached to a real customer organization (%s), not "
                   "the platform placeholder. Refusing to clear it automatically - "
                   "review that tenancy by hand." % before_org)

    fresh.organization_id = None
    log_action(
        db, None, user.id,
        action="platform_owner.neutralized",
        target_type="user", target_id=user.id,
        before={"organization_id": before_org},
        after={"organization_id": None},
        note="Owner detached from the platform pseudo-organization. "
             "Memberships deliberately untouched.",
        commit=False,
    )
    db.commit()

    return {
        "changed": True,
        "organization_id": None,
        "previous_organization_id": before_org,
        "memberships_untouched": _membership_count(db, user.id),
        "message": "The platform owner is now neutral.",
    }
