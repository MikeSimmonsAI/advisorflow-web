"""Managed client acquisition - the operations behind both screens.

Nothing here sends a message, calls a data provider or calls an AI model.
Those are P2-P4 workers, and each of them will check `profile.paused`
first. Every write records an AcquisitionEvent so the journey is auditable.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.acquisition_models import (AcquisitionEvent, AcquisitionEvidence, AcquisitionFeedback,
                                           AcquisitionOpportunity, AcquisitionProfile)
from app.models.models import BookingLink, Lead, Organization, User
from app.services.acquisition import common as C
from app.services.acquisition import harvey_adapted as H

CLIENT_FEATURES = ("leads", "booking", "calendar", "availability", "reports", "users", C.FEATURE)


# ── setup ──────────────────────────────────────────────────────────────────
def get_profile(db: Session, org_id: str) -> Optional[AcquisitionProfile]:
    return db.query(AcquisitionProfile).filter(AcquisitionProfile.organization_id == org_id).first()


def enabled_orgs(db: Session) -> List[Organization]:
    """Organizations that list the feature explicitly (checked in Python - the
    allow-list is JSON text)."""
    rows = (db.query(Organization).filter(Organization.enabled_features.isnot(None),
                                          Organization.enabled_features.like('%' + C.FEATURE + '%')).all())
    return [o for o in rows if C.explicitly_enabled(o)]


def enable(db: Session, org: Organization, actor: User) -> dict:
    """Platform owner only: make this organization THE pilot client.
    Idempotent. Refuses a second organization (PILOT_ORG_LIMIT) and refuses a
    business whose recorded industry is something other than cleaning."""
    others = [o for o in enabled_orgs(db) if o.id != org.id]
    if len(others) >= C.PILOT_ORG_LIMIT:
        raise HTTPException(409, "The acquisition pilot is already running for %s. It is limited to "
                                 "one company until the pilot is evaluated." % others[0].name)
    if not C.identity_ok(org):
        raise HTTPException(409, "This workspace is recorded as a %s business. The acquisition pilot "
                                 "is for a cleaning company." % (org.industry or "different"))
    keys = C.jload(org.enabled_features, None)
    keys = list(keys) if isinstance(keys, list) else []
    added = [k for k in CLIENT_FEATURES if k not in keys]
    org.enabled_features = C.jdump(keys + added)
    if (org.industry or "").strip().lower() in ("", "generic", "other", "none", "unknown"):
        org.industry = C.IDENTITY
    p = get_profile(db, org.id)
    created = p is None
    if created:
        p = AcquisitionProfile(organization_id=org.id, service_area=C.jdump(C.DEFAULT_SERVICE_AREA),
                               segments=C.jdump(C.SEGMENT_KEYS), services=C.jdump([]),
                               exclusions=C.jdump([]), appointment_minutes=C.DEFAULT_APPOINTMENT_MINUTES,
                               paused=True, paused_reason="Not launched - development and setup",
                               paused_at=datetime.utcnow(), paused_by_id=actor.id)
        db.add(p)
        db.flush()
    _event(db, org.id, None, "setup", actor.id, note="Acquisition program enabled" if created else "Setup re-run")
    db.commit()
    return {"organization_id": org.id, "features_added": added, "profile_created": created,
            "paused": bool(p.paused)}


# ── serialization ──────────────────────────────────────────────────────────
def profile_json(org: Organization, p: Optional[AcquisitionProfile], full: bool) -> Dict[str, Any]:
    out = {
        "business_name": (p.business_name if p else None) or org.name,
        "owner_name": p.owner_name if p else None,
        "contact_phone": p.contact_phone if p else None,
        "contact_email": p.contact_email if p else None,
        "service_area": C.jload(p.service_area, []) if p else [],
        "segments": C.jload(p.segments, []) if p else [],
        "services": C.jload(p.services, []) if p else [],
        "min_job_value": p.min_job_value if p else None,
        "insurance_note": p.insurance_note if p else None,
        "certifications_note": p.certifications_note if p else None,
        "availability_note": p.availability_note if p else None,
        "appointment_minutes": (p.appointment_minutes if p else None) or C.DEFAULT_APPOINTMENT_MINUTES,
        "configured": p is not None,
    }
    if full:
        out.update({
            "exclusions": C.jload(p.exclusions, []) if p else [],
            "notify_user_ids": C.jload(p.notify_user_ids, []) if p else [],
            "paused": bool(p.paused) if p else True,
            "paused_reason": p.paused_reason if p else None,
            "paused_at": C.iso(p.paused_at) if p else None,
            "pilot_started_at": C.iso(p.pilot_started_at) if p else None,
        })
    return out


def _lead_facts(lead: Lead) -> Dict[str, Any]:
    cf = C.jload(lead.custom_fields, {})
    return cf if isinstance(cf, dict) else {}


def opp_json(opp: AcquisitionOpportunity, lead: Lead, booking: Optional[BookingLink],
             full: bool, evidence: Optional[List[AcquisitionEvidence]] = None) -> Dict[str, Any]:
    f = _lead_facts(lead)
    contact = " ".join(p for p in (lead.first_name, lead.last_name) if p).strip() or None
    out = {
        "id": opp.id,
        "lead_id": lead.id,
        "business_name": f.get("company") or contact or "Business",
        "segment": opp.segment,
        "segment_label": C.SEGMENT_LABEL.get(opp.segment or "", None),
        "contact_name": contact,
        "contact_title": f.get("contact_title"),
        "email": lead.email,
        "phone": lead.phone,
        "website": f.get("website"),
        "address": ", ".join(p for p in (lead.street_address, lead.city,
                                         " ".join(x for x in (lead.state, lead.zip_code) if x)) if p) or None,
        "city": lead.city,
        "stage": opp.stage,
        "stage_label": C.STAGE_LABEL.get(opp.stage, opp.stage),
        "stage_entered_at": C.iso(opp.stage_entered_at),
        "interest_note": f.get("interest_note"),
        "appointment": ({"id": booking.id, "status": booking.status, "time": C.iso(booking.booked_time),
                         "label": booking.appt_label, "minutes": booking.appt_duration} if booking else None),
        "quote_amount": opp.quote_amount,
        "monthly_value": opp.monthly_value,
        "lost_reason": opp.lost_reason,
        "outcome_note": opp.outcome_note,
    }
    if full:
        out.update({
            "domain": opp.domain, "source": opp.source, "source_url": opp.source_url,
            "is_practice": bool(opp.is_practice), "score": opp.score,
            "score_reasons": C.jload(opp.score_reasons, []),
            "disqualified_reason": opp.disqualified_reason, "created_at": C.iso(opp.created_at),
            "evidence": [{"id": e.id, "kind": e.kind, "value": e.value, "source_url": e.source_url,
                          "confidence": e.confidence, "verified": bool(e.verified),
                          "recorded_at": C.iso(e.created_at)} for e in (evidence or [])],
        })
    return out


# ── queries ────────────────────────────────────────────────────────────────
def _rows(db: Session, org_id: str, stages=None, include_practice: bool = True, lead_ids=None):
    q = (db.query(AcquisitionOpportunity, Lead)
         .join(Lead, Lead.id == AcquisitionOpportunity.lead_id)
         .filter(AcquisitionOpportunity.organization_id == org_id, Lead.organization_id == org_id))
    if stages:
        q = q.filter(AcquisitionOpportunity.stage.in_(list(stages)))
    if not include_practice:
        q = q.filter(AcquisitionOpportunity.is_practice == False)  # noqa: E712
    if lead_ids is not None:
        q = q.filter(Lead.id.in_(lead_ids))
    return q.order_by(AcquisitionOpportunity.updated_at.desc()).all()


def _bookings(db: Session, ids) -> Dict[str, BookingLink]:
    ids = [i for i in ids if i]
    if not ids:
        return {}
    return {b.id: b for b in db.query(BookingLink).filter(BookingLink.id.in_(ids)).all()}


def list_client(db: Session, org_id: str, lead_ids=None) -> List[Dict[str, Any]]:
    """The client's view: real (never practice) prospects that showed interest."""
    rows = _rows(db, org_id, C.CLIENT_STAGES, include_practice=False, lead_ids=lead_ids)
    bk = _bookings(db, [o.booking_link_id for o, _ in rows])
    return [opp_json(o, l, bk.get(o.booking_link_id), full=False) for o, l in rows]


