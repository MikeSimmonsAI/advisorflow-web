"""God Mode sales operations, provisioning and implementation control (Checkpoint 6).

MOUNTED AT /god/ops, ALONGSIDE the existing /god router rather than inside it.
God Mode already had platforms, orgs, users and impersonation; this is the
operating layer on top, and keeping it in its own module means the Checkpoint 6
surface can be read, reviewed and tested as one thing.

AUTHORITY, ROUTE BY ROUTE
-------------------------
Everything here except the provisioning endpoints is `require_god`. Provisioning
uses `require_sales_member` at the door and then `assert_can_provision` on the
record, which lets a brand's own sales manager provision their own brand's Won
deal while a rep - and any manager of any other brand - is refused.

THE 404-NOT-401 ORDERING RULE
-----------------------------
FastAPI resolves decorator `dependencies=[...]` BEFORE the endpoint signature's
parameters. That fact cost a bug in Checkpoint 5.5, where production leaked 401
instead of 404 because `get_current_user` ran before the environment guard. It
does not bite here - every route in this file is authenticated by design - but
the ordering is worth stating where the next person will read it.

NO SECRETS ARE LOGGED. The activation token is returned in exactly one response
body, to the operator who created it, and appears in no audit entry, no log line
and no database column.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.models.models import Organization, Platform, User, AuditLogEntry
from app.models.sales_models import Opportunity, BrandSalesOrg, BrandPackage
from app.models.implementation_models import (
    Implementation, CustomerActivation,
    IMPLEMENTATION_STATUSES, IMPLEMENTATION_STATUS_LABELS,
    MILESTONE_STATUSES, INVITE_ACCEPTED,
)
from app.services import god_operations as ops
from app.services import provisioning as prov
from app.services import implementation_service as impls
from app.services import customer_activation as activation
from app.services.sales_access import require_sales_member, is_god
from app.services import staff_activation as staff_access
from app.services import sales_staff
from app.models.staff_models import StaffActivation, PURPOSE_SETUP, PURPOSE_RESET
from app.models.sales_models import (
    Membership, SCOPE_BRAND_SALES_ORG, BRAND_SALES_ROLES, ROLE_SALES_MANAGER,
)
from app.routers.audit_log_router import log_action

router = APIRouter(prefix="/god/ops", tags=["God Mode — Operations"])


# ══ sales operations ════════════════════════════════════════════════════════

@router.get("/sales-operations")
def sales_operations(db: Session = Depends(get_db),
                     user: User = Depends(require_god)):
    """§2 — every owner question answered from real rows in one payload."""
    return ops.sales_operations(db)


@router.get("/brands")
def brands(platform_id: Optional[str] = None,
           db: Session = Depends(get_db),
           user: User = Depends(require_god)):
    return {"brands": ops.brands(db, platform_id=platform_id)}


@router.get("/brands/{brand_sales_org_id}")
def brand_detail(brand_sales_org_id: str,
                 db: Session = Depends(get_db),
                 user: User = Depends(require_god)):
    """§3/§4 — one brand's operating summary plus the configuration that
    actually exists behind it. No generic settings framework: every field below
    is backed by a real column somebody can already edit."""
    bso = db.query(BrandSalesOrg).filter(BrandSalesOrg.id == brand_sales_org_id).first()
    if bso is None:
        raise HTTPException(status_code=404, detail="Brand sales organisation not found.")
    packages = (db.query(BrandPackage)
                  .filter(BrandPackage.platform_id == bso.platform_id)
                  .order_by(BrandPackage.sort_order, BrandPackage.name).all()
                if bso.platform_id else [])
    return {
        "summary": ops.brand_summary(db, bso),
        "configuration": {
            "brand_sales_org": {"id": bso.id, "name": bso.name, "slug": bso.slug,
                                "timezone": bso.timezone, "is_active": bool(bso.is_active)},
            "packages": [{"id": p.id, "key": p.key, "name": p.name,
                          "price": float(p.price) if p.price is not None else None,
                          "setup_fee": float(p.setup_fee) if p.setup_fee is not None else None,
                          "currency": p.currency, "billing_period": p.billing_period,
                          "is_custom": bool(p.is_custom), "is_active": bool(p.is_active),
                          # Deliberately surfaced as-is. billing_plan_key is the
                          # unwired link to the legacy Stripe plans and stays
                          # unwired: the sales packages and the Stripe products
                          # are different things at different prices (§19).
                          "billing_plan_key": p.billing_plan_key}
                         for p in packages],
        },
        "implementations": ops.implementations(db, brand_sales_org_id=bso.id),
    }


@router.get("/queues")
def queues(db: Session = Depends(get_db), user: User = Depends(require_god)):
    """§37 — the actionable exception queues, each resolvable from a real screen."""
    return ops.decision_queues(db)


# ══ won queue and provisioning ══════════════════════════════════════════════

@router.get("/won-queue")
def won_queue(db: Session = Depends(get_db),
              user: User = Depends(require_sales_member)):
    """Won deals that have no implementation yet.

    Visible to sales managers for their own brands (§16) and to god for
    everything. A rep sees only their own deals - reps cannot provision, but
    seeing that their own Won deal is still waiting is legitimate.
    """
    from app.services.sales_access import sales_org_ids, is_sales_manager
    q = db.query(Opportunity).filter(Opportunity.status == "won")
    if not is_god(user):
        allowed = sales_org_ids(user, db)
        if not allowed:
            return {"opportunities": []}
        q = q.filter(Opportunity.brand_sales_org_id.in_(allowed))
    rows = q.order_by(Opportunity.won_at.desc()).all()

    provisioned = {r[0] for r in db.query(Implementation.opportunity_id).all()}
    out = []
    for o in rows:
        if o.id in provisioned:
            continue
        if not is_god(user) and not is_sales_manager(user, db, o.brand_sales_org_id):
            if o.owner_user_id != user.id:
                continue
        bso = (db.query(BrandSalesOrg).filter(BrandSalesOrg.id == o.brand_sales_org_id).first()
               if o.brand_sales_org_id else None)
        owner = (db.query(User).filter(User.id == o.owner_user_id).first()
                 if o.owner_user_id else None)
        out.append({
            "opportunity_id": o.id,
            "company_name": o.company_name,
            "contact_name": o.contact_name,
            "won_at": o.won_at,
            # deal_value_override is a boolean flag, not an amount.
            "deal_value": float(o.deal_value or 0),
            "brand_sales_org": {"id": bso.id, "name": bso.name} if bso else None,
            "salesperson": {"id": owner.id, "name": owner.full_name} if owner else None,
            "can_provision": prov.can_provision(user, o, db),
        })
    return {"opportunities": out}


def _opportunity_or_404(db: Session, opportunity_id: str) -> Opportunity:
    opp = db.query(Opportunity).filter(Opportunity.id == opportunity_id).first()
    if opp is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return opp


@router.get("/opportunities/{opportunity_id}/provisioning-review")
def provisioning_review(opportunity_id: str,
                        db: Session = Depends(get_db),
                        user: User = Depends(require_sales_member)):
    """§6 — everything the operator confirms before a customer tenant exists."""
    opp = _opportunity_or_404(db, opportunity_id)
    prov.assert_can_provision(user, opp, db)
    return prov.provisioning_review(db, opp)


class ProvisionRequest(BaseModel):
    # Corrections to the CUSTOMER ORGANISATION only. None of these write back to
    # the opportunity, the proposal, or any other sales record (§6).
    org_name: Optional[str] = None
    slug: Optional[str] = None
    industry: Optional[str] = None
    timezone: Optional[str] = None
    org_phone: Optional[str] = None
    org_address: Optional[str] = None
    plan: str = "standard"
    target_launch_date: Optional[datetime] = None
    owner_user_id: Optional[str] = None
    notes: Optional[str] = None
    milestone_keys: Optional[List[str]] = None


@router.post("/opportunities/{opportunity_id}/provision")
def provision(opportunity_id: str,
              req: ProvisionRequest,
              db: Session = Depends(get_db),
              user: User = Depends(require_sales_member)):
    """§7 — create the customer organisation. Idempotent.

    A repeat call returns `created: false` and the ORIGINAL implementation. It
    does not error: the operator who double-clicked wants to know where the
    customer is, not to be told off.
    """
    opp = _opportunity_or_404(db, opportunity_id)
    prov.assert_can_provision(user, opp, db)
    impl, created = prov.provision_customer(
        db, opp, user,
        org_name=req.org_name, slug=req.slug, industry=req.industry,
        timezone=req.timezone, org_phone=req.org_phone, org_address=req.org_address,
        plan=req.plan, target_launch_date=req.target_launch_date,
        owner_user_id=req.owner_user_id, notes=req.notes,
        milestone_keys=req.milestone_keys,
    )
    return {"created": created, "implementation": ops._implementation_row(db, impl)}


@router.get("/staff")
def staff(implementation_id: Optional[str] = None,
          db: Session = Depends(get_db), user: User = Depends(require_god)):
    """Who can be made an implementation owner.

    NOT `/god/users`, which lists god / super / org admins - a list that is
    mostly CUSTOMER administrators and contains almost none of the internal
    people who actually do implementations. Owning an implementation is an
    internal job, so the candidates are internal identities: `organization_id IS
    NULL`, which is this architecture's positive assertion that somebody belongs
    to the control plane and to no tenant.

    The CURRENT owner is always included even if they no longer match, so a
    picker can never silently display "unassigned" for an implementation that
    has an owner.
    """
    rows = (db.query(User)
              .filter(User.organization_id.is_(None), User.is_active.is_(True))
              .order_by(User.full_name).all())
    ids = {u.id for u in rows}

    if implementation_id:
        impl = (db.query(Implementation)
                  .filter(Implementation.id == implementation_id).first())
        if impl is not None and impl.owner_user_id and impl.owner_user_id not in ids:
            cur = db.query(User).filter(User.id == impl.owner_user_id).first()
            if cur is not None:
                rows.append(cur)

    return {"staff": [{"id": u.id, "full_name": u.full_name or u.email,
                       "email": u.email, "role": u.role,
                       "is_active": bool(u.is_active)} for u in rows]}


# ══ implementations ═════════════════════════════════════════════════════════

@router.get("/implementations")
def list_implementations(platform_id: Optional[str] = None,
                         brand_sales_org_id: Optional[str] = None,
                         status: Optional[str] = None,
                         owner_user_id: Optional[str] = None,
                         blocked: Optional[bool] = None,
                         overdue: Optional[bool] = None,
                         live: Optional[bool] = None,
                         limit: int = Query(200, ge=1, le=500),
                         db: Session = Depends(get_db),
                         user: User = Depends(require_god)):
    """§17 — the implementation command centre."""
    return {
        "implementations": ops.implementations(
            db, platform_id=platform_id, brand_sales_org_id=brand_sales_org_id,
            status=status, owner_user_id=owner_user_id, blocked=blocked,
            overdue=overdue, live=live, limit=limit),
        "statuses": [{"key": k, "label": IMPLEMENTATION_STATUS_LABELS[k]}
                     for k in IMPLEMENTATION_STATUSES],
    }


@router.get("/implementations/{implementation_id}")
def implementation_detail(implementation_id: str,
                          db: Session = Depends(get_db),
                          user: User = Depends(require_god)):
    impl = impls.get_or_404(db, implementation_id)
    return {
        "implementation": ops._implementation_row(db, impl),
        "completion": impls.completion(db, impl),
        "milestones": [{"id": m.id, "key": m.key, "label": m.label,
                        "description": m.description, "position": m.position,
                        "is_required": bool(m.is_required), "status": m.status,
                        "notes": m.notes, "completed_at": m.completed_at}
                       for m in impls.milestones(db, impl)],
        "milestone_statuses": list(MILESTONE_STATUSES),
        "handoff": impls.handoff_context(db, impl),
        "launch_warnings": impls.launch_warnings(db, impl),
        "customer_admins": _customer_admins(db, impl.organization_id),
        "timeline": impls.timeline(db, impl),
        "billing": {
            "billing_status": impl.billing_status,
            "implementation_fee": float(impl.implementation_fee) if impl.implementation_fee is not None else None,
            "recurring_amount": float(impl.recurring_amount) if impl.recurring_amount is not None else None,
            "currency": impl.currency,
            "billing_start_date": impl.billing_start_date,
            "trial_start": impl.trial_start, "trial_end": impl.trial_end,
            "billing_notes": impl.billing_notes,
            "external_billing_ref": impl.external_billing_ref,
        },
    }


class OwnerRequest(BaseModel):
    owner_user_id: Optional[str] = None


@router.post("/implementations/{implementation_id}/owner")
def set_owner(implementation_id: str, req: OwnerRequest,
              db: Session = Depends(get_db), user: User = Depends(require_god)):
    impl = impls.get_or_404(db, implementation_id)
    impls.assign_owner(db, impl, user, req.owner_user_id)
    return {"implementation": ops._implementation_row(db, impl)}


class StatusRequest(BaseModel):
    status: str
    blocker_note: Optional[str] = None
    target_launch_date: Optional[datetime] = None
    note: Optional[str] = None


@router.post("/implementations/{implementation_id}/status")
def set_status(implementation_id: str, req: StatusRequest,
               db: Session = Depends(get_db), user: User = Depends(require_god)):
    impl = impls.get_or_404(db, implementation_id)
    impls.assert_can_manage(user, impl, db)
    impls.set_status(db, impl, user, req.status,
                     blocker_note=req.blocker_note,
                     target_launch_date=req.target_launch_date,
                     note=req.note)
    return {"implementation": ops._implementation_row(db, impl)}


class MilestoneRequest(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None


@router.post("/implementations/{implementation_id}/milestones/{key}")
def set_milestone(implementation_id: str, key: str, req: MilestoneRequest,
                  db: Session = Depends(get_db), user: User = Depends(require_god)):
    impl = impls.get_or_404(db, implementation_id)
    impls.assert_can_manage(user, impl, db)
    m = impls.set_milestone(db, impl, user, key, req.status, req.notes)
    return {"milestone": {"key": m.key, "status": m.status, "notes": m.notes,
                          "completed_at": m.completed_at},
            "completion": impls.completion(db, impl)}


class NewMilestoneRequest(BaseModel):
    key: str
    label: str
    description: Optional[str] = None
    is_required: bool = False


@router.post("/implementations/{implementation_id}/milestones")
def add_milestone(implementation_id: str, req: NewMilestoneRequest,
                  db: Session = Depends(get_db), user: User = Depends(require_god)):
    impl = impls.get_or_404(db, implementation_id)
    impls.assert_can_manage(user, impl, db)
    m = impls.add_milestone(db, impl, user, key=req.key, label=req.label,
                            description=req.description, is_required=req.is_required)
    return {"milestone": {"id": m.id, "key": m.key, "label": m.label,
                          "is_required": bool(m.is_required), "status": m.status}}


class LaunchRequest(BaseModel):
    acknowledge_warnings: bool = False
    note: Optional[str] = None


@router.post("/implementations/{implementation_id}/launch")
def launch(implementation_id: str, req: LaunchRequest,
           db: Session = Depends(get_db), user: User = Depends(require_god)):
    """§18 — mark the customer Live. God only, explicit, audited."""
    impl = impls.get_or_404(db, implementation_id)
    if not impls.can_launch(user, impl, db):
        raise HTTPException(status_code=403, detail="Marking a customer Live requires god authority.")
    impls.launch(db, impl, user, acknowledge_warnings=req.acknowledge_warnings, note=req.note)
    return {"implementation": ops._implementation_row(db, impl)}


class BillingRequest(BaseModel):
    """§19 — billing INTENT only. Nothing on this route charges anybody, calls
    Stripe, or activates a subscription."""
    billing_status: Optional[str] = None
    implementation_fee: Optional[float] = None
    recurring_amount: Optional[float] = None
    currency: Optional[str] = None
    billing_start_date: Optional[datetime] = None
    trial_start: Optional[datetime] = None
    trial_end: Optional[datetime] = None
    billing_notes: Optional[str] = None
    external_billing_ref: Optional[str] = None


@router.post("/implementations/{implementation_id}/billing")
def set_billing(implementation_id: str, req: BillingRequest,
                db: Session = Depends(get_db), user: User = Depends(require_god)):
    impl = impls.get_or_404(db, implementation_id)
    before = {"billing_status": impl.billing_status,
              "recurring_amount": float(impl.recurring_amount) if impl.recurring_amount is not None else None,
              "implementation_fee": float(impl.implementation_fee) if impl.implementation_fee is not None else None,
              "billing_start_date": impl.billing_start_date}
    data = req.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(impl, k, v)
    impl.last_activity_at = datetime.utcnow()
    log_action(db, impl.organization_id, user.id,
               action="billing_configuration_changed",
               target_type="implementation", target_id=impl.id,
               platform_id=impl.platform_id,
               brand_sales_org_id=impl.brand_sales_org_id,
               before=before, after=data,
               note="Billing intent recorded. No charge was made.",
               commit=False)
    db.commit()
    db.refresh(impl)
    return {"billing": data, "implementation": ops._implementation_row(db, impl)}


# ══ customer admin ══════════════════════════════════════════════════════════

def _customer_admins(db: Session, org_id: str) -> List[Dict[str, Any]]:
    users = (db.query(User).filter(User.organization_id == org_id)
               .order_by(User.created_at).all())
    out = []
    for u in users:
        a = activation.latest_for_user(db, u.id)
        out.append({
            "user_id": u.id, "full_name": u.full_name, "email": u.email,
            "role": u.role, "is_active": bool(u.is_active),
            "last_login_at": u.last_login_at,
            "invite": ({"id": a.id, "status": a.status, "expires_at": a.expires_at,
                        "send_count": a.send_count, "accepted_at": a.accepted_at,
                        "is_usable": a.is_usable()} if a else None),
        })
    return out


class CreateAdminRequest(BaseModel):
    full_name: str
    email: str
    role: str = "org_admin"
    ttl_hours: int = Field(default=activation.DEFAULT_TTL_HOURS, ge=1, le=720)
    base_url: Optional[str] = None


@router.post("/implementations/{implementation_id}/customer-admin")
def create_customer_admin(implementation_id: str, req: CreateAdminRequest,
                          db: Session = Depends(get_db), user: User = Depends(require_god)):
    """§9 — create the customer's first administrator.

    THE RESPONSE CONTAINS THE ACTIVATION LINK EXACTLY ONCE. It is not stored,
    not audited and not recoverable; a lost link is replaced with `resend`,
    which revokes the old one. No password is created, returned or emailed.
    """
    impl = impls.get_or_404(db, implementation_id)
    org = db.query(Organization).filter(Organization.id == impl.organization_id).first()
    if org is None:
        raise HTTPException(status_code=404, detail="Customer organisation not found.")
    u, act, raw = activation.create_customer_admin(
        db, org, user, full_name=req.full_name, email=req.email,
        role=req.role, implementation=impl, ttl_hours=req.ttl_hours)
    return {
        "user": {"id": u.id, "full_name": u.full_name, "email": u.email, "role": u.role},
        "activation": {"id": act.id, "expires_at": act.expires_at,
                       "prefix": act.token_prefix},
        "activation_url": activation.activation_url(req.base_url, raw),
        "warning": "This link is shown once and is not recoverable. No password was created.",
    }


class AddExistingUserRequest(BaseModel):
    user_id: str
    role: Optional[str] = None


@router.post("/implementations/{implementation_id}/customer-user")
def add_existing_customer_user(implementation_id: str, req: AddExistingUserRequest,
                               db: Session = Depends(get_db), user: User = Depends(require_god)):
    """§9(B) — add an EXISTING identity to this tenant, named by id.

    Never by email match. Matching on an address is the tenancy inference §1
    forbids, and it is how a salesperson whose address shares a domain with the
    customer ends up inside the customer's data.
    """
    impl = impls.get_or_404(db, implementation_id)
    org = db.query(Organization).filter(Organization.id == impl.organization_id).first()
    if org is None:
        raise HTTPException(status_code=404, detail="Customer organisation not found.")
    u = activation.add_existing_user(db, org, user, user_id=req.user_id, role=req.role)
    return {"user": {"id": u.id, "full_name": u.full_name, "email": u.email,
                     "role": u.role, "organization_id": u.organization_id}}


class ResendRequest(BaseModel):
    ttl_hours: int = Field(default=activation.DEFAULT_TTL_HOURS, ge=1, le=720)
    base_url: Optional[str] = None


@router.post("/activations/{activation_id}/resend")
def resend_invite(activation_id: str, req: ResendRequest,
                  db: Session = Depends(get_db), user: User = Depends(require_god)):
    act = db.query(CustomerActivation).filter(CustomerActivation.id == activation_id).first()
    if act is None:
        raise HTTPException(status_code=404, detail="Invitation not found.")
    fresh, raw = activation.resend(db, act, user, ttl_hours=req.ttl_hours)
    return {"activation": {"id": fresh.id, "expires_at": fresh.expires_at,
                           "send_count": fresh.send_count, "prefix": fresh.token_prefix},
            "activation_url": activation.activation_url(req.base_url, raw),
            "warning": "The previous link is now revoked. This one is shown once."}


@router.post("/activations/{activation_id}/revoke")
def revoke_invite(activation_id: str,
                  db: Session = Depends(get_db), user: User = Depends(require_god)):
    act = db.query(CustomerActivation).filter(CustomerActivation.id == activation_id).first()
    if act is None:
        raise HTTPException(status_code=404, detail="Invitation not found.")
    act = activation.revoke(db, act, user)
    return {"activation": {"id": act.id, "status": act.status, "revoked_at": act.revoked_at}}


# ══ sales team access (brand-sales login activation) ════════════════════════
#
# These routes use `require_sales_member` at the door and then
# `staff_access.assert_can_manage_sales_access` on the record, so god can act on any
# brand and a sales MANAGER can act only on their own. A rep reaching them gets
# 403 from the record check, not from the door - the door cannot know which
# brand is being asked about until the record is resolved.


def _sales_team_rows(db: Session, brand_sales_org_id: str):
    """Everyone with a brand-sales membership here, with their real access state."""
    mem = (db.query(Membership)
             .filter(Membership.scope_type == SCOPE_BRAND_SALES_ORG,
                     Membership.scope_id == brand_sales_org_id,
                     Membership.role.in_(BRAND_SALES_ROLES))
             .order_by(Membership.role, Membership.created_at).all())
    # One lookup for every reporting manager named on the page, rather than a
    # query per row. A ten-person team was firing ten extra selects to render a
    # column that resolves from a handful of ids.
    mgr_ids = {m.reports_to_user_id for m in mem if m.reports_to_user_id}
    mgr_names = {}
    if mgr_ids:
        for mu in db.query(User).filter(User.id.in_(list(mgr_ids))).all():
            mgr_names[mu.id] = mu.full_name

    out = []
    for m in mem:
        u = db.query(User).filter(User.id == m.user_id).first()
        if u is None:
            continue
        out.append({
            "user_id": u.id,
            "full_name": u.full_name,
            "email": u.email,
            "user_is_active": bool(u.is_active),
            # Surfaced because a non-NULL value here would mean a brand-sales
            # identity had been placed inside a customer tenant, which is a
            # thing the operator needs to SEE rather than have hidden.
            "organization_id": u.organization_id,
            "membership_id": m.id,
            "role": m.role,
            "membership_is_active": bool(m.is_active),
            "reports_to_user_id": m.reports_to_user_id,
            "reports_to_name": mgr_names.get(m.reports_to_user_id),
            "last_login_at": u.last_login_at,
            # Whether this person may be NAMED as somebody's reporting manager.
            # Computed here rather than inferred in the browser from the role
            # string, so the dropdown and `assert_manager_ok` can never disagree
            # about who is eligible - and so no screen has to interpret a role
            # for itself, which is a habit that turns into a permission decision
            # sooner or later.
            "can_be_reporting_manager": (m.role == ROLE_SALES_MANAGER
                                         and bool(m.is_active) and bool(u.is_active)),
            "access": staff_access.access_state(db, u),
        })
    return out


@router.get("/brands/{brand_sales_org_id}/sales-team")
def sales_team(brand_sales_org_id: str,
               db: Session = Depends(get_db),
               user: User = Depends(require_sales_member)):
    """Who sells this brand, and whether each of them can actually log in."""
    bso = db.query(BrandSalesOrg).filter(BrandSalesOrg.id == brand_sales_org_id).first()
    if bso is None:
        raise HTTPException(status_code=404, detail="Brand sales organisation not found.")
    staff_access.assert_can_manage_sales_access(user, bso.id, db)
    return {"brand_sales_org": {"id": bso.id, "name": bso.name, "slug": bso.slug},
            "team": _sales_team_rows(db, bso.id)}


# ── adding and managing the people who sell a brand ─────────────────────────
#
# GOD ONLY, DELIBERATELY. Reading the team is available to that brand's sales
# manager (`sales_team` above uses `assert_can_manage_sales_access`), but every
# WRITE below requires `require_god`. Handing a manager the ability to mint
# identities is a bigger grant than "runs a team", and it waits for the
# `manage_sales_users` permission the framework does not have yet. Until then a
# manager gets a 403 here, from the dependency, not from a hidden button.


@router.get("/brands/{brand_sales_org_id}/identity-lookup")
def identity_lookup(brand_sales_org_id: str,
                    email: str = Query(...),
                    db: Session = Depends(get_db),
                    god: User = Depends(require_god)):
    """Does this email already belong to somebody? Asked BEFORE anything is created.

    This is the whole duplicate-prevention story: the operator types an address,
    sees the existing human if there is one - including every membership they
    already hold, in other brands and in customer organisations - and then
    decides. Nothing is written by this route.
    """
    bso = db.query(BrandSalesOrg).filter(BrandSalesOrg.id == brand_sales_org_id).first()
    if bso is None:
        raise HTTPException(status_code=404, detail="Brand sales organisation not found.")

    email = sales_staff.assert_email(email)
    existing = sales_staff.find_identity(db, email)
    if existing is None:
        return {"exists": False, "email": email}

    summary = sales_staff.identity_summary(db, existing)
    here = sales_staff.get_membership(db, existing.id, bso.id)
    summary["already_in_this_brand"] = here is not None
    summary["membership_here"] = ({"id": here.id, "role": here.role,
                                   "is_active": bool(here.is_active)}
                                  if here else None)
    return summary


class AddSalesUserRequest(BaseModel):
    email: str
    role: str
    full_name: Optional[str] = None       # required only when creating an identity
    reports_to_user_id: Optional[str] = None
    send_setup_link: bool = True
    base_url: Optional[str] = None


@router.post("/brands/{brand_sales_org_id}/sales-team")
def add_sales_user(brand_sales_org_id: str, req: AddSalesUserRequest,
                   db: Session = Depends(get_db),
                   god: User = Depends(require_god)):
    """Add somebody to this brand's sales team, creating them only if needed.

    ONE HUMAN, ONE IDENTITY. The email is normalised and looked up first. An
    existing person is REUSED and their other memberships are left exactly as
    they are; only if nobody holds that address is a users row created, and then
    with organization_id NULL and a password nobody can know.

    The response carries the one-time link ONCE and never a password, because no
    code path in `sales_staff` ever holds one.
    """
    bso = db.query(BrandSalesOrg).filter(BrandSalesOrg.id == brand_sales_org_id).first()
    if bso is None:
        raise HTTPException(status_code=404, detail="Brand sales organisation not found.")

    email = sales_staff.assert_email(req.email)
    role = sales_staff.assert_role(req.role)

    user = sales_staff.find_identity(db, email)
    created_identity = False
    if user is None:
        user = sales_staff.create_identity(db, email, req.full_name or "", god)
        created_identity = True
    elif not user.is_active:
        raise HTTPException(
            status_code=409,
            detail="That user is deactivated. Reactivate the person before giving "
                   "them a sales seat.")

    membership, created_membership = sales_staff.grant_membership(
        db, user, bso, role, god, reports_to_user_id=req.reports_to_user_id)

    setup_url = None
    activation = None
    if req.send_setup_link:
        row, raw = staff_access.issue(
            db, user, god, brand_sales_org_id=bso.id,
            purpose=PURPOSE_SETUP if created_identity else "reset")
        setup_url = staff_access.activation_url(req.base_url, raw)
        activation = {"id": row.id, "purpose": row.purpose,
                      "expires_at": row.expires_at, "prefix": row.token_prefix}

    db.commit()
    db.refresh(user)
    db.refresh(membership)

    return {
        "user": {"id": user.id, "full_name": user.full_name, "email": user.email,
                 "organization_id": user.organization_id,
                 "created": created_identity},
        "membership": {"id": membership.id, "role": membership.role,
                       "is_active": bool(membership.is_active),
                       "reports_to_user_id": membership.reports_to_user_id,
                       "created": created_membership},
        "activation": activation,
        "setup_url": setup_url,
        "warning": ("The link is shown once and is not recoverable. No password was "
                    "created, changed or returned."),
        "team": _sales_team_rows(db, bso.id),
    }


class MembershipPatch(BaseModel):
    role: Optional[str] = None
    is_active: Optional[bool] = None
    # Explicit tri-state: absent means "leave it", null means "clear it".
    reports_to_user_id: Optional[str] = Field(default=None)
    set_reports_to: bool = False


@router.patch("/sales-memberships/{membership_id}")
def patch_sales_membership(membership_id: str, req: MembershipPatch,
                           db: Session = Depends(get_db),
                           god: User = Depends(require_god)):
    """Change a seat: role, active state, reporting line. Never deletes it.

    Deactivating is not deleting, on purpose - the row stays so owned
    opportunities, booked meetings and every audit entry naming this person
    survive untouched, and their memberships elsewhere are never read.
    """
    m = db.query(Membership).filter(Membership.id == membership_id).first()
    if m is None or m.scope_type != SCOPE_BRAND_SALES_ORG:
        raise HTTPException(status_code=404, detail="Membership not found.")

    if req.role is not None:
        sales_staff.change_role(db, m, req.role, god)
    if req.set_reports_to:
        sales_staff.set_reporting_manager(db, m, req.reports_to_user_id, god)
    if req.is_active is not None:
        sales_staff.set_active(db, m, req.is_active, god)

    db.commit()
    db.refresh(m)
    return {"membership": {"id": m.id, "user_id": m.user_id, "role": m.role,
                           "is_active": bool(m.is_active),
                           "reports_to_user_id": m.reports_to_user_id},
            "team": _sales_team_rows(db, m.scope_id)}


class SetupLinkRequest(BaseModel):
    brand_sales_org_id: str
    purpose: str = PURPOSE_SETUP          # "setup" | "reset"
    ttl_hours: int = Field(default=staff_access.DEFAULT_TTL_HOURS, ge=1, le=720)
    base_url: Optional[str] = None


@router.post("/sales-users/{user_id}/setup-link")
def sales_setup_link(user_id: str, req: SetupLinkRequest,
                     db: Session = Depends(get_db),
                     user: User = Depends(require_sales_member)):
    """Generate a one-time access link for an EXISTING brand-sales user.

    THE RESPONSE CONTAINS THE LINK EXACTLY ONCE. It is not stored, not audited
    and not recoverable; a lost link is replaced by generating another, which
    revokes this one. No password is created, changed, returned or emailed.

    This route creates nothing but the link. It does not create users, it does
    not create or modify memberships, and it cannot set `organization_id` - the
    activation table has no such column.
    """
    bso = (db.query(BrandSalesOrg)
             .filter(BrandSalesOrg.id == req.brand_sales_org_id).first())
    if bso is None:
        raise HTTPException(status_code=404, detail="Brand sales organisation not found.")
    staff_access.assert_can_manage_sales_access(user, bso.id, db)

    target = db.query(User).filter(User.id == user_id).first()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found.")

    # The person must already hold a membership in THIS brand. That is what
    # stops this route being a back door for handing a login to somebody who
    # does not sell here - it grants no access of its own, so it must only ever
    # unlock access that already exists.
    holds = (db.query(Membership)
               .filter(Membership.user_id == target.id,
                       Membership.scope_type == SCOPE_BRAND_SALES_ORG,
                       Membership.scope_id == bso.id,
                       Membership.role.in_(BRAND_SALES_ROLES),
                       Membership.is_active.is_(True))
               .first())
    if holds is None:
        raise HTTPException(
            status_code=409,
            detail="That user has no active sales membership in this brand. "
                   "An access link unlocks existing access; it does not grant it.")

    row, raw = staff_access.issue(db, target, user,
                           brand_sales_org_id=bso.id,
                           purpose=req.purpose, ttl_hours=req.ttl_hours)
    return {
        "user": {"id": target.id, "full_name": target.full_name,
                 "email": target.email, "role": holds.role},
        "activation": {"id": row.id, "purpose": row.purpose,
                       "expires_at": row.expires_at, "prefix": row.token_prefix,
                       "send_count": row.send_count},
        "setup_url": staff_access.activation_url(req.base_url, raw),
        "warning": "Shown once and not recoverable. No password was created or changed.",
    }


@router.post("/staff-activations/{activation_id}/revoke")
def revoke_sales_link(activation_id: str,
                      db: Session = Depends(get_db),
                      user: User = Depends(require_sales_member)):
    row = (db.query(StaffActivation)
             .filter(StaffActivation.id == activation_id).first())
    if row is None:
        raise HTTPException(status_code=404, detail="Link not found.")
    staff_access.assert_can_manage_sales_access(user, row.brand_sales_org_id, db)
    row = staff_access.revoke(db, row, user)
    return {"activation": {"id": row.id, "status": row.status,
                           "revoked_at": row.revoked_at}}


# ══ customer organisations ══════════════════════════════════════════════════

@router.get("/customer-organizations")
def customer_organizations(platform_id: Optional[str] = None,
                           limit: int = Query(300, ge=1, le=1000),
                           db: Session = Depends(get_db),
                           user: User = Depends(require_god)):
    """§20 — every customer tenant across every platform."""
    return {"organizations": ops.customer_organizations(db, platform_id=platform_id, limit=limit)}


# ══ audit ═══════════════════════════════════════════════════════════════════

# THE AUDIT FEED IS AN ALLOWLIST, ORGANISED BY CATEGORY.
#
# It stays an allowlist on purpose. The audit table holds every logged action in
# the system, including ordinary tenant activity, and a control-plane view that
# showed all of it would be a firehose nobody reads - which is the same as no
# audit at all. The fix for "my action is invisible" is to add that action to
# the right category here, deliberately, not to remove the filter.
#
# This list was stale: `data_cleanup.*` and `platform_owner.*` rows were being
# written and committed but never appeared, so a production cleanup and a
# platform-owner neutralisation both left records that the audit screen claimed
# did not exist. Worse than a missing feature - a false negative in the one
# place you look to find out what happened.
#
# Both the old and new names for the context actions are listed. Rows already in
# production carry `platform_owner.enter_customer`; new ones carry
# `platform_owner.context_entered`. Dropping the old spelling would hide history
# that already exists.
AUDIT_CATEGORIES = {
    "provisioning": (
        "customer_provisioned", "customer.created", "customer.activated",
        "customer.deactivated", "customer.location_created",
        "customer.location_updated", "customer.features_set",
    ),
    "staffing": (
        "customer_admin_created", "customer_admin_invited",
        "customer_admin_invite_revoked", "customer_admin_activated",
        "customer_user_added", "customer.user_added", "customer.user_updated",
        "customer.user_locations_set",
    ),
    "implementation": (
        "implementation_owner_assigned", "implementation_status_changed",
        "implementation_ready_for_launch", "implementation_milestone_changed",
        "implementation_milestone_added", "customer_marked_live",
        "billing_configuration_changed",
    ),
    "platform_owner": (
        "platform_owner.context_entered", "platform_owner.context_exited",
        "platform_owner.enter_customer", "platform_owner.exit_customer",
        "platform_owner.neutralized",
    ),
    "data_lifecycle": (
        "data_cleanup.previewed", "data_cleanup.executed",
        "data_cleanup.failed", "data_cleanup.rolled_back",
    ),
}

# Flat tuple for the query filter; the category is attached per row on the way
# out so the UI can group without a second source of truth.
CONTROL_PLANE_ACTIONS = tuple(
    a for actions in AUDIT_CATEGORIES.values() for a in actions)

_ACTION_CATEGORY = {
    a: cat for cat, actions in AUDIT_CATEGORIES.items() for a in actions}


@router.get("/audit")
def control_plane_audit(action: Optional[str] = None,
                        category: Optional[str] = None,
                        organization_id: Optional[str] = None,
                        platform_id: Optional[str] = None,
                        brand_sales_org_id: Optional[str] = None,
                        limit: int = Query(200, ge=1, le=1000),
                        db: Session = Depends(get_db),
                        user: User = Depends(require_god)):
    """§23 — the control-plane audit trail, readable by the owner.

    Reads the ONE audit table. Checkpoint 6 added columns to it rather than
    building a second engine, so an implementation's timeline and this view are
    the same rows seen through different filters and cannot disagree.
    """
    q = db.query(AuditLogEntry)
    if action:
        # Still allowlisted. Asking for a specific action by name must not be a
        # way around the filter and into ordinary tenant activity.
        if action not in _ACTION_CATEGORY:
            raise HTTPException(
                status_code=400,
                detail="'%s' is not a control-plane action. Valid actions: %s"
                       % (action, ", ".join(sorted(_ACTION_CATEGORY))))
        q = q.filter(AuditLogEntry.action == action)
    elif category:
        if category not in AUDIT_CATEGORIES:
            raise HTTPException(
                status_code=400,
                detail="Unknown category '%s'. Valid: %s"
                       % (category, ", ".join(sorted(AUDIT_CATEGORIES))))
        q = q.filter(AuditLogEntry.action.in_(AUDIT_CATEGORIES[category]))
    else:
        q = q.filter(AuditLogEntry.action.in_(CONTROL_PLANE_ACTIONS))
    if organization_id:
        q = q.filter(AuditLogEntry.organization_id == organization_id)
    if platform_id:
        q = q.filter(AuditLogEntry.platform_id == platform_id)
    if brand_sales_org_id:
        q = q.filter(AuditLogEntry.brand_sales_org_id == brand_sales_org_id)
    rows = q.order_by(AuditLogEntry.created_at.desc()).limit(limit).all()

    actors = {}
    out = []
    for r in rows:
        if r.actor_user_id not in actors:
            a = db.query(User).filter(User.id == r.actor_user_id).first()
            actors[r.actor_user_id] = a.full_name if a else None
        out.append({
            "id": r.id, "action": r.action,
            "category": _ACTION_CATEGORY.get(r.action, "other"),
            "at": r.created_at,
            "actor": actors[r.actor_user_id], "actor_user_id": r.actor_user_id,
            "target_type": r.target_type, "target_id": r.target_id,
            "organization_id": r.organization_id, "platform_id": r.platform_id,
            "brand_sales_org_id": r.brand_sales_org_id,
            "before": r.before_state, "after": r.after_state,
            "details": r.details, "note": r.note,
        })
    return {
        "entries": out,
        "actions": list(CONTROL_PLANE_ACTIONS),
        "categories": {k: list(v) for k, v in AUDIT_CATEGORIES.items()},
    }
