"""CUSTOMERS — create, configure, staff and activate, all over HTTP.

This is the answer to "provision a customer without SQL, a Render shell, a seed
script or a developer". Every step below is a call an owner makes from a
browser, and the last one is the only thing that flips a customer live.

Guarded by `require_god` throughout, on purpose. The mission says a Brand Owner
gets the MINIMUM customer-management capability that fits the existing
permission architecture, and that architecture has no brand-owner role yet -
`BRAND_SALES_ROLES` is exactly ("sales_manager", "sales_rep"), and neither of
those is an operator. Inventing a role here to hold this authority would be
building the Brand Owner command centre the mission explicitly deferred. So the
scoping hook is present and unused: `_scope_platforms` already narrows what a
non-owner would see, and the day a brand_owner role exists it is the only line
that changes.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.models.models import Organization, Platform, User
from app.models.location_models import Location
from app.routers.audit_log_router import log_action
from app.services import customer_provisioning as cp
from app.services import customer_readiness as cr
from app.services import entitlements
from app.services import platform_owner as po
from app.services import staff_activation as _activation
from app.models.staff_models import PURPOSE_SETUP as _PURPOSE_SETUP

router = APIRouter(prefix="/god/customers", tags=["customers"])


def _load(db: Session, org_id: str) -> Organization:
    if po.is_platform_pseudo_org(org_id):
        raise HTTPException(status_code=404, detail="Customer not found")
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Customer not found")
    return org


class BriefPrefetch:
    """Platform, user counts and location counts for a whole customer list.

    `_brief` cost three queries per organisation, one of which loaded every user
    row just to count four things about them. On a list of every customer that
    is the entire users table, once per customer.
    """

    __slots__ = ("platforms", "counts", "locations")

    def __init__(self, db: Session, orgs):
        self.platforms, self.counts, self.locations = {}, {}, {}
        ids = sorted({o.id for o in orgs})
        if not ids:
            return
        plat_ids = sorted({o.platform_id for o in orgs if o.platform_id})
        if plat_ids:
            self.platforms = {p.id: p for p in
                              db.query(Platform).filter(Platform.id.in_(plat_ids)).all()}
        # One pass over the users of every listed org. The per-org helper derives
        # its four numbers in Python from the same rows, so this derives them the
        # same way rather than re-expressing them as SQL that could drift.
        for u in db.query(User).filter(User.organization_id.in_(ids)).all():
            b = self.counts.setdefault(str(u.organization_id),
                                       {"total": 0, "active": 0, "pending": 0, "admins": 0})
            b["total"] += 1
            if u.is_active:
                b["active"] += 1
                if u.last_login_at is None:
                    b["pending"] += 1
                if u.role == "org_admin":
                    b["admins"] += 1
        for org_id, n in (db.query(Location.organization_id, func.count(Location.id))
                          .filter(Location.organization_id.in_(ids))
                          .group_by(Location.organization_id).all()):
            self.locations[str(org_id)] = int(n)

    def user_counts(self, org_id):
        return self.counts.get(str(org_id),
                               {"total": 0, "active": 0, "pending": 0, "admins": 0})


def _brief(db: Session, org: Organization,
           pre: Optional["BriefPrefetch"] = None) -> Dict[str, Any]:
    if pre is not None:
        platform = pre.platforms.get(org.platform_id) if org.platform_id else None
        counts = pre.user_counts(org.id)
        location_count = pre.locations.get(str(org.id), 0)
    else:
        platform = (db.query(Platform).filter(Platform.id == org.platform_id).first()
                    if org.platform_id else None)
        counts = cr.customer_user_counts(db, org.id)
        location_count = db.query(Location).filter(
            Location.organization_id == org.id).count()
    return {
        "id": org.id, "name": org.name, "slug": org.slug,
        "industry": org.industry, "plan": org.plan,
        "is_active": bool(org.is_active),
        "platform_id": org.platform_id,
        "brand": None if platform is None else platform.name,
        "user_count": counts["total"], "active_users": counts["active"],
        "location_count": location_count,
        "created_at": org.created_at.isoformat() if org.created_at else None,
    }


# ── list ────────────────────────────────────────────────────────────────────

@router.get("")
def list_customers(platform_id: Optional[str] = Query(None),
                   include_inactive: bool = Query(True),
                   db: Session = Depends(get_db), user: User = Depends(require_god)):
    q = po.exclude_platform_org(db.query(Organization))
    if platform_id:
        q = q.filter(Organization.platform_id == platform_id)
    if not include_inactive:
        q = q.filter(Organization.is_active == True)  # noqa: E712
    orgs = q.order_by(Organization.name.asc()).all()
    pre = BriefPrefetch(db, orgs)
    return {"customers": [_brief(db, o, pre) for o in orgs], "total": len(orgs)}


# ── STEP 1: create ──────────────────────────────────────────────────────────

class LocationIn(BaseModel):
    name: str
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    country: Optional[str] = "US"
    phone: Optional[str] = None
    email: Optional[str] = None
    timezone: Optional[str] = None
    operating_hours: Optional[Any] = None
    notes: Optional[str] = None


class CustomerCreate(BaseModel):
    name: str
    platform_id: str
    slug: Optional[str] = None
    legal_name: Optional[str] = None
    industry: str = "funeral"
    plan: str = "trial"
    timezone: str = "America/Chicago"
    phone: Optional[str] = None
    address: Optional[str] = None
    primary_location: Optional[LocationIn] = None


@router.post("", status_code=201)
def create_customer(req: CustomerCreate, db: Session = Depends(get_db),
                    user: User = Depends(require_god)):
    org, loc = cp.create_customer(
        db, user, name=req.name, platform_id=req.platform_id, slug=req.slug,
        industry=req.industry, plan=req.plan, timezone=req.timezone,
        legal_name=req.legal_name, phone=req.phone, address=req.address,
        primary_location=(req.primary_location.model_dump()
                          if req.primary_location else None),
    )
    db.commit()
    db.refresh(org)
    return {"customer": _brief(db, org),
            "primary_location": None if loc is None else cp.location_row(db, loc)}


@router.get("/{org_id}")
def customer_detail(org_id: str, db: Session = Depends(get_db),
                    user: User = Depends(require_god)):
    org = _load(db, org_id)
    return {
        "customer": _brief(db, org),
        "locations": cp.list_locations(db, org.id),
        "users": cp.customer_users(db, org.id),
        "features": entitlements.feature_report(org),
        "readiness": cr.readiness(db, org),
    }


# ── STEP 2: locations ───────────────────────────────────────────────────────

@router.get("/{org_id}/locations")
def get_locations(org_id: str, db: Session = Depends(get_db),
                  user: User = Depends(require_god)):
    _load(db, org_id)
    return {"locations": cp.list_locations(db, org_id)}


@router.post("/{org_id}/locations", status_code=201)
def add_location(org_id: str, req: LocationIn, db: Session = Depends(get_db),
                 user: User = Depends(require_god)):
    org = _load(db, org_id)
    loc = cp.create_location(db, org, user, **req.model_dump())
    db.commit()
    return cp.location_row(db, loc)


class LocationPatch(BaseModel):
    name: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    timezone: Optional[str] = None
    operating_hours: Optional[Any] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None
    is_primary: Optional[bool] = None


@router.patch("/{org_id}/locations/{location_id}")
def update_location(org_id: str, location_id: str, req: LocationPatch,
                    db: Session = Depends(get_db), user: User = Depends(require_god)):
    import json as _json
    org = _load(db, org_id)
    loc = (db.query(Location)
           .filter(Location.id == location_id,
                   Location.organization_id == org.id).first())
    if not loc:
        raise HTTPException(status_code=404, detail="Location not found")

    data = {k: v for k, v in req.model_dump().items() if v is not None}
    make_primary = data.pop("is_primary", None)
    if "operating_hours" in data and isinstance(data["operating_hours"], (dict, list)):
        data["operating_hours"] = _json.dumps(data["operating_hours"])
    before = {k: getattr(loc, k, None) for k in data}
    for k, v in data.items():
        setattr(loc, k, v)
    if make_primary:
        cp.set_primary_location(db, org, loc, user, commit=False)
    log_action(db, org.id, user.id, action="customer.location_updated",
               target_type="location", target_id=loc.id,
               platform_id=org.platform_id, before=before, after=data, commit=False)
    db.commit()
    return cp.location_row(db, loc)


# ── STEP 3/4: people ────────────────────────────────────────────────────────

@router.get("/{org_id}/identity-lookup")
def identity_lookup(org_id: str, email: str = Query(...),
                    db: Session = Depends(get_db), user: User = Depends(require_god)):
    """Email first. Nothing is created by asking."""
    _load(db, org_id)
    return cp.lookup_identity(db, email, org_id)


class CustomerUserIn(BaseModel):
    email: EmailStr
    full_name: Optional[str] = None
    role: str = "advisor"
    location_ids: List[str] = []
    base_url: Optional[str] = None


@router.post("/{org_id}/users", status_code=201)
def add_user(org_id: str, req: CustomerUserIn, db: Session = Depends(get_db),
             user: User = Depends(require_god)):
    """Add or reuse a person, then hand over access by a one-time link.

    No password is generated, returned or logged. The link is the only thing
    that grants access and it is shown exactly once.
    """
    org = _load(db, org_id)
    target, created = cp.add_customer_user(
        db, org, user, email=str(req.email), full_name=req.full_name or "",
        role=req.role, location_ids=req.location_ids)

    setup_url = None
    if created or target.must_change_password:
        _row, raw = _activation.issue(db, target, user, purpose=_PURPOSE_SETUP)
        setup_url = _activation.activation_url(req.base_url, raw)

    db.commit()
    return {
        "user": {"id": target.id, "email": target.email,
                 "full_name": target.full_name, "role": target.role},
        "created_identity": created,
        "setup_url": setup_url,
    }


class AssignLocationsIn(BaseModel):
    location_ids: List[str]


@router.put("/{org_id}/users/{user_id}/locations")
def set_user_locations(org_id: str, user_id: str, req: AssignLocationsIn,
                       db: Session = Depends(get_db), user: User = Depends(require_god)):
    org = _load(db, org_id)
    target = (db.query(User)
              .filter(User.id == user_id, User.organization_id == org.id).first())
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    assigned = cp.assign_locations(db, org, target, user, req.location_ids, commit=False)
    log_action(db, org.id, user.id, action="customer.user_locations_set",
               target_type="user", target_id=target.id, platform_id=org.platform_id,
               after={"location_ids": assigned}, commit=False)
    db.commit()
    return {"user_id": target.id, "location_ids": assigned}


# ── STEP 5: features ────────────────────────────────────────────────────────

@router.get("/{org_id}/features")
def get_features(org_id: str, db: Session = Depends(get_db),
                 user: User = Depends(require_god)):
    return entitlements.feature_report(_load(db, org_id))


class FeaturesIn(BaseModel):
    # None restores the legacy "everything" mode; [] means nothing enabled.
    enabled: Optional[List[str]] = None


@router.put("/{org_id}/features")
def put_features(org_id: str, req: FeaturesIn, db: Session = Depends(get_db),
                 user: User = Depends(require_god)):
    org = _load(db, org_id)
    entitlements.set_features(db, org, user, req.enabled)
    db.commit()
    db.refresh(org)
    return entitlements.feature_report(org)


# ── STEP 10: review, then activate ──────────────────────────────────────────

@router.get("/{org_id}/readiness")
def get_readiness(org_id: str, db: Session = Depends(get_db),
                  user: User = Depends(require_god)):
    return cr.readiness(db, _load(db, org_id))


class ActivateIn(BaseModel):
    acknowledge_warnings: bool = False


@router.post("/{org_id}/activate")
def activate(org_id: str, req: ActivateIn, db: Session = Depends(get_db),
             user: User = Depends(require_god)):
    """Flip the customer live. Blockers refuse; warnings must be acknowledged."""
    org = _load(db, org_id)
    state = cr.readiness(db, org)
    if state["blockers"]:
        raise HTTPException(
            status_code=409,
            detail="Cannot activate: %s" % " ".join(state["blockers"]))
    if state["warnings"] and not req.acknowledge_warnings:
        raise HTTPException(
            status_code=409,
            detail="This customer can be activated but has open warnings: %s "
                   "Re-send with acknowledge_warnings=true to proceed."
                   % " ".join(state["warnings"]))

    was = bool(org.is_active)
    org.is_active = True
    log_action(db, org.id, user.id, action="customer.activated",
               target_type="organization", target_id=org.id,
               platform_id=org.platform_id,
               before={"is_active": was}, after={"is_active": True},
               details={"acknowledged_warnings": state["warnings"]
                        if req.acknowledge_warnings else []},
               commit=False)
    db.commit()
    return {"activated": True, "customer": _brief(db, org), "readiness": state}


# ── test-data cleanup: preview, then a typed confirmation ───────────────────

class CleanupRequest(BaseModel):
    rules: List[str] = []
    org_ids: Optional[List[str]] = None
    import_batches: Optional[List[str]] = None
    # Per-record deletion manifest. Off by default because it is a row per
    # candidate and most previews just want the counts.
    include_manifest: bool = False


class CleanupExecute(CleanupRequest):
    confirmation: str
    # The plan this confirmation was typed against. Optional for callers that
    # preview and execute in one breath, but passing it is what makes the
    # candidate set verifiable: if the world moved in between, execute refuses
    # rather than deleting a set nobody reviewed.
    execution_id: Optional[str] = None


@router.post("/cleanup/preview")
def cleanup_preview(req: CleanupRequest, db: Session = Depends(get_db),
                    user: User = Depends(require_god)):
    """What WOULD be removed. Deletes nothing.

    POST rather than GET because the rule set is structured, not because it
    changes anything — it does not.
    """
    from app.services import data_cleanup
    plan = data_cleanup.preview(db, rules=req.rules, org_ids=req.org_ids,
                                import_batches=req.import_batches,
                                include_manifest=True)

    # The plan is persisted HERE, on preview, so the manifest exists on disk
    # before anybody confirms anything. The first production cleanup's manifest
    # lived only in a browser tab and was lost when the tab reloaded; the
    # deletion had already happened and the record of what went with it had not.
    row = data_cleanup.record_plan(db, user, rules=req.rules, org_ids=req.org_ids,
                                   import_batches=req.import_batches, plan=plan)

    log_action(
        db, None, user.id,
        action="data_cleanup.previewed", target_type="cleanup", target_id=row.id,
        details={"execution_id": row.id, "rules": req.rules,
                 "total_records": plan["total_records"],
                 "lead_count": len(plan["_lead_ids"])},
        note="Preview only. Nothing was deleted.",
    )

    lead_ids = plan.pop("_lead_ids")
    plan["execution_id"] = row.id
    plan["target_lead_ids"] = lead_ids
    if not req.include_manifest:
        plan["manifest"] = None
    return plan


@router.get("/cleanup/rules")
def cleanup_rules(user: User = Depends(require_god)):
    from app.services import data_cleanup
    return {"rules": [{"key": k, "description": v}
                      for k, v in sorted(data_cleanup.RULES.items())],
            "protected": data_cleanup.PROTECTED}


@router.post("/cleanup/execute")
def cleanup_execute(req: CleanupExecute, db: Session = Depends(get_db),
                    user: User = Depends(require_god)):
    """Delete exactly what the preview showed. Requires the typed phrase."""
    from app.services import data_cleanup
    return data_cleanup.execute(db, user, rules=req.rules, org_ids=req.org_ids,
                                import_batches=req.import_batches,
                                confirmation=req.confirmation,
                                execution_id=req.execution_id)


@router.get("/cleanup/history")
def cleanup_history(limit: int = Query(50, ge=1, le=500),
                    status: Optional[str] = Query(None),
                    db: Session = Depends(get_db), user: User = Depends(require_god)):
    """Every cleanup ever planned, and what became of it.

    Includes previews that were never confirmed and attempts that failed. A
    history that only lists successes cannot answer "did anyone try to delete
    this", which is the question you ask when something is missing.
    """
    from app.models.cleanup_models import CleanupExecution
    q = db.query(CleanupExecution)
    if status:
        q = q.filter(CleanupExecution.status == status)
    rows = q.order_by(CleanupExecution.created_at.desc()).limit(limit).all()
    return {"executions": [r.as_dict() for r in rows], "total": len(rows)}


@router.get("/cleanup/history/{execution_id}")
def cleanup_receipt(execution_id: str, db: Session = Depends(get_db),
                    user: User = Depends(require_god)):
    """The full receipt for one cleanup, manifest included."""
    from app.models.cleanup_models import CleanupExecution
    row = (db.query(CleanupExecution)
           .filter(CleanupExecution.id == execution_id).first())
    if not row:
        raise HTTPException(status_code=404, detail="Cleanup execution not found")
    return row.as_dict(include_manifest=True)


@router.post("/{org_id}/deactivate")
def deactivate(org_id: str, db: Session = Depends(get_db),
               user: User = Depends(require_god)):
    """Suspend a customer. Deliberately not a delete — nothing is removed."""
    org = _load(db, org_id)
    was = bool(org.is_active)
    org.is_active = False
    log_action(db, org.id, user.id, action="customer.deactivated",
               target_type="organization", target_id=org.id,
               platform_id=org.platform_id,
               before={"is_active": was}, after={"is_active": False}, commit=False)
    db.commit()
    return {"activated": False, "customer": _brief(db, org)}
