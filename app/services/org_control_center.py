"""ORGANIZATION CONTROL CENTER — one read, every section, all of it observed.

`GET /god/customers/{id}/control-center` is assembled here. Every number is a
query; every status is `org_blueprints`' evaluation of a real row. Where the
schema cannot answer a question the field is `None` with a `*_note` saying so —
never a placeholder that looks like data.

Nothing here writes. Tenant scoping: every query below filters on the one
organization id the router already loaded through `_load` (404 for unknown or
platform pseudo-orgs).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.models import Organization, Platform, User
from app.models.location_models import Location, UserLocation
from app.services import customer_provisioning as cp
from app.services import customer_readiness as cr
from app.services import org_blueprints as ob


def _iso(dt) -> Optional[str]:
    return dt.isoformat() if dt else None


# ── header ──────────────────────────────────────────────────────────────────

def header(db: Session, org: Organization) -> Dict[str, Any]:
    from app.services import industry_templates
    platform = (db.query(Platform).filter(Platform.id == org.platform_id).first()
                if org.platform_id else None)
    primary = (db.query(Location)
               .filter(Location.organization_id == org.id,
                       Location.is_primary == True).first())  # noqa: E712
    owner = None
    try:
        from app.models.implementation_models import Implementation
        impl = (db.query(Implementation)
                .filter(Implementation.organization_id == org.id).first())
        if impl is not None and impl.owner_user_id:
            u = db.query(User).filter(User.id == impl.owner_user_id).first()
            if u is not None:
                owner = {"id": u.id, "name": u.full_name, "email": u.email}
    except Exception:
        owner = None
    try:
        industry_label = industry_templates.resolve(org.industry).get("label")
    except Exception:
        industry_label = None
    lifecycle = getattr(org, "lifecycle_status", None)
    return {
        "id": org.id,
        "customer_id": org.id,
        "name": org.name,
        "slug": org.slug,
        "display_name": getattr(org, "brand_name", None),
        "brand": None if platform is None else platform.name,
        "platform_id": org.platform_id,
        "industry": org.industry,
        "industry_label": industry_label,
        "plan": org.plan,
        "is_active": bool(org.is_active),
        "lifecycle_status": lifecycle,
        "status": lifecycle or ("active" if org.is_active else "suspended"),
        "is_demo": bool(getattr(org, "is_demo", False)),
        "created_at": _iso(org.created_at),
        # The organization row has no timezone column. The primary location's
        # timezone is the one real answer; otherwise it is unknown.
        "timezone": primary.timezone if primary is not None else None,
        "timezone_source": ("primary_location" if primary is not None and primary.timezone
                            else None),
        "phone": getattr(org, "org_phone", None),
        "address": getattr(org, "org_address", None),
        "support_email": getattr(org, "support_email", None),
        # No primary-contact field exists on the organization.
        "primary_contact": None,
        "primary_contact_note": "Not tracked — the organization record has no "
                                "primary-contact field.",
        "implementation_owner": owner,
    }


# ── people ──────────────────────────────────────────────────────────────────

def _invitation(user: User, link) -> Dict[str, str]:
    """Derived only from last_login_at, must_change_password and the latest
    staff-activation link row. No invitation-status column exists."""
    if user.last_login_at is not None:
        return {"status": "accepted", "label": "Accepted",
                "reason": "Has signed in."}
    if link is not None:
        st = (link.status or "").lower()
        if st == "accepted":
            return {"status": "accepted", "label": "Accepted",
                    "reason": "Setup link used; has not signed in since."}
        if st == "revoked":
            return {"status": "revoked", "label": "Revoked",
                    "reason": "The last setup link was revoked."}
        expired = (st == "expired" or (link.expires_at is not None
                                       and link.expires_at < datetime.utcnow()))
        if expired:
            return {"status": "expired", "label": "Invite expired",
                    "reason": "The last setup link expired unused."}
        return {"status": "pending", "label": "Invited",
                "reason": "A setup link was issued and not yet used."}
    if user.must_change_password:
        return {"status": "not_invited", "label": "Not invited",
                "reason": "Account exists; no setup link has been issued."}
    return {"status": "never_signed_in", "label": "Never signed in",
            "reason": "Account has a password but has never signed in."}


def people(db: Session, org: Organization) -> List[Dict[str, Any]]:
    rows = cp.customer_users(db, org.id)
    ids = [r["id"] for r in rows]
    users = {u.id: u for u in db.query(User).filter(User.id.in_(ids)).all()} if ids else {}
    links = {}
    if ids:
        from app.models.staff_models import StaffActivation
        for a in (db.query(StaffActivation)
                  .filter(StaffActivation.user_id.in_(ids))
                  .order_by(StaffActivation.created_at.asc()).all()):
            links[a.user_id] = a            # last one wins → latest
    now = datetime.utcnow()
    out = []
    for r in rows:
        u = users.get(r["id"])
        inv = _invitation(u, links.get(r["id"])) if u is not None else None
        locked = bool(u is not None and getattr(u, "lockout_until", None)
                      and u.lockout_until > now)
        if not r["is_active"]:
            access = {"status": "deactivated", "label": "Deactivated"}
        elif locked:
            access = {"status": "locked", "label": "Locked out"}
        else:
            access = {"status": "active", "label": "Active"}
        out.append({**r,
                    "workspace": org.name,
                    "invitation": inv,
                    "access": access})
    return out


# ── locations ───────────────────────────────────────────────────────────────

def locations(db: Session, org: Organization) -> List[Dict[str, Any]]:
    rows = cp.list_locations(db, org.id)
    staff: Dict[str, List[Dict[str, str]]] = {}
    q = (db.query(UserLocation.location_id, User.id, User.full_name, User.email)
         .join(User, User.id == UserLocation.user_id)
         .filter(UserLocation.organization_id == org.id))
    for loc_id, uid, name, email in q.all():
        staff.setdefault(loc_id, []).append({"id": uid, "name": name or email})
    for r in rows:
        r["assigned_users"] = staff.get(r["id"], [])
        # The only booking-routing rule the schema holds: the primary location
        # is where bookings route by default. Nothing per-location beyond that.
        r["booking_route"] = ("Default booking route" if r["is_primary"] else
                              "Routes only when chosen explicitly")
        r["communication_routing"] = ({"phone": r["phone"]} if r["phone"] else None)
    return rows


# ── recent activity ────────────────────────────────────────────────────────

def recent_activity(db: Session, org: Organization, limit: int = 12) -> List[Dict[str, Any]]:
    from app.models.models import AuditLogEntry
    rows = (db.query(AuditLogEntry)
            .filter(AuditLogEntry.organization_id == org.id)
            .order_by(AuditLogEntry.created_at.desc())
            .limit(limit).all())
    actor_ids = {r.actor_user_id for r in rows if r.actor_user_id}
    actors = ({u.id: (u.full_name or u.email) for u in
               db.query(User).filter(User.id.in_(actor_ids)).all()} if actor_ids else {})
    out = []
    for r in rows:
        try:
            details = json.loads(r.details) if r.details else None
        except (ValueError, TypeError):
            details = None
        out.append({"id": r.id, "at": _iso(r.created_at), "action": r.action,
                    "actor": actors.get(r.actor_user_id),
                    "target_type": r.target_type, "target_id": r.target_id,
                    "details": details if isinstance(details, dict) else None})
    return out


# ── metrics ─────────────────────────────────────────────────────────────────

def metrics(db: Session, org: Organization, blueprint_key: str) -> Dict[str, Any]:
    from app.models.models import Lead, PipelineConversation, BookingLink
    since = datetime.utcnow() - timedelta(days=30)

    def _count(q):
        try:
            return int(q.scalar() or 0)
        except Exception:
            return None

    leads_total = _count(db.query(func.count(Lead.id)).filter(Lead.organization_id == org.id))
    leads_30 = _count(db.query(func.count(Lead.id))
                      .filter(Lead.organization_id == org.id, Lead.created_at >= since))
    convs = _count(db.query(func.count(PipelineConversation.id))
                   .filter(PipelineConversation.organization_id == org.id))
    appts = _count(db.query(func.count(BookingLink.id))
                   .join(Lead, Lead.id == BookingLink.lead_id)
                   .filter(Lead.organization_id == org.id,
                           BookingLink.status.in_(("booked", "confirmed"))))
    try:
        from app.models.intake_models import OrgContact
        contacts = _count(db.query(func.count(OrgContact.id))
                          .filter(OrgContact.organization_id == org.id))
    except Exception:
        contacts = None
    deals = None
    deals_note = "Deals are tracked only for wholesale organizations."
    if blueprint_key == "wholesale_real_estate":
        from app.models.wholesale_models import WholesaleDeal
        deals = _count(db.query(func.count(WholesaleDeal.id))
                       .filter(WholesaleDeal.organization_id == org.id))
        deals_note = None
    return {
        "leads": {"total": leads_total, "last_30_days": leads_30,
                  "source": "leads"},
        "contacts": {"total": contacts, "source": "org_contacts"},
        "conversations": {"total": convs, "source": "pipeline_conversations"},
        "appointments": {"total": appts,
                         "source": "booking_links with status booked/confirmed"},
        "deals": {"total": deals, "source": "wholesale_deals" if deals is not None else None,
                  "note": deals_note},
        # Trends are omitted on purpose: none is computed from a stored
        # previous period, so none is shown.
        "trends": None,
    }


# ── enabled tools ──────────────────────────────────────────────────────────

def enabled_tools(org: Organization, evaluation: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import entitlements
    report = entitlements.feature_report(org)
    tiers = {}
    for tier in ("core_required", "vertical_required", "optional"):
        for f in evaluation["features"][tier]:
            tiers[f["key"]] = f["tier"]
    tools = [{"key": f["key"], "label": f["label"], "enabled": f["enabled"],
              "tier": tiers.get(f["key"], "not_in_blueprint"),
              "requires": f["requires"]}
             for f in report["available"]]
    return {"mode": report["mode"], "enabled_count": report["enabled_count"],
            "tools": tools, "dependency_gaps": report["dependency_gaps"],
            "below_plan": report["below_plan"]}


# ── administration split ───────────────────────────────────────────────────

# What an organization admin can change for themselves, today, with nothing
# delegated: the `require_admin` routes in app/routers/org_settings_router.py.
_ORG_ADMIN_SETTINGS = [
    ("branding", "Branding (name, logo, colours)", "PATCH /org-settings/branding", None),
    ("contact", "Contact details", "PATCH /org-settings/contact", None),
    ("industry", "Industry template", "PATCH /org-settings/industry", None),
    ("tiers", "Lead tier configuration", "PATCH /org-settings/tiers", None),
    ("social_links", "Social links", "PATCH /org-settings/social-links", None),
    ("email_sender", "Email sender identity", "PATCH /org-settings/email-sender", None),
    ("calendar_provider", "Calendar provider", "PATCH /org-settings/calendar-provider", None),
    ("users", "Invite and manage their own users", "/admin/users", "users"),
]


def administration(db: Session, org: Organization) -> Dict[str, Any]:
    from app.services import capabilities as caps
    from app.services import entitlements
    report = caps.administration_report(db, org)
    not_org_scoped = set(getattr(caps, "BRAND_SCOPED_CAPABILITIES", ())) | \
        set(getattr(caps, "PLATFORM_SCOPED_CAPABILITIES", ()))
    available = [c for c in report["self_management"]["available"]
                 if c["key"] not in not_org_scoped]

    self_manageable = [
        {"key": k, "label": label, "route": route,
         "available": (True if feat is None else entitlements.org_has_feature(org, feat)),
         "requires_feature": feat,
         "changed_by": "Organization admin (org_admin)",
         "gate": "Always self-managed" if feat is None else "Needs the '%s' feature" % feat}
        for k, label, route, feat in _ORG_ADMIN_SETTINGS
    ]
    delegated = [
        {"key": c["key"], "label": c["label"], "why": c["why"],
         "self_management_allowed": c["allowed"],
         "feature_enabled": c["feature_enabled"],
         "blocked_reason": c["blocked_reason"],
         "holders": [u["full_name"] or u["email"]
                     for u in report["administrators"]["users"]
                     if c["key"] in u["effective"]],
         "changed_by": "Authorized administrator — only when God allows the "
                       "organization to self-manage it AND grants that person"}
        for c in available if c["delegable"]
    ]
    platform_only = [
        {"key": c["key"], "label": c["label"], "why": c["why"],
         "changed_by": "Platform (God) only — never delegable"}
        for c in available if not c["delegable"]
    ]
    platform_only += [
        {"key": "entitlements", "label": "Feature entitlements",
         "why": "What the organization may use.",
         "changed_by": "Platform (God) only — PUT /god/customers/{id}/features"},
        {"key": "activation", "label": "Activate / suspend organization",
         "why": "Flips the customer live or suspends it.",
         "changed_by": "Platform (God) only"},
    ]
    return {"self_manageable": self_manageable,
            "authorized_admin": delegated,
            "platform_only": platform_only,
            "administrators": report["administrators"]}


# ── the whole payload ──────────────────────────────────────────────────────

def control_center(db: Session, org: Organization) -> Dict[str, Any]:
    evaluation = ob.evaluate_blueprint(db, org)
    bp_key = evaluation["blueprint"]["key"]
    legacy = cr.readiness(db, org)
    ppl = people(db, org)
    locs = locations(db, org)
    counts = cr.customer_user_counts(db, org.id)
    items = {i["key"]: i for i in evaluation["setup"]}
    primary = next((l for l in locs if l["is_primary"]), None)
    return {
        "header": header(db, org),
        "blueprint": {"key": bp_key, "label": evaluation["blueprint"]["label"],
                      "selected_because": evaluation["blueprint"]["selected_because"],
                      "missing_required_features":
                          evaluation["features"]["missing_required"],
                      "overall": evaluation["overall"]},
        "readiness": {
            "items": evaluation["setup"],
            "vertical_items": evaluation["vertical_setup"],
            "complete": evaluation["activation"]["complete"],
            "total": evaluation["activation"]["total"],
            "percent": evaluation["activation"]["percent"],
            # The activation gate's own words (POST /activate uses these).
            "blockers": legacy["blockers"],
            "warnings": legacy["warnings"],
            "can_activate": legacy["can_activate"],
        },
        "essentials": {
            "company": {"name": org.name, "industry": org.industry, "plan": org.plan,
                        "created_at": _iso(org.created_at),
                        "status": items["company"]["status"]},
            "primary_location": (None if primary is None else
                                 {k: primary[k] for k in ("id", "name", "address_line1",
                                                          "city", "state", "postal_code",
                                                          "timezone")}),
            "location_count": len(locs),
            "users": counts,
            "booking": {"status": items["booking"]["status"],
                        "reason": items["booking"]["reason"]},
            "communications": {"sms": items["sms"]["status"],
                               "email": items["email"]["status"]},
        },
        "people": ppl,
        "locations": locs,
        "enabled_tools": enabled_tools(org, evaluation),
        "recent_activity": recent_activity(db, org),
        "metrics": metrics(db, org, bp_key),
        "administration": administration(db, org),
        # The existing provisioning summary's observed sections, for the
        # Operations tab (same words POST /activate is judged by).
        "operations": {k: legacy["sections"][k] for k in
                       ("communications_sms", "communications_email", "calendar",
                        "booking", "ai", "data")},
    }
