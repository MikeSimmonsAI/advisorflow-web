"""RAPID CLEANING-COMPANY ONBOARDING (#452 / #474).

One provisioning service, two doors:
  * Convert an EvoSys Sales opportunity (a cleaning company the owner recruited)
    -> REUSES app.services.provisioning.provision_customer, the platform's only
       Won -> Customer path (Implementation row, customer_organization_id link,
       idempotent by opportunity).
  * Add a cleaning company directly (no Sales record) -> a new organization on
    the EvoSys Pro platform, idempotent by company name.
Both then run the SAME tail: cleaning identity, the acquisition template
(engine.enable - starts PAUSED), a draft business profile from what is known,
and optional access (client admin invitation via staff_activation one-time
links; operator grant). Nothing is emailed, nothing is sent to prospects, no
pilot clock starts, no God account is ever created.

Create first, complete details later: only the company name is required. An
unknown territory is stored as EMPTY (never "all of DFW"), which blocks master
assignment until it is set - it never blocks creating the company.
"""
from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.acquisition_models import AcquisitionOpportunity, AcquisitionProfile
from app.models.models import Organization, Platform, User
from app.services.acquisition import common as C
from app.services.acquisition import engine as E

PLATFORM_SLUG = "evosyspro"
SERVICES = [
    ("office", "Commercial office cleaning", "office"),
    ("medical", "Medical / dental cleaning", "medical_dental"),
    ("apartment_turnover", "Apartment turnover cleaning", "apartments"),
    ("property_management", "Property management cleaning", "property_management"),
    ("str_turnover", "Short-term rental turnover", "short_term_rental"),
    ("janitorial", "Janitorial contracts", "office"),
    ("move_in_out", "Move-in / move-out cleaning", "apartments"),
]
SERVICE_LABEL = {k: l for k, l, _ in SERVICES}
SERVICE_SEGMENT = {k: s for k, _, s in SERVICES}


def _clean_list(raw, width=80, limit=60) -> List[str]:
    items = raw if isinstance(raw, list) else str(raw or "").replace("\n", ",").split(",")
    out = []
    for x in items:
        s = str(x or "").strip()[:width]
        if s and s not in out:
            out.append(s)
    return out[:limit]


def _platform(db: Session) -> Platform:
    p = db.query(Platform).filter(Platform.slug == PLATFORM_SLUG).first()
    if p is None:
        raise HTTPException(409, "The EvoSys Pro platform record is missing")
    return p


def find_existing(db: Session, name: str) -> Optional[Organization]:
    """A cleaning workspace with this exact name already on EvoSys Pro."""
    nm = (name or "").strip().lower()
    if not nm:
        return None
    return (db.query(Organization).filter(func.lower(Organization.name) == nm,
                                          Organization.industry == "cleaning").first())


def _profile_from(data: Dict[str, Any]) -> Dict[str, Any]:
    """The draft profile: only what the owner actually entered."""
    svc_keys = [s for s in _clean_list(data.get("services")) if s in SERVICE_LABEL]
    custom = _clean_list(data.get("custom_services"), width=120, limit=10)
    services = [SERVICE_LABEL[k] for k in svc_keys] + custom
    segments = sorted({SERVICE_SEGMENT[k] for k in svc_keys})
    out: Dict[str, Any] = {
        "business_name": data.get("name"), "owner_name": data.get("contact_name"),
        "contact_email": data.get("contact_email"), "contact_phone": data.get("contact_phone"),
        "website": data.get("website"),
        "services": services, "segments": segments,
        # NEVER the DFW default: an unconfirmed territory is empty.
        "service_area": _clean_list(data.get("cities")),
        "service_zips": _clean_list(data.get("zips"), width=5, limit=300),
        "exclusions": " ".join(_clean_list(data.get("exclusions"), width=200)),
    }
    if data.get("states"):
        out["service_states"] = _clean_list(data.get("states"), width=20)
    if data.get("radius"):
        out["service_radius_miles"] = data.get("radius")
    if data.get("timezone"):
        out["timezone"] = data.get("timezone")
    if data.get("offer_type"):
        out["offer_type"] = data.get("offer_type")
    return out


