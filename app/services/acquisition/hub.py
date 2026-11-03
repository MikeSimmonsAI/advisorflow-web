"""Acquisition Hub (#452): the owner's platform-wide numbers, contact research
and client provisioning - read from the records that already exist.

EMAIL READINESS (this cleaning workflow is email-only):
    no verified, usable email  =  NOT EMAIL READY
A business phone number is never treated as email-ready, whatever the shared
qualification engine's channel bucket says (it scores SMS/voice too).

Nothing here sends, assigns, imports or starts a pilot.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.acquisition_models import (AcquisitionDiscoveryRun, AcquisitionOpportunity,
                                           AcquisitionProfile)
from app.models.lead_intel_models import LeadIntelProspect
from app.models.models import Organization, User
from app.services.acquisition import common as C
from app.services.acquisition import engine as E
from app.services.acquisition import geo as G

QUEUES = [
    ("needs_research", "Needs research"),
    ("needs_email", "Needs email"),
    ("verification_pending", "Email verification pending"),
    ("ready_for_review", "Verified, ready to assign"),
    ("verified_contact", "Verified contact (assigned)"),
    ("not_eligible", "Not eligible"),
]
QUEUE_LABEL = dict(QUEUES)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def readiness(p: LeadIntelProspect) -> Dict[str, Any]:
    """Why this business can or can't be emailed yet, in plain words."""
    reasons: List[str] = []
    geo, _ = G.status(p.state)
    eligible = p.stage == "qualified" and geo != G.EXCLUDED and not p.suppressed_reason
    if p.stage != "qualified":
        reasons.append("Not qualified (%s)" % p.stage)
    if geo == G.EXCLUDED:
        reasons.append("Outside the supported territory")
    elif geo == G.REVIEW:
        reasons.append("Location not confirmed")
    if p.suppressed_reason:
        reasons.append("Suppressed: %s" % p.suppressed_reason)
    has_source = bool(getattr(p, "source_url", None))
    if not has_source:
        reasons.append("No source link recorded")
    email = (p.email or "").strip()
    if not email:
        status = "none"
        reasons.append("No published email found yet")
    elif not _EMAIL.match(email):
        status = "invalid"
        reasons.append("Email isn't a valid address")
    elif getattr(p, "email_verified", None) is True:
        status = "verified"
    else:
        status = "unverified"
        reasons.append("Email not verified at its source")
    ready = eligible and status == "verified" and geo == G.ELIGIBLE
    if not eligible:
        queue = "not_eligible"
    elif not has_source and status == "none":
        queue = "needs_research"
    elif status in ("none", "invalid"):
        queue = "needs_email"
    elif status == "unverified":
        queue = "verification_pending"
    elif p.routed_org_id:
        queue = "verified_contact"
    else:
        queue = "ready_for_review"
    return {"email_status": status, "email_ready": ready, "queue": queue, "queue_label": QUEUE_LABEL[queue],
            "not_ready_reasons": [] if ready else reasons}


def _research_log(p: LeadIntelProspect) -> List[Dict[str, Any]]:
    try:
        ev = json.loads(p.evidence_json) if p.evidence_json else {}
    except ValueError:
        ev = {}
    log = ev.get("research_log") if isinstance(ev, dict) else None
    return log if isinstance(log, list) else []


