"""CREATE A CUSTOMER — the supported production path, with no shell involved.

There was already a way to create a customer organization: win a deal, then
provision from the Won queue (`provisioning.py`). That is the right flow when a
deal exists, and it is untouched. It is not a general answer, because it makes
"can I stand up a customer" depend on there being an Opportunity, which is why
the only way to create SCI today would have been a seed script.

So this is the other half: create a customer directly, configure it, staff it,
and activate it — every step an HTTP call an owner can make from a browser.

THREE RULES THIS FILE ENFORCES.

A CUSTOMER ALWAYS BELONGS TO A BRAND. `platform_id` is required, not optional
and not defaulted. `POST /onboarding/register` and the old `provision-client`
both created organizations with a NULL platform, and an org with no platform
sits outside every scoping decision in the system — invisible to the very
operator who is supposed to own it. Refusing is cheaper than explaining.

ONE HUMAN, ONE IDENTITY. Email is normalised and looked up BEFORE anything is
created, exactly as `sales_staff.py` does for the sales side. A person who
already exists is reused, never duplicated. Where this file cannot honour that
rule it says so out loud rather than quietly making a second row — see
`lookup_identity`, which reports the real schema limit instead of pretending.

STATUS IS OBSERVED, NEVER ASSERTED. `readiness()` reports CONNECTED only where
a stored credential actually exists to look at. Everything else is
NOT_CONFIGURED. A provisioning summary that says "Twilio: CONNECTED" because a
step was clicked is worse than no summary at all.
"""

import json
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.models import Organization, Platform, User
from app.models.location_models import Location, UserLocation
from app.routers.audit_log_router import log_action
from app.services.sales_staff import normalize_email, assert_email, _unknowable_password
from app.services.auth_service import hash_password

# Roles a customer-side person may hold. Deliberately excludes every
# control-plane role: provisioning a customer must never be a way to mint a
# super_admin. Mirrors CUSTOMER_ADMIN_ROLES in customer_activation.py.
CUSTOMER_ROLES = ("org_admin", "advisor", "viewer")

# Status vocabulary for readiness(). There is no "HEALTHY" and no "ACTIVE" —
# those words invite a screen to imply a check that nobody performed.
ST_CONFIGURED = "CONFIGURED"
ST_NOT_CONFIGURED = "NOT_CONFIGURED"
ST_PARTIAL = "PARTIAL"
ST_NONE = "NONE"


# ── slugs ───────────────────────────────────────────────────────────────────

def slugify(raw: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (raw or "").strip().lower()).strip("-")
    return s or "customer"


def unique_slug(db: Session, base: str) -> str:
    base = slugify(base)
    if not db.query(Organization).filter(Organization.slug == base).first():
        return base
    for n in range(2, 200):
        cand = "%s-%d" % (base, n)
        if not db.query(Organization).filter(Organization.slug == cand).first():
            return cand
    return "%s-%s" % (base, uuid.uuid4().hex[:6])


# ── STEP 1: the company ─────────────────────────────────────────────────────

def create_customer(db: Session, actor: User, *, name: str, platform_id: str,
                    slug: Optional[str] = None, industry: str = "funeral",
                    plan: str = "trial", timezone: str = "America/Chicago",
                    legal_name: Optional[str] = None,
                    phone: Optional[str] = None, address: Optional[str] = None,
                    primary_location: Optional[Dict[str, Any]] = None) -> Tuple[Organization, Optional[Location]]:
    """Create a customer organization and, optionally, its first location.

    Does NOT commit — the router commits, so a half-made customer cannot
    survive a later failure in the same request.
    """
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Company name is required.")

    if not platform_id:
        raise HTTPException(
            status_code=400,
            detail="A brand is required. A customer organization with no platform sits "
                   "outside every scoping decision in the system, including the customer "
                   "list of the operator who owns it.")
    platform = db.query(Platform).filter(Platform.id == platform_id).first()
    if not platform:
        raise HTTPException(status_code=404, detail="Brand/platform not found")

    final_slug = slugify(slug) if slug else unique_slug(db, name)
    if db.query(Organization).filter(Organization.slug == final_slug).first():
        raise HTTPException(status_code=400,
                            detail="Slug '%s' is already taken." % final_slug)

    org = Organization(
        id=str(uuid.uuid4()),
        name=name,
        slug=final_slug,
        platform_id=platform_id,
        industry=industry,
        plan=plan,
        is_active=True,
        org_phone=phone,
        org_address=address,
        # Explicit empty allow-list, not NULL. NULL means "all features" for
        # backward compatibility with orgs that predate entitlement, and a brand
        # new customer should start with nothing switched on rather than
        # everything.
        enabled_features=json.dumps([]),
    )
    if legal_name and hasattr(org, "brand_name"):
        org.brand_name = legal_name
    db.add(org)
    db.flush()

    loc = None
    if primary_location:
        loc = create_location(db, org, actor, is_primary=True,
                              timezone=primary_location.get("timezone") or timezone,
                              **{k: v for k, v in primary_location.items()
                                 if k in ("name", "address_line1", "address_line2", "city",
                                          "state", "postal_code", "country", "phone",
                                          "email", "operating_hours", "notes")})

    log_action(
        db, org.id, actor.id,
        action="customer.created", target_type="organization", target_id=org.id,
        platform_id=platform_id,
        details={"name": org.name, "slug": org.slug, "industry": industry,
                 "plan": plan, "brand": platform.name,
                 "primary_location": None if loc is None else loc.name},
        commit=False,
    )
    return org, loc