def _unknowable_password() -> str:
    return secrets.token_urlsafe(48)


def invite(db: Session, org: Organization, actor: User, email: str, full_name: Optional[str],
           base_url: Optional[str] = None) -> Dict[str, Any]:
    """The client's administrator login, through the EXISTING one-time
    activation link (staff_activation). No password is created, shown or
    emailed by this; the link is returned once for the owner to send."""
    from app.services import staff_activation as SA
    from app.models.staff_models import PURPOSE_SETUP
    from app.services.auth_service import hash_password
    em = (email or "").strip().lower()
    if not em or "@" not in em:
        raise HTTPException(422, "Give the client administrator's email")
    u = db.query(User).filter(func.lower(User.email) == em).first()
    if u is not None:
        if u.role == "god_admin":
            raise HTTPException(409, "That email belongs to a platform owner login and can't be a client user")
        if u.organization_id != org.id:
            raise HTTPException(409, "That email already has an EvoSys login in another workspace. Use a different email for this company.")
        if u.last_login_at is not None and not u.must_change_password:
            return {"status": "accepted", "user_id": u.id, "email": u.email, "setup_url": None,
                    "note": "This person already signs in to this workspace."}
        row, raw = SA.issue(db, u, actor, purpose=PURPOSE_SETUP)
        return {"status": "invitation_pending", "user_id": u.id, "email": u.email,
                "setup_url": SA.activation_url(base_url, raw), "resent": True, "expires_at": C.iso(row.expires_at)}
    u = User(organization_id=org.id, email=em, full_name=(full_name or em.split("@")[0]).strip()[:120],
             password_hash=hash_password(_unknowable_password()), role="org_admin",
             must_change_password=True, is_active=True)
    db.add(u)
    db.flush()
    row, raw = SA.issue(db, u, actor, purpose=PURPOSE_SETUP)
    return {"status": "invitation_pending", "user_id": u.id, "email": u.email,
            "setup_url": SA.activation_url(base_url, raw), "resent": False, "expires_at": C.iso(row.expires_at)}