def list_owner(db: Session, org_id: str, stage: Optional[str] = None,
               practice: Optional[bool] = None) -> List[Dict[str, Any]]:
    rows = _rows(db, org_id, [stage] if stage else None)
    if practice is not None:
        rows = [(o, l) for o, l in rows if bool(o.is_practice) == practice]
    bk = _bookings(db, [o.booking_link_id for o, _ in rows])
    ev: Dict[str, List[AcquisitionEvidence]] = {}
    ids = [o.id for o, _ in rows]
    if ids:
        for e in (db.query(AcquisitionEvidence).filter(AcquisitionEvidence.organization_id == org_id,
                                                       AcquisitionEvidence.opportunity_id.in_(ids))
                  .order_by(AcquisitionEvidence.created_at.asc()).all()):
            ev.setdefault(e.opportunity_id, []).append(e)
    return [opp_json(o, l, bk.get(o.booking_link_id), full=True, evidence=ev.get(o.id, [])) for o, l in rows]


def load_opp(db: Session, org_id: str, opp_id: str, client: bool = False) -> AcquisitionOpportunity:
    opp = (db.query(AcquisitionOpportunity)
           .filter(AcquisitionOpportunity.id == opp_id, AcquisitionOpportunity.organization_id == org_id).first())
    if opp is None or (client and (opp.is_practice or opp.stage not in C.CLIENT_STAGES)):
        raise HTTPException(404, "Prospect not found")
    return opp