def record_contact(db: Session, p: LeadIntelProspect, actor: User, data: Dict[str, Any]) -> Dict[str, Any]:
    """An operator records a contact detail ACTUALLY PUBLISHED somewhere, with
    the page it came from. Verified only when the operator says they checked
    it at that source. Nothing is guessed, generated or sent."""
    email = (data.get("email") or "").strip().lower()
    src = (data.get("source_url") or "").strip()
    if not src.lower().startswith(("http://", "https://")):
        raise HTTPException(422, "Give the web page where this contact is published")
    if email and not _EMAIL.match(email):
        raise HTTPException(422, "That isn't a valid email address")
    name = (data.get("contact_name") or "").strip()[:120] or None
    title = (data.get("contact_title") or "").strip()[:120] or None
    if not (email or name):
        raise HTTPException(422, "Record an email or a contact name")
    verified = bool(data.get("verified")) and bool(email)
    if email:
        p.email, p.email_verified = email, verified
    if name:
        p.contact_name = name
    if title:
        p.contact_title = title
    try:
        ev = json.loads(p.evidence_json) if p.evidence_json else {}
    except ValueError:
        ev = {}
    if not isinstance(ev, dict):
        ev = {}
    entry = {"at": datetime.utcnow().isoformat(), "by": actor.full_name or actor.email, "email": email or None,
             "contact_name": name, "contact_title": title, "source_url": src, "verified": verified,
             "note": (data.get("note") or "").strip()[:300] or None}
    ev.setdefault("research_log", []).append(entry)
    p.evidence_json = json.dumps(ev, default=str)
    # Already with a client: the client's copy gets the same sourced fact.
    pushed = False
    if p.routed_org_id and email:
        opp = (db.query(AcquisitionOpportunity)
               .filter(AcquisitionOpportunity.organization_id == p.routed_org_id,
                       AcquisitionOpportunity.source == "master_pool:" + p.id).first())
        if opp is not None:
            E.add_evidence(db, p.routed_org_id, opp, actor, {"kind": "contact_email", "value": email,
                                                            "source_url": src, "verified": verified,
                                                            "confidence": 0.9 if verified else 0.5}, commit=False)
            pushed = True
    db.commit()
    return {"prospect_id": p.id, "readiness": readiness(p), "pushed_to_client": pushed, "sent": 0}