def onboard(db: Session, actor: User, data: Dict[str, Any], base_url: Optional[str] = None) -> Dict[str, Any]:
    """Create (or find) ONE cleaning company workspace. Safe to retry: the same
    Sales opportunity or the same company name never makes a second workspace."""
    name = (data.get("name") or "").strip()[:150]
    opp_id = data.get("sales_opportunity_id") or None
    created, source = False, "direct"
    org: Optional[Organization] = None
    if opp_id:
        from app.models.sales_models import Opportunity, STAGE_WON
        from app.services import provisioning as PROV
        opp = db.query(Opportunity).filter(Opportunity.id == opp_id).first()
        if opp is None:
            raise HTTPException(404, "Sales opportunity not found")
        name = name or (opp.company_name or "").strip()
        source = "sales"
        if opp.customer_organization_id:
            org = db.query(Organization).filter(Organization.id == opp.customer_organization_id).first()
        if org is None:
            if len(name) < 2:
                raise HTTPException(422, "Give the cleaning company's name")
            same = find_existing(db, name)
            if same is not None and not data.get("create_anyway"):
                return {"status": "duplicate", "name": name, "organization_id": same.id,
                        "message": "A cleaning workspace named %s already exists." % same.name}
            if opp.status != "won" and opp.stage != STAGE_WON:
                # The owner converting a recruited company IS the win.
                opp.status, opp.stage = "won", STAGE_WON
                opp.won_at = opp.won_at or datetime.utcnow()
                opp.stage_changed_at = datetime.utcnow()
                db.flush()
            impl, created = PROV.provision_customer(
                db, opp, actor, org_name=name, industry="cleaning", plan="trial",
                timezone=data.get("timezone") or None, org_phone=data.get("contact_phone") or None,
                notes="Converted to an Acquisition client")
            org = db.query(Organization).filter(Organization.id == impl.organization_id).first()
    else:
        if len(name) < 2:
            raise HTTPException(422, "Give the cleaning company's name")
        org = find_existing(db, name)
        if org is None:
            plat = _platform(db)
            from app.services.provisioning import unique_slug
            org = Organization(name=name, slug=unique_slug(db, name), plan="trial", platform_id=plat.id,
                               is_active=True, industry="cleaning",
                               org_phone=(data.get("contact_phone") or None))
            db.add(org)
            db.commit()
            db.refresh(org)
            created = True
    if org.industry != "cleaning":
        org.industry = "cleaning"
        db.commit()
    had_profile = E.get_profile(db, org.id) is not None
    E.enable(db, org, actor)
    p = E.get_profile(db, org.id)
    fresh = created or not had_profile
    if fresh:
        # Draft profile from what is known; no DFW default, nothing invented.
        p.service_area = C.jdump([])
        p.segments = C.jdump([])
        db.commit()
        E.update_profile(db, org.id, {k: v for k, v in _profile_from(dict(data, name=name)).items() if v is not None},
                         owner=True)
        p = E.get_profile(db, org.id)
        if opp_id:
            p.sales_opportunity_id = opp_id
        p.offer_terms = (data.get("offer_terms") or "").strip()[:1000] or None
        db.commit()
    out: Dict[str, Any] = {"status": "created" if created else "existing", "name": org.name,
                           "organization_id": org.id, "source": source, "paused": bool(p.paused),
                           "pilot_started": p.pilot_started_at is not None,
                           "program_access": "/acquisition-ops", "client_portal": "/program/acquisition",
                           "readiness": E.readiness(org, p), "invitation": None, "operator": None, "sent": 0}
    if data.get("invite_email"):
        try:
            out["invitation"] = invite(db, org, actor, data.get("invite_email"), data.get("contact_name"), base_url)
        except HTTPException as exc:
            db.rollback()
            out["invitation"] = {"status": "failed", "error": exc.detail}
    if data.get("operator_email"):
        try:
            E.grant_operator(db, org, data.get("operator_email"), actor)
            out["operator"] = {"status": "granted", "email": data.get("operator_email")}
        except HTTPException as exc:
            db.rollback()
            out["operator"] = {"status": "failed", "error": exc.detail}
    return out


def batch(db: Session, actor: User, items: List[Dict[str, Any]], defaults: Dict[str, Any],
          base_url: Optional[str] = None) -> Dict[str, Any]:
    """Several companies at once, each its own workspace. One failure never
    stops the rest, and running the same batch again creates nothing twice."""
    if not items:
        raise HTTPException(422, "Choose at least one company")
    if len(items) > 25:
        raise HTTPException(422, "Up to 25 companies at a time")
    results = []
    for it in items:
        data = dict(defaults or {})
        data.update({k: v for k, v in (it or {}).items() if v not in (None, "", [])})
        try:
            results.append(onboard(db, actor, data, base_url))
        except HTTPException as exc:
            db.rollback()
            results.append({"status": "failed", "name": data.get("name") or data.get("sales_opportunity_id"),
                            "error": exc.detail})
        except Exception as exc:  # noqa: BLE001 - report it, keep going
            db.rollback()
            results.append({"status": "failed", "name": data.get("name"), "error": type(exc).__name__})
    return {"created": sum(r["status"] == "created" for r in results),
            "existing": sum(r["status"] == "existing" for r in results),
            "duplicates": sum(r["status"] == "duplicate" for r in results),
            "failed": sum(r["status"] == "failed" for r in results), "results": results, "sent": 0}


# ── the owner's views ───────────────────────────────────────────────────────
def _invitation_state(db: Session, org_id: str) -> Dict[str, Any]:
    """Where the client's own login really stands. Creating a workspace never
    creates a working login: that needs an invitation link the client accepts."""
    from app.services import staff_activation as SA
    users = db.query(User).filter(User.organization_id == org_id, User.is_active.isnot(False)).all()
    if not users:
        return {"status": "not_sent", "label": "Invitation not sent", "users": 0}
    if any(u.last_login_at is not None for u in users):
        return {"status": "active", "label": "Client login active", "users": len(users)}
    rows = [SA.latest_for_user(db, u.id) for u in users]
    if any(r is not None and r.accepted_at is not None for r in rows):
        return {"status": "accepted", "label": "Invitation accepted - not signed in yet", "users": len(users)}
    if any(r is not None and r.is_usable() for r in rows):
        return {"status": "sent", "label": "Invitation link issued - not accepted", "users": len(users)}
    return {"status": "expired", "label": "Invitation expired - resend", "users": len(users)}