# ── writes ─────────────────────────────────────────────────────────────────
def _event(db, org_id, opp_id, kind, actor_id, from_stage=None, to_stage=None, note=None):
    db.add(AcquisitionEvent(organization_id=org_id, opportunity_id=opp_id, kind=kind, from_stage=from_stage,
                            to_stage=to_stage, actor_id=actor_id, note=(note or None) and str(note)[:500]))


def _move(db, opp: AcquisitionOpportunity, to_stage: str, actor_id: Optional[str], note: Optional[str] = None):
    if to_stage not in C.STAGE_KEYS:
        raise HTTPException(422, "Unknown stage")
    if opp.stage == to_stage:
        return
    now = datetime.utcnow()
    _event(db, opp.organization_id, opp.id, "stage", actor_id, opp.stage, to_stage, note)
    opp.stage = to_stage
    opp.stage_entered_at = now
    stamp = {C.INTERESTED: "interested_at", C.BOOKED: "booked_at", C.HELD: "held_at",
             C.QUOTED: "quoted_at", C.WON: "closed_at", C.LOST: "closed_at"}.get(to_stage)
    if stamp and getattr(opp, stamp) is None:
        setattr(opp, stamp, now)


def add_prospect(db: Session, org: Organization, actor: User, data: Dict[str, Any]) -> AcquisitionOpportunity:
    """Owner adds one business with its evidence (the controlled test cohort,
    and the hand-entry path). Dedupe + exclusions are deterministic."""
    name = (data.get("business_name") or "").strip()[:200]
    if not name:
        raise HTTPException(422, "Business name is required")
    segment = data.get("segment") or None
    if segment and segment not in C.SEGMENT_KEYS:
        raise HTTPException(422, "Unknown segment")
    website = (data.get("website") or "").strip()[:300] or None
    domain = H.normalize_domain(website) if website else ""
    junk = H.is_junk(name, domain)
    if junk:
        raise HTTPException(422, "Not added: %s" % junk)
    key = H.dedupe_key(name, domain, data.get("phone") or "")
    if not key:
        raise HTTPException(422, "A website or business name is required")
    dup = (db.query(AcquisitionOpportunity)
           .filter(AcquisitionOpportunity.organization_id == org.id, AcquisitionOpportunity.dedupe_key == key).first())
    if dup is not None:
        raise HTTPException(409, "This business is already in the pipeline")
    p = get_profile(db, org.id)
    excl = C.jload(p.exclusions, []) if p else []
    email = (data.get("email") or "").strip().lower()[:200] or None
    hit = (H.do_not_contact_domain_match(domain, excl) if domain else "") or \
          (H.do_not_contact_match(email, excl) if email else "")
    if hit:
        raise HTTPException(409, "Not added: matches the client's exclusion list (%s)" % hit)
    practice = bool(data.get("is_practice", True))
    facts = {"company": name, "website": website, "segment": segment,
             "contact_title": (data.get("contact_title") or "").strip()[:120] or None}
    lead = Lead(organization_id=org.id, first_name=(data.get("contact_first_name") or "").strip()[:80] or None,
                last_name=(data.get("contact_last_name") or "").strip()[:80] or None, email=email,
                phone=(data.get("phone") or "").strip()[:40] or None,
                street_address=(data.get("street_address") or "").strip()[:200] or None,
                city=(data.get("city") or "").strip()[:80] or None, state=(data.get("state") or "TX")[:20],
                zip_code=(data.get("zip_code") or "").strip()[:20] or None,
                source=C.PRACTICE_SOURCE if practice else C.LEAD_SOURCE, contact_channel="email_only",
                custom_fields=C.jdump({k: v for k, v in facts.items() if v}))
    db.add(lead)
    db.flush()
    now = datetime.utcnow()
    opp = AcquisitionOpportunity(organization_id=org.id, lead_id=lead.id, stage=C.DISCOVERED, stage_entered_at=now,
                                 segment=segment, dedupe_key=key, domain=domain or None,
                                 source=(data.get("source") or "manual")[:60],
                                 source_url=(data.get("source_url") or "").strip()[:500] or None,
                                 is_practice=practice)
    db.add(opp)
    db.flush()
    _event(db, org.id, opp.id, "stage", actor.id, None, C.DISCOVERED,
           "Added by hand" + (" (practice cohort)" if practice else ""))
    if opp.source_url:
        add_evidence(db, org.id, opp, actor, {"kind": "listing", "value": name, "source_url": opp.source_url,
                                              "confidence": data.get("confidence")}, commit=False)
    db.commit()
    return opp


