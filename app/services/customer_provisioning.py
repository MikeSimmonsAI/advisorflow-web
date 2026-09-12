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

# The one industry registry. A customer created without a stated business type
# resolves through here to neutral defaults rather than inheriting whichever
# vertical happened to be the platform's first.
from app.services import industry_templates as _industry_templates

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
                    slug: Optional[str] = None, industry: Optional[str] = None,
                    plan: str = "trial", timezone: str = "America/Chicago",
                    legal_name: Optional[str] = None,
                    phone: Optional[str] = None, address: Optional[str] = None,
                    primary_location: Optional[Dict[str, Any]] = None) -> Tuple[Organization, Optional[Location]]:
    """Create a customer organization and, optionally, its first location.

    Does NOT commit — the router commits, so a half-made customer cannot
    survive a later failure in the same request.

    INDUSTRY DEFAULTED TO "funeral" AND THAT WAS THE BUG. Every caller that did
    not name a business type — which is the ordinary case when an operator
    creates a customer — produced an organization carrying a funeral home's
    lead tiers, appointment types and AI vocabulary, in whatever business the
    customer actually operates. It now resolves through the industry template
    registry, whose fallback is a neutral service-business configuration, and
    the resolved key is STORED so the org's configuration is explicit rather
    than implied by a default that may change.
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
        industry=_industry_templates.normalize(industry),
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

    ONE HUMAN IDENTITY, MANY AUTHORIZED CONTEXTS.
    =============================================
    This function used to refuse two perfectly legitimate people:

      * brand-sales staff (`organization_id` IS NULL), on the grounds that
        making them a customer user "would change what they are";
      * anybody already in another customer, on the grounds that customer
        tenancy is one column and so cannot hold two.

    Both reasons described the schema as it was BEFORE `workspace_access`
    moved customer tenancy onto `Membership` rows with `SCOPE_CUSTOMER_ORG`.
    That module's own docstring names this file's stale comment as one of the
    two it was written to close, and `customer_activation.add_existing_user`
    was migrated at the same time. This one was not, so the Launch Engine
    inherited a refusal the platform had already stopped meaning: a
    salesperson who sold a deal could not be given onboarding access to the
    customer he had just sold, and the screen told the operator to invent a
    second email address for the same human.

    Adding customer access is now ADDITIVE. It grants a membership; it does
    not move, downgrade or overwrite what the person already is. Their
    brand-sales membership, their platform role and any other workspace they
    hold are all untouched — see `add_customer_user`.

    WHAT STILL REFUSES, AND WHY IT IS NOT THE SAME THING
    ----------------------------------------------------
    A god_admin or super_admin is still refused. That is not a tenancy limit
    that membership fixed; it is the standing rule that provisioning must
    never be a door through which the control plane acquires a customer
    tenancy. Root authority is not an ordinary multi-context case.
    """
    email = assert_email(email)
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        return {"email": email, "exists": False, "can_add": True,
                "action": "create", "reason": None, "user": None}

    from app.models.sales_models import (Membership, SCOPE_BRAND_SALES_ORG,
                                         SCOPE_CUSTOMER_ORG, SCOPE_PLATFORM)
    memberships = db.query(Membership).filter(Membership.user_id == user.id).all()
    active = [m for m in memberships if m.is_active]

    # What this person already holds, named so an operator can see that it
    # survives rather than having to trust that it does.
    workspace_rows = [m for m in active if m.scope_type == SCOPE_CUSTOMER_ORG]
    other_workspaces = [m for m in workspace_rows if m.scope_id != org_id]
    org_names = {}
    if other_workspaces:
        ids = [m.scope_id for m in other_workspaces]
        org_names = {o.id: o.name for o in db.query(Organization)
                     .filter(Organization.id.in_(ids)).all()}
    brand_sales_count = len([m for m in active
                             if m.scope_type == SCOPE_BRAND_SALES_ORG])
    platform_count = len([m for m in active if m.scope_type == SCOPE_PLATFORM])

    summary = {
        "id": user.id, "email": user.email, "full_name": user.full_name,
        "role": user.role, "is_active": bool(user.is_active),
        "organization_id": user.organization_id,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "has_usable_login": not bool(user.must_change_password),
        "memberships": [
            {"scope_type": m.scope_type, "scope_id": m.scope_id, "role": m.role,
             "is_active": bool(m.is_active)} for m in memberships
        ],
        # The contexts that will still be there afterwards.
        "brand_sales_memberships": brand_sales_count,
        "platform_memberships": platform_count,
        "other_workspaces": [
            {"organization_id": m.scope_id,
             "organization_name": org_names.get(m.scope_id, m.scope_id),
             "role": m.role}
            for m in other_workspaces
        ],
    }

    if user.role in ("god_admin", "super_admin"):
        return {"email": email, "exists": True, "can_add": False, "action": "refuse",
                "reason": "This is a platform control-plane account. Adding it to a "
                          "customer would give a customer tenancy to an operator.",
                "user": summary}

    already_here = (user.organization_id == org_id
                    or any(m.scope_id == org_id for m in workspace_rows))
    if already_here:
        return {"email": email, "exists": True, "can_add": True, "action": "reuse",
                "reason": "Already a member of this customer. Their existing account "
                          "will be updated, not duplicated.",
                "user": summary}

    # An existing identity that belongs somewhere else — brand sales, another
    # customer, or both. This is the multi-context case, and it is allowed.
    held = []
    if brand_sales_count:
        held.append("brand-sales access")
    if platform_count:
        held.append("brand/platform access")
    if other_workspaces:
        held.append("%d other workspace%s"
                    % (len(other_workspaces),
                       "" if len(other_workspaces) == 1 else "s"))
    if not held and user.organization_id:
        other = db.query(Organization).filter(
            Organization.id == user.organization_id).first()
        held.append("access to %s" % (other.name if other else "another customer"))

    return {
        "email": email, "exists": True, "can_add": True, "action": "add_context",
        "reason": ("Existing AdvisorFlow identity. Their current access%s stays "
                   "exactly as it is; this customer is ADDED as a separate "
                   "workspace membership. No second account is created."
                   % ((" (" + ", ".join(held) + ")") if held else "")),
        "user": summary,
    }


