"""EvoSense Acquisition Engine — HTTP surface (Wholesale Phase 7).

Mounted under the Wholesale prefix and gated exactly like the rest of the
module:

    require_feature("wholesale_real_estate")   the customer bought Wholesale
    require_tenant_or_observer / _user          the caller is inside a customer
    require_not_observation (writes)            a real actor, not an observer

Additionally:
    * budgets, provider switches and RESUMING a paused switch need a workspace
      admin; PAUSING anything is open to every user (anyone may pull the brake)
    * promotion needs a signed-in person — there is no automation route to it
    * no response ever contains a provider credential (none are stored), and
      nothing here is reachable from the Investor Deal Room or Seller Portal,
      which are separate token-scoped routers that never import this module
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_not_observation, require_tenant_or_observer, require_tenant_user
from app.models.evosense_models import (EvoSenseContactPoint, EvoSenseEngagement, EvoSenseEvent,
                                        EvoSenseFeedback, EvoSenseHandoff, EvoSenseIdentityReview,
                                        EvoSenseObservation, EvoSenseOwner, EvoSensePerson,
                                        EvoSenseProperty, EvoSenseRun, EvoSenseStrategy)
from app.models.models import User
from app.services import wholesale_service as svc
from app.services.entitlements import require_feature
from app.services.evosense import common as C
from app.services.evosense import contacts as CT
from app.services.evosense import conversation as CV
from app.services.evosense import enrichment as EN
from app.services.evosense import evaluate as EV
from app.services.evosense import handoff as HO
from app.services.evosense import hunt as HU
from app.services.evosense import ingest as IN
from app.services.evosense import outreach as OU
from app.services.evosense import promotion as PR
from app.services.evosense import providers as PV
from app.services.evosense import signals as SIG
from app.services.evosense import strategy as ST
from app.services.evosense import views as V

FEATURE = "wholesale_real_estate"
router = APIRouter(prefix="/wholesale/evosense", tags=["wholesale-evosense"],
                   dependencies=[Depends(require_feature(FEATURE))])

ADMIN_ROLES = ("org_admin", "super_admin", "god_admin", "admin", "owner")


def _read_org(db, user) -> str:
    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=409, detail="No customer organization selected.")
    return org_id


def _admin(user):
    if getattr(user, "role", None) not in ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Only a workspace admin can change this.")


def _prop(db, org_id, property_id) -> EvoSenseProperty:
    p = (db.query(EvoSenseProperty)
         .filter(EvoSenseProperty.id == property_id, EvoSenseProperty.organization_id == org_id).first())
    if p is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return p


# ── Command Center / Inbox / Property ───────────────────────────────────────

@router.get("/command-center")
def command_center(hours: int = Query(24, ge=1, le=24 * 30), db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_or_observer)):
    return V.command_center(db, _read_org(db, user), hours=hours)


@router.get("/inbox")
def inbox(bucket: Optional[str] = None, strategy_id: Optional[str] = None, q: Optional[str] = None,
          signal: Optional[str] = None, sort: str = "opportunity",
          limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
          county: Optional[str] = None, min_score: Optional[int] = Query(None, ge=0, le=100),
          source: Optional[str] = None, archived: bool = False,
          db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    return V.inbox(db, _read_org(db, user), bucket=bucket, strategy_id=strategy_id, q=q,
                   signal=signal, sort=sort, limit=limit, offset=offset, county=county,
                   min_score=min_score, source=source, archived=archived)


@router.get("/properties/{property_id}")
def property_detail(property_id: str, db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_or_observer)):
    org_id = _read_org(db, user)
    return V.property_detail(db, org_id, _prop(db, org_id, property_id))


@router.get("/properties/{property_id}/observations/{observation_id}/raw")
def observation_raw(property_id: str, observation_id: str, db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_or_observer)):
    """The raw evidence behind one observation, exactly as the source gave it
    (never credentials — adapters never receive any)."""
    org_id = _read_org(db, user)
    prop = _prop(db, org_id, property_id)
    o = (db.query(EvoSenseObservation)
         .filter(EvoSenseObservation.id == observation_id, EvoSenseObservation.organization_id == org_id,
                 EvoSenseObservation.property_id == prop.id).first())
    if o is None:
        raise HTTPException(status_code=404, detail="Observation not found")
    return {"id": o.id, "provider": o.provider_key, "reference": o.source_reference,
            "retrieved_at": V._iso(o.observed_at), "source_updated_at": V._iso(o.source_updated_at),
            "source_url": o.source_url, "adapter_version": o.adapter_version,
            "content_hash": o.content_hash, "raw": o.raw_payload,
            "normalized": C.jload(o.payload, {}), "processing": o.processing_status,
            "error": o.processing_error}


class ManualProperty(BaseModel):
    street_address: str
    unit: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    county: Optional[str] = None
    parcel_apn: Optional[str] = None
    property_type: Optional[str] = None
    square_feet: Optional[int] = None
    estimated_value: Optional[float] = None
    mortgage_balance: Optional[float] = None
    last_sale_date: Optional[str] = None
    owner_name: Optional[str] = None
    mailing_street: Optional[str] = None
    mailing_city: Optional[str] = None
    mailing_state: Optional[str] = None
    mailing_zip: Optional[str] = None
    signals: List[str] = []
    strategy_id: Optional[str] = None
    is_test: bool = False


@router.post("/properties")
def add_property(payload: ManualProperty, db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_user),
                 _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    bad = [s for s in payload.signals if s not in SIG.CATALOG]
    if bad:
        raise HTTPException(status_code=422, detail="Unknown signal: %s" % ", ".join(bad))
    strategy = ST.get(db, org_id, payload.strategy_id) if payload.strategy_id else None
    try:
        out = HU.add_manual(db, org_id, payload.model_dump(), user=user, strategy=strategy)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    return out


@router.post("/import")
async def import_properties(file: UploadFile = File(...), strategy_id: Optional[str] = Form(None),
                            db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
                            _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    raw = await file.read()
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File is larger than 5 MB.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    strategy = ST.get(db, org_id, strategy_id) if strategy_id else None
    try:
        out = HU.import_csv(db, org_id, text, user=user, strategy=strategy,
                            filename=file.filename or "upload.csv")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    return out


class EnrichIn(BaseModel):
    approved: bool = False


@router.post("/properties/{property_id}/enrich")
def enrich(property_id: str, payload: EnrichIn, db: Session = Depends(get_db),
           user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    prop = _prop(db, org_id, property_id)
    out = EN.run(db, prop, EV.strategy_for(db, prop), user=user, approved=payload.approved)
    db.commit()
    return out


@router.post("/properties/{property_id}/outreach")
def outreach(property_id: str, db: Session = Depends(get_db),
             user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    prop = _prop(db, org_id, property_id)
    out = OU.start(db, prop, EV.strategy_for(db, prop), user=user, actor_type=C.ACTOR_USER)
    db.commit()
    return out


class ReplyIn(BaseModel):
    text: str
    delivery: str = "manual_entry"      # manual_entry | sandbox_simulated


@router.post("/properties/{property_id}/reply")
def record_reply(property_id: str, payload: ReplyIn, db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    """Record a seller reply.

    `manual_entry`: a person types in something the seller said elsewhere
    (a call, a voicemail) — saved and read by EvoSense directly.
    `sandbox_simulated` (SANDBOX properties only): the words are delivered
    through the platform's REAL inbound SMS processing
    (`sms_router.process_inbound_sms`) — the same persistence, hard-stop,
    DNC/suppression and EvoSense routing a Twilio webhook gets — minus only
    the Twilio signature (there is no Twilio) and with the caller's own
    organization in place of the receiving-number lookup.
    Real SMS replies need no route here: the inbound webhook delivers them."""
    org_id = svc.write_org_id(db, user)
    prop = _prop(db, org_id, property_id)
    if payload.delivery not in ("manual_entry", "sandbox_simulated"):
        raise HTTPException(status_code=422, detail="delivery must be manual_entry or sandbox_simulated")
    if payload.delivery == "sandbox_simulated" and not prop.is_test:
        raise HTTPException(status_code=409, detail="Simulated replies exist only for SANDBOX properties.")
    eng = (db.query(EvoSenseEngagement)
           .filter(EvoSenseEngagement.organization_id == org_id,
                   EvoSenseEngagement.property_id == prop.id,
                   EvoSenseEngagement.status.in_(("active", "responded", "handed_off", "nurture")))
           .order_by(EvoSenseEngagement.updated_at.desc()).first())
    if eng is None:
        raise HTTPException(status_code=409, detail="No conversation is open for this property.")
    if payload.delivery == "sandbox_simulated":
        return _simulate_inbound_sms(db, org_id, eng, payload.text)
    try:
        out = CV.receive(db, org_id, eng, payload.text, user=user, delivery=payload.delivery)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    return out


def _simulate_inbound_sms(db, org_id, eng, text):
    import uuid
    from app.models.evosense_models import EvoSenseMessage
    from app.models.models import Lead
    from app.routers.sms_router import process_inbound_sms
    lead = db.query(Lead).filter(Lead.id == eng.lead_id, Lead.organization_id == org_id).first() \
        if eng.lead_id else None
    if lead is None or not lead.phone or not lead.is_test:
        raise HTTPException(status_code=409, detail="This SANDBOX conversation has no test seller to reply.")
    if not (text or "").strip():
        raise HTTPException(status_code=422, detail="Empty message")
    sid = "SBX" + uuid.uuid4().hex[:28]
    platform = process_inbound_sms(db, org_id=org_id, advisor=None, From="+" + lead.phone.lstrip("+"),
                                   Body=text.strip(), MessageSid=sid)
    msg = (db.query(EvoSenseMessage)
           .filter(EvoSenseMessage.organization_id == org_id,
                   EvoSenseMessage.platform_ref == platform.get("reply_id")).first())
    reading = C.jload(msg.reading, {}) if msg else {}
    return {"delivery": "sandbox_simulated", "path": "platform inbound SMS processing",
            "platform": platform, "message_id": msg.id if msg else None,
            "outcome": msg.outcome if msg else None,
            "pending": (reading or {}).get("pending"), "why": (reading or {}).get("why")}


@router.post("/properties/{property_id}/retry-reading")
def retry_reading(property_id: str, db: Session = Depends(get_db),
                  user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    """Read again this property's saved seller replies whose reading is pending."""
    org_id = svc.write_org_id(db, user)
    prop = _prop(db, org_id, property_id)
    if C.controls(db, org_id).paused_all:
        raise HTTPException(status_code=409, detail="EvoSense is paused. Resume it to read held replies.")
    done = []
    for msg in CV.pending_messages(db, org_id).filter_by(property_id=prop.id).all():
        res = CV.evaluate(db, org_id, msg, user=user)
        done.append({"message_id": msg.id, "outcome": res.get("outcome"), "pending": res.get("pending")})
    db.commit()
    return {"retried": done}