def add_evidence(db: Session, org_id: str, opp: AcquisitionOpportunity, actor: Optional[User],
                 data: Dict[str, Any], commit: bool = True) -> AcquisitionEvidence:
    kind = data.get("kind")
    if kind not in C.EVIDENCE_KINDS:
        raise HTTPException(422, "Unknown evidence kind")
    conf = data.get("confidence")
    if conf not in (None, ""):
        try:
            conf = float(conf)
        except (TypeError, ValueError):
            raise HTTPException(422, "Confidence must be between 0 and 1")
        if not 0 <= conf <= 1:
            raise HTTPException(422, "Confidence must be between 0 and 1")
    else:
        conf = None
    url = (data.get("source_url") or "").strip()[:500] or None
    if url and not url.lower().startswith(("http://", "https://")):
        raise HTTPException(422, "Source must be a web address")
    e = AcquisitionEvidence(organization_id=org_id, opportunity_id=opp.id, kind=kind,
                            value=(str(data.get("value") or "")[:2000]) or None, source_url=url, confidence=conf,
                            verified=bool(data.get("verified")) and bool(url),
                            recorded_by_id=actor.id if actor else None)
    db.add(e)
    if commit:
        db.commit()
    return e


def owner_move(db: Session, opp: AcquisitionOpportunity, actor: User, data: Dict[str, Any]) -> None:
    to = data.get("stage")
    if to == C.DISQUALIFIED:
        reason = (data.get("reason") or "").strip()[:300]
        if not reason:
            raise HTTPException(422, "Say why this business is not a fit")
        opp.disqualified_reason = reason
    if to in (C.CONTACTED, C.REPLIED):
        # Those stages are set by the outreach/reply workers from real message
        # records (P3) - never by hand, so "contacted" is always evidence-backed.
        raise HTTPException(409, "Contacted and Replied are recorded from real messages, not set by hand")
    if to in C.CLIENT_STAGES and opp.is_practice:
        raise HTTPException(409, "Practice prospects cannot reach the client's stages")
    if to == C.INTERESTED:
        note = (data.get("note") or "").strip()[:500]
        if not note:
            raise HTTPException(422, "Record what they said that shows interest")
        lead = db.query(Lead).filter(Lead.id == opp.lead_id, Lead.organization_id == opp.organization_id).first()
        f = _lead_facts(lead)
        f["interest_note"] = note
        lead.custom_fields = C.jdump(f)
    _move(db, opp, to, actor.id, data.get("note"))
    db.commit()


