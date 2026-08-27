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


def _brief(db: Session, org: Organization) -> Dict[str, Any]:
    platform = (db.query(Platform).filter(Platform.id == org.platform_id).first()
                if org.platform_id else None)
    counts = cr.customer_user_counts(db, org.id)
    return {
        "id": org.id, "name": org.name, "slug": org.slug,
        "industry": org.industry, "plan": org.plan,
        "is_active": bool(org.is_active),
        "platform_id": org.platform_id,
        "brand": None if platform is None else platform.name,
        "user_count": counts["total"], "active_users": counts["active"],
        "location_count": db.query(Location).filter(
            Location.organization_id == org.id).count(),
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
    return {"customers": [_brief(db, o) for o in orgs], "total": len(orgs)}


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


@router.post("/cleanup/preview")
def cleanup_preview(req: CleanupRequest, db: Session = Depends(get_db),
                    user: User = Depends(require_god)):
    """What WOULD be removed. Deletes nothing.

    POST rather than GET because the rule set is structured, not because it
    changes anything — it does not.
    """
    from app.services import data_cleanup
    return data_cleanup.preview(db, rules=req.rules, org_ids=req.org_ids,
                                import_batches=req.import_batches,
                                include_manifest=req.include_manifest)


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
                                confirmation=req.confirmation)


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