# ── STEP 2: locations ───────────────────────────────────────────────────────

def create_location(db: Session, org: Organization, actor: User, *, name: str,
                    is_primary: bool = False, **fields) -> Location:
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Location name is required.")

    slug = slugify(name)
    existing = (db.query(Location)
                .filter(Location.organization_id == org.id, Location.slug == slug).first())
    if existing:
        raise HTTPException(
            status_code=400,
            detail="This customer already has a location named '%s'." % name)

    hours = fields.pop("operating_hours", None)
    if isinstance(hours, (dict, list)):
        hours = json.dumps(hours)

    loc = Location(
        id=str(uuid.uuid4()),
        organization_id=org.id,
        name=name,
        slug=slug,
        is_primary=False,          # set below, through the one code path that may
        is_active=True,
        operating_hours=hours,
        created_by=actor.id,
        **{k: v for k, v in fields.items()
           if k in ("address_line1", "address_line2", "city", "state", "postal_code",
                    "country", "phone", "email", "timezone", "notes")}
    )
    db.add(loc)
    db.flush()

    # First location is always primary — a customer with locations but no
    # primary is a customer whose booking routing has nowhere to start.
    first = db.query(Location).filter(Location.organization_id == org.id).count() == 1
    if is_primary or first:
        set_primary_location(db, org, loc, actor, commit=False)

    log_action(
        db, org.id, actor.id,
        action="customer.location_created", target_type="location", target_id=loc.id,
        platform_id=org.platform_id,
        details={"name": loc.name, "city": loc.city, "is_primary": bool(loc.is_primary)},
        commit=False,
    )
    return loc


def set_primary_location(db: Session, org: Organization, loc: Location, actor: User,
                         commit: bool = True) -> Location:
    """Exactly one primary per customer, enforced here rather than by an index.

    A partial unique index would say this better but does not port cleanly
    between SQLite and Postgres, and this codebase runs both.
    """
    if loc.organization_id != org.id:
        raise HTTPException(status_code=404, detail="Location not found")
    (db.query(Location)
       .filter(Location.organization_id == org.id, Location.id != loc.id)
       .update({Location.is_primary: False}, synchronize_session=False))
    loc.is_primary = True
    db.flush()
    if commit:
        db.commit()
    return loc


def list_locations(db: Session, org_id: str) -> List[Dict[str, Any]]:
    rows = (db.query(Location)
            .filter(Location.organization_id == org_id)
            .order_by(Location.is_primary.desc(), Location.name.asc()).all())
    return [location_row(db, l) for l in rows]


def location_row(db: Session, l: Location) -> Dict[str, Any]:
    staff = db.query(UserLocation).filter(UserLocation.location_id == l.id).count()
    try:
        hours = json.loads(l.operating_hours) if l.operating_hours else None
    except (ValueError, TypeError):
        hours = None
    return {
        "id": l.id, "name": l.name, "slug": l.slug,
        "is_primary": bool(l.is_primary), "is_active": bool(l.is_active),
        "address_line1": l.address_line1, "address_line2": l.address_line2,
        "city": l.city, "state": l.state, "postal_code": l.postal_code,
        "country": l.country, "phone": l.phone, "email": l.email,
        "timezone": l.timezone,
        "operating_hours": hours,
        # Reported as an explicit unknown rather than as a default.
        "operating_hours_status": ST_CONFIGURED if hours else ST_NOT_CONFIGURED,
        "staff_count": staff,
        "notes": l.notes,
    }


# ── STEP 3/4: people, canonically ───────────────────────────────────────────