def client_outcome(db: Session, opp: AcquisitionOpportunity, actor: User, data: Dict[str, Any]) -> None:
    to = data.get("outcome")
    allowed_from = C.CLIENT_OUTCOMES.get(to)
    if allowed_from is None:
        raise HTTPException(422, "Unknown outcome")
    if opp.stage not in allowed_from:
        raise HTTPException(409, "%s can't be recorded from %s" % (C.STAGE_LABEL[to], C.STAGE_LABEL[opp.stage]))
    if to == C.QUOTED:
        opp.quote_amount = C.money(data.get("quote_amount"))
        opp.monthly_value = C.money(data.get("monthly_value")) if data.get("monthly_value") not in (None, "") else opp.monthly_value
        if opp.quote_amount is None and opp.monthly_value is None:
            raise HTTPException(422, "Enter the quote amount or the monthly value")
    if to == C.WON:
        mv = C.money(data.get("monthly_value"))
        if mv is not None:
            opp.monthly_value = mv
    if to == C.LOST:
        opp.lost_reason = (data.get("reason") or "").strip()[:300] or None
    note = (data.get("note") or "").strip()[:2000]
    if note:
        opp.outcome_note = note
    _move(db, opp, to, actor.id, note or None)
    db.commit()


def set_paused(db: Session, org_id: str, actor: User, paused: bool, reason: Optional[str]) -> AcquisitionProfile:
    p = get_profile(db, org_id)
    if p is None:
        raise HTTPException(409, "Run setup first")
    if paused:
        r = (reason or "").strip()[:300]
        if not r:
            raise HTTPException(422, "Say why it is being paused")
        p.paused, p.paused_reason, p.paused_at, p.paused_by_id = True, r, datetime.utcnow(), actor.id
    else:
        p.paused, p.paused_reason = False, None
    _event(db, org_id, None, "pause" if paused else "resume", actor.id, note=reason)
    db.commit()
    return p


def update_profile(db: Session, org_id: str, data: Dict[str, Any], owner: bool) -> AcquisitionProfile:
    """The client edits her business facts; the platform owner also edits the
    exclusion list and alert recipients."""
    p = get_profile(db, org_id)
    if p is None:
        raise HTTPException(409, "This program has not been set up yet")
    text = {"business_name": 200, "owner_name": 120, "contact_phone": 40, "contact_email": 200,
            "insurance_note": 1000, "certifications_note": 1000, "availability_note": 1000}
    for k, w in text.items():
        if k in data:
            setattr(p, k, (str(data[k] or "").strip()[:w]) or None)
    if "service_area" in data:
        p.service_area = C.jdump(C.clean_list(data["service_area"], limit=60))
    if "segments" in data:
        p.segments = C.jdump(C.clean_list(data["segments"], allowed=C.SEGMENT_KEYS))
    if "services" in data:
        p.services = C.jdump(C.clean_list(data["services"], limit=30, width=120))
    if "min_job_value" in data:
        p.min_job_value = C.money(data["min_job_value"])
    if "appointment_minutes" in data:
        try:
            m = int(data["appointment_minutes"])
        except (TypeError, ValueError):
            raise HTTPException(422, "Appointment length must be minutes")
        if m not in (15, 30, 45, 60, 90, 120):
            raise HTTPException(422, "Appointment length must be 15, 30, 45, 60, 90 or 120 minutes")
        p.appointment_minutes = m
    if owner and "exclusions" in data:
        raw = data["exclusions"]
        p.exclusions = C.jdump(H.split_entries(" ".join(raw) if isinstance(raw, list) else raw))
    if owner and "notify_user_ids" in data:
        ids = C.clean_list(data["notify_user_ids"], limit=10, width=64)
        valid = {u.id for u in db.query(User).filter(User.organization_id == org_id, User.id.in_(ids)).all()} if ids else set()
        p.notify_user_ids = C.jdump([i for i in ids if i in valid])
    db.commit()
    return p