class NurtureIn(BaseModel):
    choice: str                   # 30_days | 60_days | 90_days | 6_months | date
    date: Optional[str] = None
    reason: Optional[str] = None


@router.post("/properties/{property_id}/nurture")
def nurture(property_id: str, payload: NurtureIn, db: Session = Depends(get_db),
            user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    prop = _prop(db, org_id, property_id)
    eng = (db.query(EvoSenseEngagement)
           .filter(EvoSenseEngagement.organization_id == org_id, EvoSenseEngagement.property_id == prop.id,
                   EvoSenseEngagement.status.in_(("active", "responded", "handed_off", "nurture", "pending")))
           .order_by(EvoSenseEngagement.updated_at.desc()).first())
    if eng is None:
        raise HTTPException(status_code=409, detail="No conversation to nurture.")
    try:
        until = CV.nurture_until_from(payload.choice, payload.date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    from app.models.models import Lead
    lead = db.query(Lead).filter(Lead.id == eng.lead_id, Lead.organization_id == org_id).first() \
        if eng.lead_id else None
    CV.set_nurture(db, prop, eng, lead, until, payload.reason or "Set by %s" % (user.full_name or "a user"),
                   user=user)
    for h in db.query(EvoSenseHandoff).filter(EvoSenseHandoff.organization_id == org_id,
                                              EvoSenseHandoff.property_id == prop.id,
                                              EvoSenseHandoff.status.in_(("open", "acknowledged"))).all():
        HO.resolve(db, h, "dismissed", user, "Moved to nurture")
    EV.refresh_status(db, prop)
    db.commit()
    return {"nurture_until": until.isoformat() + "Z", "status": prop.status}


class PromoteIn(BaseModel):
    note: Optional[str] = None


@router.post("/properties/{property_id}/promote")
def promote(property_id: str, payload: PromoteIn, db: Session = Depends(get_db),
            user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    out = PR.promote(db, org_id, _prop(db, org_id, property_id), user, note=payload.note)
    db.commit()
    return out


class FeedbackIn(BaseModel):
    kind: str
    reason: Optional[str] = None


@router.post("/properties/{property_id}/feedback")
def feedback(property_id: str, payload: FeedbackIn, db: Session = Depends(get_db),
             user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    prop = _prop(db, org_id, property_id)
    if payload.kind not in C.FEEDBACK_KINDS:
        raise HTTPException(status_code=422, detail="kind must be one of %s" % ", ".join(C.FEEDBACK_KINDS))
    db.add(EvoSenseFeedback(organization_id=org_id, property_id=prop.id, kind=payload.kind,
                            reason=(payload.reason or "")[:250] or None, user_id=user.id,
                            snapshot=C.jdump({"opportunity": prop.opportunity_score,
                                              "contact": prop.contact_confidence,
                                              "intent": prop.seller_intent, "status": prop.status})))
    C.log_event(db, org_id, "feedback", property_id=prop.id, user=user, actor_type=C.ACTOR_USER,
                is_test=prop.is_test, summary="Feedback: %s" % payload.kind)
    db.commit()
    return {"ok": True, "note": "Recorded for review. EvoSense does not retrain itself on feedback."}


class ContactIn(BaseModel):
    kind: str = "phone"
    value: str
    person_name: Optional[str] = None
    role: str = "owner"


@router.post("/properties/{property_id}/contacts")
def add_contact(property_id: str, payload: ContactIn, db: Session = Depends(get_db),
                user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    """MANUAL contact entry — the fallback when no provider can find one."""
    from app.services import wholesale_enrichment as WE
    org_id = svc.write_org_id(db, user)
    prop = _prop(db, org_id, property_id)
    owner = CT.primary_owner(db, prop)
    if owner is None:
        raise HTTPException(status_code=409, detail="Add the owner of record first.")
    if payload.kind == "phone" and not CT.normalize_phone(payload.value):
        raise HTTPException(status_code=422, detail="Enter a 10-digit US phone number.")
    if payload.kind not in ("phone", "email"):
        raise HTTPException(status_code=422, detail="kind must be phone or email")
    res = WE.EnrichmentResult(status=WE.STATUS_SUCCEEDED, provider="manual",
                              phones=[WE.EnrichmentPhone(number=payload.value, confidence=None,
                                                         source="manual")] if payload.kind == "phone" else [],
                              emails=[payload.value] if payload.kind == "email" else [],
                              message="person:%s|%s" % (payload.person_name, payload.role)
                              if payload.person_name else None)
    touched = CT.apply_result(db, owner, res, PV.PROVIDERS["manual"])
    C.log_event(db, org_id, "contact.manual", property_id=prop.id, user=user, actor_type=C.ACTOR_USER,
                is_test=prop.is_test, summary="Contact added by hand")
    EV.rescore(db, prop)
    db.commit()
    return {"contacts": [c.id for c in touched], "status": prop.status}


@router.post("/properties/{property_id}/contacts/{contact_id}/wrong-party")
def mark_wrong_party(property_id: str, contact_id: str, db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    prop = _prop(db, org_id, property_id)
    cp = (db.query(EvoSenseContactPoint)
          .filter(EvoSenseContactPoint.id == contact_id, EvoSenseContactPoint.organization_id == org_id).first())
    if cp is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    cp.status = "wrong_party"
    cp.status_reason = "Marked wrong party by %s" % (user.full_name or "a user")
    C.log_event(db, org_id, "contact.wrong_party", property_id=prop.id, user=user,
                actor_type=C.ACTOR_USER, is_test=prop.is_test, summary="Contact marked wrong party")
    EV.rescore(db, prop)
    db.commit()
    return {"ok": True}


class SignalIn(BaseModel):
    signal_type: str
    note: Optional[str] = None


@router.post("/properties/{property_id}/signals")
def add_signal(property_id: str, payload: SignalIn, db: Session = Depends(get_db),
               user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    prop = _prop(db, org_id, property_id)
    if payload.signal_type not in SIG.CATALOG or SIG.CATALOG[payload.signal_type].get("derived"):
        raise HTTPException(status_code=422, detail="Choose a signal a person can observe.")
    SIG.upsert(db, prop, payload.signal_type, source="operator", connector_kind=C.MANUAL,
               confidence=70, normalized_value=payload.note or "entered by a person",
               provenance={"user": user.id}, user=user)
    C.log_event(db, org_id, "signal.manual", property_id=prop.id, user=user, actor_type=C.ACTOR_USER,
                is_test=prop.is_test, summary="Signal added: %s" % payload.signal_type)
    EV.rescore(db, prop)
    db.commit()
    return {"ok": True, "opportunity_score": prop.opportunity_score}


@router.post("/properties/{property_id}/rescore")
def rescore(property_id: str, db: Session = Depends(get_db),
            user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    prop = _prop(db, org_id, property_id)
    EV.rescore(db, prop)
    db.commit()
    return {"opportunity_score": prop.opportunity_score, "status": prop.status}


class HandoffIn(BaseModel):
    status: str
    note: Optional[str] = None


@router.post("/handoffs/{handoff_id}")
def resolve_handoff(handoff_id: str, payload: HandoffIn, db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    h = db.query(EvoSenseHandoff).filter(EvoSenseHandoff.id == handoff_id,
                                         EvoSenseHandoff.organization_id == org_id).first()
    if h is None:
        raise HTTPException(status_code=404, detail="Not found")
    if payload.status == "promoted":
        raise HTTPException(status_code=422, detail="Use the promote action.")
    try:
        HO.resolve(db, h, payload.status, user, payload.note)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    prop = _prop(db, org_id, h.property_id)
    EV.refresh_status(db, prop)
    db.commit()
    return {"ok": True, "status": prop.status}


# ── Identity review ─────────────────────────────────────────────────────────

@router.get("/identity-reviews")
def identity_reviews(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    org_id = _read_org(db, user)
    out = []
    for r in (db.query(EvoSenseIdentityReview)
              .filter(EvoSenseIdentityReview.organization_id == org_id,
                      EvoSenseIdentityReview.status == "open").all()):
        obs = db.query(EvoSenseObservation).filter(EvoSenseObservation.id == r.observation_id).first()
        payload = C.jload(obs.payload, {}) if obs else {}
        cands = [V.row(p) for p in db.query(EvoSenseProperty).filter(
            EvoSenseProperty.organization_id == org_id,
            EvoSenseProperty.id.in_(C.jload(r.candidate_property_ids, []) or ["-"])).all()]
        out.append({"id": r.id, "reason": r.reason, "created_at": V._iso(r.created_at),
                    "incoming": {"provider": obs.provider_key if obs else None,
                                 "connector_kind": obs.connector_kind if obs else None,
                                 "reference": obs.source_reference if obs else None,
                                 "address": payload.get("street_address"), "city": payload.get("city"),
                                 "zip_code": payload.get("zip_code"), "parcel_apn": payload.get("parcel_apn"),
                                 "signals": [s.get("type") for s in payload.get("signals") or []]},
                    "candidates": cands})
    return {"items": out}


class ReviewIn(BaseModel):
    action: str                    # merge | new | dismiss
    property_id: Optional[str] = None


@router.post("/identity-reviews/{review_id}")
def resolve_review(review_id: str, payload: ReviewIn, db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    r = db.query(EvoSenseIdentityReview).filter(EvoSenseIdentityReview.id == review_id,
                                                EvoSenseIdentityReview.organization_id == org_id).first()
    if r is None:
        raise HTTPException(status_code=404, detail="Not found")
    if r.status != "open":
        raise HTTPException(status_code=409, detail="Already resolved")
    try:
        prop = IN.resolve_review(db, org_id, r, payload.action, payload.property_id, user)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    for pid in C.jload(r.candidate_property_ids, []) or []:
        p = db.query(EvoSenseProperty).filter(EvoSenseProperty.id == pid).first()
        if p is not None:
            EV.rescore(db, p)
    if prop is not None:
        EV.rescore(db, prop)
    db.commit()
    return {"ok": True, "property_id": prop.id if prop else None}


# ── Strategies ──────────────────────────────────────────────────────────────

class StrategyIn(BaseModel):
    model_config = {"extra": "allow"}


@router.get("/strategies")
def list_strategies(include_archived: bool = False, db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_or_observer)):
    org_id = _read_org(db, user)
    q = db.query(EvoSenseStrategy).filter(EvoSenseStrategy.organization_id == org_id)
    if not include_archived:
        q = q.filter(EvoSenseStrategy.status != "archived")
    items = []
    for s in q.order_by(EvoSenseStrategy.created_at.desc()).all():
        items.append({**ST.payload(s), "metrics": V.strategy_metrics(db, org_id, s),
                      "automation": _automation(db, org_id, s)})
    return {"items": items, "signals": [{"key": k, "label": v["label"], "derived": bool(v.get("derived"))}
                                        for k, v in SIG.CATALOG.items()],
            "property_types": list(ST.PROPERTY_TYPES), "occupancy": list(ST.OCCUPANCY),
            "owner_geography": list(ST.OWNER_GEO)}


@router.post("/strategies/preview")
def preview_strategy(payload: StrategyIn, db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_or_observer)):
    """Validate and summarize WITHOUT saving — the Builder's summary step."""
    clean, problems = ST.validate(payload.model_dump(), partial=True)
    tmp = EvoSenseStrategy(organization_id="preview")
    tmp.handoff_intent_threshold = 70
    tmp.daily_budget_cents = 0
    ST.apply(tmp, clean)
    return {"problems": problems, "summary": ST.summary(tmp),
            "activation_problems": ST.activation_problems(tmp)}


@router.post("/strategies")
def create_strategy(payload: StrategyIn, db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    data = payload.model_dump()
    clean, problems = ST.validate(data)
    if problems:
        raise HTTPException(status_code=422, detail=" ".join(problems))
    if (clean.get("daily_budget_cents") or clean.get("monthly_budget_cents")) and \
            getattr(user, "role", None) not in ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Only a workspace admin can give a strategy a paid-data budget.")
    s = EvoSenseStrategy(organization_id=org_id, created_by_id=user.id, is_test=bool(data.get("is_test")))
    ST.apply(s, clean)
    db.add(s)
    db.flush()
    C.log_event(db, org_id, "strategy.created", strategy_id=s.id, user=user, actor_type=C.ACTOR_USER,
                is_test=s.is_test, summary="Strategy created: %s" % s.name)
    _apply_cadence(db, s, data)
    db.commit()
    return {**ST.payload(s), "automation": _automation(db, org_id, s)}


def _apply_cadence(db, strategy, data):
    """Automatic hunting: manual | daily | interval (hours). Daily by default."""
    from app.services.evosense import scheduler as SCH
    cadence = data.get("hunt_cadence")
    if cadence is None:
        SCH.schedule_for(db, strategy)
        return
    try:
        SCH.set_cadence(db, strategy, cadence, data.get("hunt_interval_hours"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


def _automation(db, org_id, strategy):
    from app.services.evosense import scheduler as SCH
    for row in SCH.status(db, org_id):
        if row["strategy_id"] == strategy.id:
            return row
    sched = SCH.schedule_for(db, strategy, create=False)
    return {"strategy_id": strategy.id, "cadence": sched.cadence if sched else SCH.DEFAULT_CADENCE,
            "interval_hours": sched.interval_hours if sched else None, "state": strategy.status,
            "next_due_at": None, "last_hunt_at": V._iso(strategy.last_hunt_at),
            "last_status": sched.last_status if sched else None}


@router.get("/strategies/{strategy_id}")
def get_strategy(strategy_id: str, db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_or_observer)):
    org_id = _read_org(db, user)
    s = ST.get(db, org_id, strategy_id)
    runs = (db.query(EvoSenseRun).filter(EvoSenseRun.organization_id == org_id,
                                         EvoSenseRun.strategy_id == s.id)
            .order_by(EvoSenseRun.started_at.desc()).limit(10).all())
    return {**ST.payload(s), "metrics": V.strategy_metrics(db, org_id, s),
            "automation": _automation(db, org_id, s),
            "runs": [{"id": r.id, "status": r.status, "trigger": r.trigger, "error": r.error,
                      "counts": C.jload(r.counts, {}), "started_at": V._iso(r.started_at),
                      "finished_at": V._iso(r.finished_at)} for r in runs]}


@router.patch("/strategies/{strategy_id}")
def edit_strategy(strategy_id: str, payload: StrategyIn, db: Session = Depends(get_db),
                  user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    s = ST.get(db, org_id, strategy_id)
    if s.status == "archived":
        raise HTTPException(status_code=409, detail="An archived strategy cannot be edited. Clone it.")
    clean, problems = ST.validate(payload.model_dump(), partial=True)
    if problems:
        raise HTTPException(status_code=422, detail=" ".join(problems))
    money_keys = {"daily_budget_cents", "monthly_budget_cents", "max_cost_per_property_cents",
                  "approval_over_cents"}
    changed_money = [k for k in money_keys & set(clean) if clean[k] != getattr(s, k)]
    if changed_money and getattr(user, "role", None) not in ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Only a workspace admin can change budgets.")
    before = ST.payload(s)
    ST.apply(s, clean)
    s.version = (s.version or 1) + 1
    if s.status == "active" and ST.activation_problems(s):
        raise HTTPException(status_code=422, detail=" ".join(ST.activation_problems(s)))
    data = payload.model_dump()
    if data.get("hunt_cadence") is not None:
        _apply_cadence(db, s, data)
    C.log_event(db, org_id, "strategy.edited", strategy_id=s.id, user=user, actor_type=C.ACTOR_USER,
                is_test=s.is_test, summary="Strategy edited (v%s)" % s.version,
                details={"changed": sorted(clean) + (["hunt_cadence"] if data.get("hunt_cadence") else []),
                         "before": {k: before.get(k) for k in clean}})
    db.commit()
    return {**ST.payload(s), "automation": _automation(db, org_id, s)}


@router.post("/strategies/{strategy_id}/clone")
def clone_strategy(strategy_id: str, db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    s = ST.get(db, org_id, strategy_id)
    c = EvoSenseStrategy(organization_id=org_id, created_by_id=user.id, cloned_from_id=s.id,
                         is_test=s.is_test, status="draft")
    for col in EvoSenseStrategy.__table__.columns.keys():
        if col in ST.EDITABLE:
            setattr(c, col, getattr(s, col))
    c.name = "%s (copy)" % s.name
    db.add(c)
    db.flush()
    C.log_event(db, org_id, "strategy.cloned", strategy_id=c.id, user=user, actor_type=C.ACTOR_USER,
                is_test=c.is_test, summary="Cloned from %s" % s.name)
    db.commit()
    return ST.payload(c)


@router.post("/strategies/{strategy_id}/hunt")
def hunt_now(strategy_id: str, background: bool = False, db: Session = Depends(get_db),
             user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    s = ST.get(db, org_id, strategy_id)
    if s.status != "active":
        raise HTTPException(status_code=409, detail="Activate the strategy before it can hunt.")
    if background:
        C.log_event(db, org_id, "hunt.queued", strategy_id=s.id, user=user, actor_type=C.ACTOR_USER,
                    is_test=s.is_test, summary="Hunt started in the background")
        db.commit()
        HU.start_background(org_id, s.id, user_id=user.id)
        return {"run_id": None, "status": "started", "background": True,
                "note": "Follow progress in the strategy's runs and the event log."}
    run = HU.run_strategy(db, org_id, s, trigger="manual", user=user)
    return {"run_id": run.id, "status": run.status, "error": run.error, "counts": C.jload(run.counts, {})}


@router.post("/strategies/{strategy_id}/pilot-archive")
def pilot_archive(strategy_id: str, db: Session = Depends(get_db),
                  user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    s = ST.get(db, org_id, strategy_id)
    out = HU.archive_pilot(db, org_id, s, user=user)
    db.commit()
    return out


@router.post("/strategies/{strategy_id}/pilot-restore")
def pilot_restore(strategy_id: str, db: Session = Depends(get_db),
                  user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    s = ST.get(db, org_id, strategy_id)
    out = HU.unarchive_pilot(db, org_id, s, user=user)
    db.commit()
    return out


def _strategy_action(action: str):
    def endpoint(strategy_id: str, db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
        return strategy_action(strategy_id, action, db, user)
    endpoint.__name__ = "strategy_" + action
    return endpoint


# One explicit route per lifecycle verb (not a "/{action}" catch-all), so every
# route is a fixed template the cross-tenant guard can enumerate.
for _action in ("activate", "pause", "resume", "archive"):
    router.add_api_route("/strategies/{strategy_id}/%s" % _action, _strategy_action(_action),
                         methods=["POST"])


def strategy_action(strategy_id: str, action: str, db, user):
    org_id = svc.write_org_id(db, user)
    s = ST.get(db, org_id, strategy_id)
    before = s.status
    ST.transition(s, action)
    if s.status == "active":
        from app.services.evosense import scheduler as SCH
        SCH.schedule_for(db, s)               # automatic hunting starts with the strategy
    C.log_event(db, org_id, "strategy." + action, strategy_id=s.id, user=user, actor_type=C.ACTOR_USER,
                is_test=s.is_test, summary="Strategy %s → %s" % (before, s.status))
    db.commit()
    return ST.payload(s)


# ── Inbound routing review (Phase 7.1) ──────────────────────────────────────

class RoutingIn(BaseModel):
    engagement_id: Optional[str] = None       # None = belongs to no EvoSense conversation


@router.post("/routing-reviews/{review_id}")
def resolve_routing(review_id: str, payload: RoutingIn, db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    from app.services.evosense import inbound as INB
    org_id = svc.write_org_id(db, user)
    try:
        return INB.resolve_routing_review(db, org_id, review_id, payload.engagement_id, user)
    except LookupError:
        raise HTTPException(status_code=404, detail="Not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


# ── Providers and controls ──────────────────────────────────────────────────

@router.get("/providers")
def providers(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    org_id = _read_org(db, user)
    out = PV.status_report(db, org_id)
    db.commit()
    return out


@router.get("/sources")
def sources(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    """The Source Registry: every real, manual and not-configured source with
    its jurisdiction, access method, freshness and verified health."""
    org_id = _read_org(db, user)
    out = PV.source_registry(db, org_id)
    db.commit()
    return out


class VerifyIn(BaseModel):
    key: str


@router.post("/sources/verify")
def verify_source(payload: VerifyIn, db: Session = Depends(get_db),
                  user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    """A cheap real probe of one of THIS organization's sources (the key names
    an adapter, not a record). Only a success makes a source HEALTHY."""
    source_key = payload.key
    org_id = svc.write_org_id(db, user)
    _admin(user)
    if source_key not in PV.PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown source")
    res = PV.verify_source(db, org_id, source_key,
                           platform_admin=getattr(user, "role", None) in PV.PLATFORM_ADMIN_ROLES)
    C.log_event(db, org_id, "source.verified" if res.get("ok") else "source.verify_failed", user=user,
                actor_type=C.ACTOR_USER, summary="%s verify: %s" % (
                    source_key, "ok" if res.get("ok") else "%s %s" % (res.get("code"), res.get("error"))))
    db.commit()
    return {**res, "registry": PV.source_registry(db, org_id)}


class ProviderPatch(BaseModel):
    key: str
    enabled: Optional[bool] = None
    priority: Optional[int] = None


@router.patch("/providers")
def patch_provider(payload: ProviderPatch, db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    """Configure one of THIS organization's providers. The key names an adapter,
    not a record, and the row edited is always the caller's own."""
    org_id = svc.write_org_id(db, user)
    _admin(user)
    key = payload.key
    if key not in PV.PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider")
    cfg = PV.config(db, org_id, key)
    if payload.enabled is not None:
        cfg.enabled = payload.enabled
    if payload.priority is not None:
        cfg.priority = max(0, min(1000, payload.priority))
    C.log_event(db, org_id, "provider.configured", user=user, actor_type=C.ACTOR_USER,
                summary="%s: enabled=%s priority=%s" % (key, cfg.enabled, cfg.priority))
    db.commit()
    return PV.status_report(db, org_id)


@router.get("/controls")
def get_controls(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    org_id = _read_org(db, user)
    out = V.controls_payload(C.controls(db, org_id), db, org_id)
    db.commit()
    return out


class ControlsPatch(BaseModel):
    paused_all: Optional[bool] = None
    paused_discovery: Optional[bool] = None
    paused_paid_data: Optional[bool] = None
    paused_sms: Optional[bool] = None
    paused_email: Optional[bool] = None
    paused_voice: Optional[bool] = None
    paused_ai_replies: Optional[bool] = None
    org_daily_budget_cents: Optional[int] = None
    org_monthly_budget_cents: Optional[int] = None
    owner_touch_cap_days: Optional[int] = None
    score_weights: Optional[Dict[str, int]] = None


@router.patch("/controls")
def patch_controls(payload: ControlsPatch, db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    ctl = C.controls(db, org_id)
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        if k == "score_weights":
            _admin(user)
            from app.services.evosense import scoring as SC
            ctl.score_weights = C.jdump(SC.clean_weights(v)) if v else None
            continue
        if k.startswith("paused_"):
            if v is None:
                continue
            if v is False and getattr(ctl, k) and getattr(user, "role", None) not in ADMIN_ROLES:
                raise HTTPException(status_code=403, detail="Anyone can pause; only a workspace admin can resume.")
        else:
            _admin(user)
            if v is not None and v < 0:
                raise HTTPException(status_code=422, detail="%s cannot be negative" % k)
        setattr(ctl, k, v)
    ctl.updated_by_id = user.id
    C.log_event(db, org_id, "controls.changed", user=user, actor_type=C.ACTOR_USER,
                summary="Controls changed: %s" % ", ".join("%s=%s" % kv for kv in data.items()),
                details=data)
    db.commit()
    return V.controls_payload(ctl, db, org_id)


@router.get("/events")
def events(limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db),
           user: User = Depends(require_tenant_or_observer)):
    org_id = _read_org(db, user)
    rows = (db.query(EvoSenseEvent).filter(EvoSenseEvent.organization_id == org_id)
            .order_by(EvoSenseEvent.created_at.desc()).limit(limit).all())
    return {"items": [{"id": e.id, "action": e.action, "summary": e.summary, "actor": e.actor_type,
                       "property_id": e.property_id, "strategy_id": e.strategy_id,
                       "is_test": e.is_test, "at": V._iso(e.created_at)} for e in rows]}