def lookup_identity(db: Session, email: str, org_id: str) -> Dict[str, Any]:
    """Email-first lookup, run BEFORE anything is created.

    Reports what reuse is actually possible rather than assuming. Customer
    tenancy in this schema is the single `users.organization_id` column - there
    is no additive customer membership (Membership with SCOPE_CUSTOMER_ORG
    exists but grants nothing and nothing writes it). So a human who already
    belongs to a DIFFERENT customer cannot also belong to this one, and the
    honest answer is to say that plainly. Creating a second row for the same
    person to work around it would be the duplicate this whole design forbids.
    """
    email = assert_email(email)
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        return {"email": email, "exists": False, "can_add": True,
                "action": "create", "reason": None, "user": None}

    from app.models.sales_models import Membership
    memberships = db.query(Membership).filter(Membership.user_id == user.id).all()
    summary = {
        "id": user.id, "email": user.email, "full_name": user.full_name,
        "role": user.role, "is_active": bool(user.is_active),
        "organization_id": user.organization_id,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "memberships": [
            {"scope_type": m.scope_type, "scope_id": m.scope_id, "role": m.role,
             "is_active": bool(m.is_active)} for m in memberships
        ],
    }

    if user.role in ("god_admin", "super_admin"):
        return {"email": email, "exists": True, "can_add": False, "action": "refuse",
                "reason": "This is a platform control-plane account. Adding it to a "
                          "customer would give a customer tenancy to an operator.",
                "user": summary}

    if user.organization_id == org_id:
        return {"email": email, "exists": True, "can_add": True, "action": "reuse",
                "reason": "Already a member of this customer. Their existing account "
                          "will be updated, not duplicated.",
                "user": summary}

    if user.organization_id is None:
        return {"email": email, "exists": True, "can_add": False, "action": "refuse",
                "reason": "This person is brand-sales staff (organization_id is NULL by "
                          "design - they sell the product, they do not use a tenant of "
                          "it). Making them a customer user would change what they are. "
                          "Use a different address for their customer-side account.",
                "user": summary}

    other = db.query(Organization).filter(
        Organization.id == user.organization_id).first()
    return {"email": email, "exists": True, "can_add": False, "action": "refuse",
            "reason": "This person already belongs to customer '%s'. Customer tenancy is "
                      "a single field in this schema, so one identity cannot hold two "
                      "customer organizations. A second user row would be a duplicate "
                      "human, which is worse."
                      % (other.name if other else user.organization_id),
            "user": summary}


def add_customer_user(db: Session, org: Organization, actor: User, *, email: str,
                      full_name: str, role: str = "advisor",
                      location_ids: Optional[List[str]] = None) -> Tuple[User, bool]:
    """Add or reuse a person on a customer. Returns (user, created)."""
    if role not in CUSTOMER_ROLES:
        raise HTTPException(
            status_code=400,
            detail="Role must be one of: %s" % ", ".join(CUSTOMER_ROLES))

    look = lookup_identity(db, email, org.id)
    if not look["can_add"]:
        raise HTTPException(status_code=409, detail=look["reason"])

    created = False
    if look["action"] == "create":
        if not (full_name or "").strip():
            raise HTTPException(status_code=400,
                                detail="Full name is required for a new person.")
        user = User(
            id=str(uuid.uuid4()),
            organization_id=org.id,
            email=look["email"],
            full_name=full_name.strip(),
            # A hash nobody can know. Access is handed over by a one-time link
            # (customer_activation), never by a password in a response body.
            password_hash=hash_password(_unknowable_password()),
            role=role,
            is_active=True,
            must_change_password=True,
        )
        db.add(user)
        db.flush()
        created = True
    else:
        user = db.query(User).filter(User.id == look["user"]["id"]).first()
        if role != user.role:
            user.role = role
        user.is_active = True

    assign_locations(db, org, user, actor, location_ids or [], commit=False)

    log_action(
        db, org.id, actor.id,
        action="customer.user_added" if created else "customer.user_updated",
        target_type="user", target_id=user.id,
        platform_id=org.platform_id,
        details={"email": user.email, "role": role, "created_identity": created,
                 "locations": location_ids or []},
        commit=False,
    )
    return user, created


def assign_locations(db: Session, org: Organization, user: User, actor: User,
                     location_ids: List[str], commit: bool = True) -> List[str]:
    """Set exactly which of this customer's locations a person works at."""
    valid = {
        l.id for l in db.query(Location)
        .filter(Location.organization_id == org.id,
                Location.id.in_(location_ids or [])).all()
    } if location_ids else set()
    unknown = set(location_ids or []) - valid
    if unknown:
        # 404 rather than 400: a location id from another customer must not be
        # confirmed as existing.
        raise HTTPException(status_code=404, detail="Location not found")

    existing = {ul.location_id: ul for ul in db.query(UserLocation)
                .filter(UserLocation.user_id == user.id,
                        UserLocation.organization_id == org.id).all()}

    for loc_id in valid - set(existing):
        db.add(UserLocation(id=str(uuid.uuid4()), user_id=user.id, location_id=loc_id,
                            organization_id=org.id))
    for loc_id in set(existing) - valid:
        db.delete(existing[loc_id])
    db.flush()
    if commit:
        db.commit()
    return sorted(valid)


def customer_users(db: Session, org_id: str) -> List[Dict[str, Any]]:
    users = (db.query(User).filter(User.organization_id == org_id)
             .order_by(User.full_name.asc()).all())
    links = {}
    for ul in db.query(UserLocation).filter(UserLocation.organization_id == org_id).all():
        links.setdefault(ul.user_id, []).append(ul.location_id)
    names = {l.id: l.name for l in db.query(Location)
             .filter(Location.organization_id == org_id).all()}
    return [
        {
            "id": u.id, "email": u.email, "full_name": u.full_name, "role": u.role,
            "is_active": bool(u.is_active),
            "must_change_password": bool(u.must_change_password),
            "has_signed_in": u.last_login_at is not None,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "location_ids": links.get(u.id, []),
            "locations": [names.get(x, x) for x in links.get(u.id, [])],
        }
        for u in users
    ]