def add_feedback(db: Session, org_id: str, user: User, data: Dict[str, Any]) -> AcquisitionFeedback:
    body = (data.get("body") or "").strip()[:4000]
    if not body:
        raise HTTPException(422, "Write a short note")
    rating = data.get("rating") or "general"
    if rating not in ("good_fit", "poor_fit", "general"):
        raise HTTPException(422, "Unknown rating")
    opp_id = data.get("opportunity_id") or None
    if opp_id:
        load_opp(db, org_id, opp_id, client=True)
    fb = AcquisitionFeedback(organization_id=org_id, user_id=user.id, opportunity_id=opp_id, rating=rating, body=body)
    db.add(fb)
    _event(db, org_id, opp_id, "feedback", user.id, note=rating)
    db.commit()
    return fb


def feedback_list(db: Session, org_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    rows = (db.query(AcquisitionFeedback, User).outerjoin(User, User.id == AcquisitionFeedback.user_id)
            .filter(AcquisitionFeedback.organization_id == org_id)
            .order_by(AcquisitionFeedback.created_at.desc()).limit(limit).all())
    return [{"id": f.id, "rating": f.rating, "body": f.body, "opportunity_id": f.opportunity_id,
             "by": (u.full_name or u.email) if u else None, "at": C.iso(f.created_at)} for f, u in rows]


def events(db: Session, org_id: str, limit: int = 60) -> List[Dict[str, Any]]:
    rows = (db.query(AcquisitionEvent).filter(AcquisitionEvent.organization_id == org_id)
            .order_by(AcquisitionEvent.created_at.desc()).limit(limit).all())
    return [{"kind": e.kind, "from": e.from_stage, "to": e.to_stage, "note": e.note,
             "opportunity_id": e.opportunity_id, "at": C.iso(e.created_at)} for e in rows]


# ── truthful counts ────────────────────────────────────────────────────────
def funnel(db: Session, org_id: str, include_practice: bool) -> Dict[str, Any]:
    rows = (db.query(AcquisitionOpportunity)
            .filter(AcquisitionOpportunity.organization_id == org_id).all())
    real = [o for o in rows if not o.is_practice]
    use = rows if include_practice else real
    by_stage = {k: 0 for k in C.STAGE_KEYS}
    for o in use:
        by_stage[o.stage] = by_stage.get(o.stage, 0) + 1

    def reached(stamp):
        return sum(1 for o in real if getattr(o, stamp) is not None)

    won = [o for o in real if o.stage == C.WON]
    quoted = [o for o in real if o.quoted_at is not None]
    return {
        "by_stage": by_stage,
        "total": len(use),
        "practice": len(rows) - len(real),
        # outcome counts are ALWAYS real prospects only
        "interested": reached("interested_at"),
        "booked": reached("booked_at"),
        "held": reached("held_at"),
        "no_show": sum(1 for o in real if o.stage == C.NO_SHOW),
        "quoted": len(quoted),
        "won": len(won),
        "lost": sum(1 for o in real if o.stage == C.LOST),
        "won_monthly_value": round(sum(o.monthly_value or 0 for o in won), 2),
        "quoted_value": round(sum((o.quote_amount or 0) for o in quoted), 2),
    }


def upcoming(db: Session, org_id: str, lead_ids=None) -> List[Dict[str, Any]]:
    now = datetime.utcnow()
    items = [x for x in list_client(db, org_id, lead_ids)
             if x["stage"] == C.BOOKED and x["appointment"] and x["appointment"]["time"]]
    items = [x for x in items if x["appointment"]["time"] >= C.iso(now)]
    return sorted(items, key=lambda x: x["appointment"]["time"])