def directory(db: Session) -> Dict[str, Any]:
    from app.services.acquisition import hub as HUB
    orgs = E.enabled_orgs(db)
    ids = [o.id for o in orgs]
    stats = HUB.client_stats(db, ids)
    profiles = {p.organization_id: p for p in db.query(AcquisitionProfile)
                .filter(AcquisitionProfile.organization_id.in_(ids)).all()} if ids else {}
    items = []
    for o in orgs:
        p = profiles.get(o.id)
        r = E.readiness(o, p)
        done = sum(1 for x in r["required"] if x["ok"])
        inv = _invitation_state(db, o.id)
        territory = (C.jload(p.service_area, []) if p else []) + (C.jload(getattr(p, "service_zips", None), []) if p else [])
        s = stats.get(o.id, {})
        if p is None:
            stage = "workspace_created"
        elif p.pilot_started_at:
            stage = "pilot_active"
        elif r["launch_ready"]:
            stage = "outreach_ready"
        elif territory and C.jload(p.segments, []):
            stage = "ready_for_assignment"
        else:
            stage = "onboarding_incomplete"
        items.append({"organization_id": o.id, "name": o.name, "is_test": HUB.is_test_org(o.name), "created_at": C.iso(o.created_at),
                      "contact": (p.owner_name if p else None), "contact_email": (p.contact_email if p else None),
                      "onboarding": {"done": done, "total": len(r["required"]), "missing": r["missing"]},
                      "status": stage, "invitation": inv, "paused": bool(p.paused) if p else True,
                      "pilot_started_at": C.iso(p.pilot_started_at) if p else None,
                      "territory": territory[:8], "territory_confirmed": bool(territory),
                      "segments": C.jload(p.segments, []) if p else [],
                      "offer_label": C.OFFER_LABEL.get((getattr(p, "offer_type", None) or C.FREE_PILOT)),
                      "sales_opportunity_id": getattr(p, "sales_opportunity_id", None) if p else None,
                      **s})
    return {"items": items, "limit": C.PILOT_ORG_LIMIT}


def sales_candidates(db: Session) -> Dict[str, Any]:
    """Cleaning companies in EvoSys Sales the owner might convert. Read-only:
    nothing here moves or copies a Sales record."""
    from app.models.sales_models import BrandSalesOrg, Opportunity
    plat = db.query(Platform).filter(Platform.slug == PLATFORM_SLUG).first()
    if plat is None:
        return {"items": []}
    bso_ids = [b.id for b in db.query(BrandSalesOrg).filter(BrandSalesOrg.platform_id == plat.id).all()]
    if not bso_ids:
        return {"items": []}
    rows = (db.query(Opportunity).filter(Opportunity.brand_sales_org_id.in_(bso_ids),
                                         func.lower(func.coalesce(Opportunity.industry, "")).like("%clean%"))
            .order_by(Opportunity.created_at.desc()).limit(300).all())
    orgs = {o.id: o.name for o in db.query(Organization).filter(
        Organization.id.in_([r.customer_organization_id for r in rows if r.customer_organization_id])).all()} \
        if any(r.customer_organization_id for r in rows) else {}
    items = []
    for r in rows:
        if r.status == "lost":
            st = "lost"
        elif r.customer_organization_id:
            st = "workspace_created"
        elif r.stage in ("prospect",):
            st = "prospect"
        else:
            st = "interested"
        items.append({"id": r.id, "company": r.company_name, "contact": r.contact_name, "email": r.email,
                      "phone": r.phone, "website": r.website, "source": r.source, "stage": r.stage,
                      "status": st, "workspace": orgs.get(r.customer_organization_id),
                      "organization_id": r.customer_organization_id})
    return {"items": items}