def _money(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def client_stats(db: Session, org_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {oid: {"assigned": 0, "real": 0, "contacts_verified": 0, "contacted": 0,
                                            "replied": 0, "interested": 0, "booked": 0, "won": 0,
                                            "monthly_value_won": 0.0, "practice": 0} for oid in org_ids}
    if not org_ids:
        return out
    from app.models.acquisition_models import AcquisitionEvidence
    rows = (db.query(AcquisitionOpportunity)
            .filter(AcquisitionOpportunity.organization_id.in_(org_ids)).all())
    verified = {r[0] for r in db.query(AcquisitionEvidence.opportunity_id)
                .filter(AcquisitionEvidence.organization_id.in_(org_ids),
                        AcquisitionEvidence.kind == "contact_email", AcquisitionEvidence.verified.is_(True)).all()}
    for o in rows:
        s = out[o.organization_id]
        if o.is_practice:
            s["practice"] += 1
            continue
        s["real"] += 1
        if (o.source or "").startswith("master_pool:") and o.stage != C.DISQUALIFIED:
            s["assigned"] += 1
        rank = C.RANK.get(o.stage, 0)
        if o.id in verified:
            s["contacts_verified"] += 1
        if rank >= C.RANK[C.CONTACTED] and o.stage != C.DISQUALIFIED:
            s["contacted"] += 1
        if rank >= C.RANK[C.REPLIED] and o.stage != C.DISQUALIFIED:
            s["replied"] += 1
        if o.interested_at is not None:
            s["interested"] += 1
        if o.booked_at is not None:
            s["booked"] += 1
        if o.stage == C.WON:
            s["won"] += 1
            s["monthly_value_won"] += _money(o.monthly_value)
    return out


def overview(db: Session) -> Dict[str, Any]:
    """Platform-wide totals. Practice records never count as results."""
    pool = db.query(LeadIntelProspect).all()
    ready = [readiness(p) for p in pool]
    queues = {k: 0 for k, _ in QUEUES}
    for r in ready:
        queues[r["queue"]] += 1
    qualified = [p for p in pool if p.stage == "qualified"]
    assigned = [p for p in pool if p.routed_org_id]
    clients = E.enabled_orgs(db)
    ids = [o.id for o in clients]
    stats = client_stats(db, ids)
    profiles = {p.organization_id: p for p in db.query(AcquisitionProfile)
                .filter(AcquisitionProfile.organization_id.in_(ids)).all()} if ids else {}
    tot = {k: sum(s[k] for s in stats.values()) for k in ("contacted", "replied", "interested", "booked", "won")}
    mrr = round(sum(s["monthly_value_won"] for s in stats.values()), 2)
    runs = db.query(AcquisitionDiscoveryRun).all()
    spend = round(sum(r.cost_usd or 0 for r in runs), 4)
    calls = sum((r.search_requests or 0) + (r.detail_requests or 0) for r in runs)
    email_ready = sum(1 for r in ready if r["email_ready"])
    from app.models.import_models import ImportBatch
    batches = (db.query(ImportBatch).filter(ImportBatch.pipeline == "platform_pool")
               .order_by(ImportBatch.created_at.desc()).limit(6).all())
    recent_assign = sorted([p for p in assigned if p.routed_at], key=lambda p: p.routed_at, reverse=True)[:8]
    names = {o.id: o.name for o in clients}
    return {
        "generated_at": datetime.utcnow().isoformat(),
        "pool": {"total": len(pool), "qualified": len(qualified), "available": len([p for p in qualified if not p.routed_org_id]),
                 "assigned": len(assigned), "email_verified": sum(1 for r in ready if r["email_status"] == "verified"),
                 "email_ready": email_ready, "needs_email": queues["needs_email"] + queues["needs_research"],
                 "held_for_review": sum(1 for p in pool if G.status(p.state)[0] == G.REVIEW),
                 "duplicates": sum(1 for p in pool if p.stage == "duplicate")},
        "queues": [{"key": k, "label": l, "count": queues[k]} for k, l in QUEUES],
        "funnel": [
            {"key": "imported", "label": "Imported", "value": len(pool)},
            {"key": "qualified", "label": "Qualified", "value": len(qualified)},
            {"key": "assigned", "label": "Assigned", "value": len(assigned)},
            {"key": "email_ready", "label": "Email ready", "value": email_ready},
            {"key": "contacted", "label": "Contacted", "value": tot["contacted"]},
            {"key": "interested", "label": "Interested", "value": tot["interested"]},
            {"key": "booked", "label": "Booked", "value": tot["booked"]},
            {"key": "won", "label": "Won", "value": tot["won"]},
        ],
        "results": dict(tot, monthly_value_won=mrr),
        "spend": {"usd": spend, "provider_calls": calls, "source": "Metered Google Places discovery runs"},
        "clients": [{"organization_id": o.id, "name": o.name,
                     "paused": bool(profiles[o.id].paused) if o.id in profiles else True,
                     "pilot_started_at": C.iso(profiles[o.id].pilot_started_at) if o.id in profiles else None,
                     "service_area": C.jload(profiles[o.id].service_area, [])[:8] if o.id in profiles else [],
                     "service_states": C.jload(getattr(profiles.get(o.id), "service_states", None), []) if o.id in profiles else [],
                     "launch_ready": E.readiness(o, profiles.get(o.id))["launch_ready"],
                     "offer_label": C.OFFER_LABEL.get(getattr(profiles.get(o.id), "offer_type", None) or C.FREE_PILOT),
                     **stats[o.id]} for o in clients],
        "recent_imports": [{"id": b.id, "name": b.display_name, "rows": b.total_rows, "status": b.status,
                            "committed": b.committed_rows or 0, "duplicates": b.matched_rows or 0,
                            "invalid": b.invalid_rows or 0, "at": C.iso(b.created_at), "source": b.source_system}
                           for b in batches],
        "recent_assignments": [{"prospect_id": p.id, "name": p.name, "client": names.get(p.routed_org_id, "Another client"),
                                "at": C.iso(p.routed_at)} for p in recent_assign],
    }


def provision(db: Session, actor: User, name: str) -> Dict[str, Any]:
    """Add a cleaning company: a new organization on the EvoSys Pro platform
    with the acquisition template switched on. Starts PAUSED, no users, no
    onboarding facts, nothing sent."""
    import re as _re
    from app.models.models import Platform
    nm = (name or "").strip()[:150]
    if len(nm) < 2:
        raise HTTPException(422, "Give the cleaning company's name")
    if db.query(Organization).filter(Organization.name == nm).first() is not None:
        raise HTTPException(409, "A workspace with that name already exists")
    platform = db.query(Platform).filter(Platform.slug == "evosyspro").first()
    if platform is None:
        raise HTTPException(409, "The EvoSys Pro platform record is missing")
    slug = _re.sub(r"[^a-z0-9]+", "-", nm.lower()).strip("-")[:50] or "cleaning-client"
    base, n = slug, 1
    while db.query(Organization).filter(Organization.slug == slug).first() is not None:
        slug = "%s-%d" % (base, n)
        n += 1
    org = Organization(name=nm, slug=slug, plan="trial", platform_id=platform.id, is_active=True, industry="cleaning")
    db.add(org)
    db.commit()
    db.refresh(org)
    out = E.enable(db, org, actor)
    return {"organization_id": org.id, "name": org.name, "paused": out["paused"], "users_created": 0, "sent": 0}