def add_customer_user(db: Session, org: Organization, actor: User, *, email: str,
                      full_name: str, role: str = "advisor",
                      location_ids: Optional[List[str]] = None) -> Tuple[User, bool]:
    """Add or reuse a person on a customer. Returns (user, created).

    WHAT THIS WRITES, AND WHAT IT REFUSES TO TOUCH
    ==============================================
    The grant is an ACTIVE `Membership(scope_type=customer_org)` row, which is
    what `workspace_access` treats as authority to enter a workspace. It is
    written on both branches — a person created here and a person reused here
    both end up holding the same kind of grant, rather than the new one relying
    on the legacy column and a login-time backfill to become real.

    For an identity that already exists elsewhere, three things are left alone
    on purpose, mirroring `customer_activation.add_existing_user`:

      users.organization_id   seeded only when EMPTY. Already pointing at a
                              tenant means they work there today, and
                              repointing it would move them out of it — the
                              transfer the old 409 was really protecting
                              against, and still not what "add" means.
      users.role              their PLATFORM role. Changed only when the legacy
                              column points at this customer, i.e. when it is
                              genuinely the role being described. A
                              salesperson does not become an advisor because
                              he also administers a customer.
      every other membership  brand-sales, platform and other workspaces are
                              never read for permission here and never written.

    So the workspace role lives on the membership and the platform role lives
    on the user row, and neither is inferred from the other.
    """
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
        # PLAN LIMIT. Only on the CREATE branch: reactivating or re-roling an
        # identity that already belongs to this org adds no seat, and refusing
        # it would strand a customer at their ceiling with a deactivated
        # colleague they cannot restore.
        from app.services import plan_limits
        plan_limits.require_capacity(db, org, plan_limits.LIMIT_USERS, adding=1)
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
        # READ BEFORE WRITING. Whether the column already described THIS
        # customer has to be decided before anything below seeds it, or
        # seeding makes the role test true in the same pass and rewrites the
        # platform role of the very person it was meant to protect.
        was_homed_here = (user.organization_id == org.id)
        summary = look.get("user") or {}
        # A STANDING PLATFORM IDENTITY KEEPS ITS EMPTY COLUMN.
        #
        # `active_workspace_org_id` resolves a request's tenant from the
        # SELECTED workspace first and `users.organization_id` second. So
        # seeding the column is right for somebody whose only context is this
        # workspace — without it they would have no default tenant and every
        # route that still reads the column would see nothing. It is wrong for
        # brand-sales or brand/platform staff: it would make a customer their
        # default tenancy, which is how a salesperson quietly becomes a
        # customer-only person. They reach this workspace by selecting it, and
        # their membership is what makes that selection real.
        has_platform_identity = bool(summary.get("brand_sales_memberships")
                                     or summary.get("platform_memberships"))
        if not user.organization_id and not has_platform_identity:
            user.organization_id = org.id
            if not getattr(user, "platform_id", None):
                user.platform_id = org.platform_id
        # Only when the column was ALREADY describing this customer is
        # `users.role` the role being set here. Otherwise it belongs to another
        # context and the workspace role on the membership below is the answer.
        if was_homed_here and role != user.role:
            user.role = role
        user.is_active = True

    # THE GRANT ITSELF — additive, idempotent, and the thing authorization
    # actually reads. `grant_workspace_membership` updates the one row for
    # (person, this workspace) rather than adding a second, so calling this
    # twice re-roles instead of duplicating.
    from app.services import workspace_access
    membership = workspace_access.grant_workspace_membership(
        db, user_id=user.id, organization_id=org.id, role=role,
        granted_by=actor.id, commit=False,
        # The create branch above already reserved this seat through
        # require_capacity, and the flushed `users` row is now counted as
        # homed — checking again here would refuse a plan's last user.
        check_capacity=not created)
    # The per-request memo would otherwise still say this person has no
    # membership here, for the rest of this request.
    workspace_access.invalidate_workspace_memberships(user)

    assign_locations(db, org, user, actor, location_ids or [], commit=False)

    log_action(
        db, org.id, actor.id,
        action="customer.user_added" if created else "customer.user_updated",
        target_type="user", target_id=user.id,
        platform_id=org.platform_id,
        details={"email": user.email, "role": role, "created_identity": created,
                 "locations": location_ids or [],
                 # Named in the record because "did this move him or add to
                 # him" is the question a reader of this history will have.
                 "workspace_membership_id": membership.id,
                 "workspace_role": membership.role,
                 "grant": "additive",
                 "seconded": (user.organization_id != org.id)},
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


def customer_people(db: Session, org_id: str) -> List[User]:
    """Everyone with access to this customer, by EITHER route.

    Two doors, one list. `users.organization_id` is the legacy home column,
    and an ACTIVE customer_org `Membership` is the additive grant that
    `workspace_access` treats as authority. Listing only the column is how a
    brand-sales person who was given org_admin access to a customer stayed
    invisible on that customer's own People tab — and, worse, invisible to
    `launch_invitation.status`, which then reported the onboarding as never
    sent because it could not see the person it had just been sent to.

    Union, de-duplicated: somebody homed here who also holds a membership is
    one person, not two rows.
    """
    from app.models.sales_models import Membership, SCOPE_CUSTOMER_ORG

    homed = db.query(User).filter(User.organization_id == org_id).all()
    member_ids = [r[0] for r in db.query(Membership.user_id)
                  .filter(Membership.scope_type == SCOPE_CUSTOMER_ORG,
                          Membership.scope_id == org_id,
                          Membership.is_active == True).all()]  # noqa: E712
    seen = {u.id: u for u in homed}
    missing = [i for i in member_ids if i not in seen]
    if missing:
        for u in db.query(User).filter(User.id.in_(missing)).all():
            seen[u.id] = u
    return sorted(seen.values(), key=lambda u: (u.full_name or "", u.email or ""))


def customer_users(db: Session, org_id: str) -> List[Dict[str, Any]]:
    from app.models.sales_models import Membership, SCOPE_CUSTOMER_ORG

    users = customer_people(db, org_id)
    # The role IN THIS WORKSPACE, which is the membership's and never
    # `users.role` — that column may be describing another context entirely.
    ws_roles = {
        m.user_id: m.role for m in db.query(Membership)
        .filter(Membership.scope_type == SCOPE_CUSTOMER_ORG,
                Membership.scope_id == org_id,
                Membership.is_active == True).all()}      # noqa: E712
    links = {}
    for ul in db.query(UserLocation).filter(UserLocation.organization_id == org_id).all():
        links.setdefault(ul.user_id, []).append(ul.location_id)
    names = {l.id: l.name for l in db.query(Location)
             .filter(Location.organization_id == org_id).all()}
    return [
        {
            "id": u.id, "email": u.email, "full_name": u.full_name,
            # `role` stays the workspace answer this screen has always shown;
            # where the person is only seconded here, that is the membership's
            # role rather than their platform one.
            "role": ws_roles.get(u.id) or u.role,
            "platform_role": u.role,
            "workspace_role": ws_roles.get(u.id),
            # True for somebody whose home is elsewhere — a salesperson who
            # also administers this customer. The operator should be able to
            # see that without reading the schema.
            "is_seconded": (u.organization_id != org_id),
            "is_active": bool(u.is_active),
            "must_change_password": bool(u.must_change_password),
            "has_signed_in": u.last_login_at is not None,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "location_ids": links.get(u.id, []),
            "locations": [names.get(x, x) for x in links.get(u.id, [])],
        }
        for u in users
    ]
