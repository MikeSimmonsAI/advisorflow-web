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


def _admin(user, db):
    """Workspace admin check FOR THE WORKSPACE BEING ACTED ON.

    Every admin action here operates on `svc.write_org_id` / `_read_org`, i.e.
    the ACTIVE workspace (X-Workspace-Id backed by a membership). This used to
    read the account-global `users.role`, so a person who is org_admin of
    workspace A but an advisor/viewer in workspace B could change budgets,
    provider switches, ground truth and reprocess runs inside B.

    Now the role is `lead_scope.effective_role` - the caller's membership role
    in the selected workspace, falling back to `users.role` only when no
    workspace is selected (same resolution deps.require_admin uses, and the
    same ambient request svc.write_org_id reads). Platform operators
    (god_admin / super_admin) keep today's behaviour: their authority is not a
    customer membership grant, and the org they can reach is already bounded
    by write_org_id/_read_org.
    """
    if getattr(user, "role", None) in ("god_admin", "super_admin"):
        return
    from app.services.lead_scope import effective_role
    if effective_role(user, db) not in ADMIN_ROLES:
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


@router.get("/import/list-kinds")
def import_list_kinds(user: User = Depends(require_tenant_or_observer)):
    """The distress-list kinds the one EvoSense import understands."""
    return {"list_kinds": HU.distress_list_kinds(), "columns": list(HU.CSV_COLUMNS),
            "evidence_columns": list(HU.EVIDENCE_COLUMNS)}


@router.post("/import")
async def import_properties(file: UploadFile = File(...), strategy_id: Optional[str] = Form(None),
                            list_kind: Optional[str] = Form(None), list_source: Optional[str] = Form(None),
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
                            filename=file.filename or "upload.csv",
                            list_kind=(list_kind or None), list_source=((list_source or "").strip()[:120] or None))
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


class ContactVerifyIn(BaseModel):
    note: Optional[str] = None
    verified: bool = True


@router.post("/properties/{property_id}/contacts/{contact_id}/verify")
def verify_contact(property_id: str, contact_id: str, payload: ContactVerifyIn,
                   db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
                   _g: User = Depends(require_not_observation)):
    """A PERSON confirms this is the right person at this number/address (a
    call, a reply that identified them). Raises Contact Confidence; grants no
    permission to contact - consent and blocks are unchanged. Reversible."""
    org_id = svc.write_org_id(db, user)
    prop = _prop(db, org_id, property_id)
    cp = (db.query(EvoSenseContactPoint)
          .filter(EvoSenseContactPoint.id == contact_id, EvoSenseContactPoint.organization_id == org_id,
                  EvoSenseContactPoint.owner_id.in_([o.id for o in CT.current_owners(db, prop)]))
          .first())
    if cp is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    if payload.verified:
        cp.verified_at = C.now()
        cp.verified_by_id = user.id
        cp.verification_note = (payload.note or "").strip()[:200] or None
    else:
        cp.verified_at = cp.verified_by_id = cp.verification_note = None
    C.log_event(db, org_id, "contact.verified" if payload.verified else "contact.unverified",
                property_id=prop.id, user=user, actor_type=C.ACTOR_USER, is_test=prop.is_test,
                summary=("Contact verified by %s" if payload.verified else "Verification removed by %s")
                % (user.full_name or "a user"), details={"contact_point": cp.id, "note": cp.verification_note})
    EV.rescore(db, prop)
    db.commit()
    return {"ok": True, "contactability": C.jload(prop.contactability_detail, None)}


class EvoCompIn(BaseModel):
    street_address: str
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    sale_price: float
    sale_date: str
    source_reference: str
    square_feet: Optional[int] = None
    bedrooms: Optional[float] = None
    bathrooms: Optional[float] = None
    half_baths: Optional[int] = None
    year_built: Optional[int] = None
    lot_size_sqft: Optional[int] = None
    property_type: Optional[str] = None
    sale_type: Optional[str] = None
    distance_miles: Optional[float] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    attestation: Optional[str] = None
    notes: Optional[str] = None


@router.post("/properties/{property_id}/comps")
def add_evosense_comp(property_id: str, payload: EvoCompIn, db: Session = Depends(get_db),
                      user: User = Depends(require_tenant_user),
                      _g: User = Depends(require_not_observation)):
    """A MANUAL sold comp for an EvoSense property, with the evidence a second
    person could check (address, closed price, sale date, source reference).
    Labelled MANUAL for good; it moves to the deal on promotion."""
    from datetime import datetime as _dt
    from app.models.wholesale_models import WholesaleComp
    from app.services.evosense import valuation as VAL
    org_id = svc.write_org_id(db, user)
    prop = _prop(db, org_id, property_id)
    try:
        sdate = _dt.fromisoformat(payload.sale_date[:10]).date()
    except ValueError:
        raise HTTPException(status_code=422, detail="Sale date must be YYYY-MM-DD.")
    if payload.sale_price <= 0 or not payload.source_reference.strip():
        raise HTTPException(status_code=422, detail="A closed sale needs a price and a source reference.")
    data = payload.model_dump(exclude={"sale_date"})
    if data.get("sale_type"):
        data["sale_type"] = data["sale_type"].strip().lower().replace(" ", "_")
    comp = WholesaleComp(organization_id=org_id, deal_id=prop.promoted_deal_id,
                         evosense_property_id=prop.id, source="manual",
                         verification_state="manual", entered_by_id=user.id,
                         is_test=bool(prop.is_test), sale_date=sdate, **data)
    db.add(comp)
    C.log_event(db, org_id, "comp.added", property_id=prop.id, user=user, actor_type=C.ACTOR_USER,
                is_test=prop.is_test, summary="Manual sold comp added: %s" % payload.street_address)
    db.flush()
    res = VAL.arv(db, prop)
    db.commit()
    return {"comp_id": comp.id, "arv": res}


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
                  "approval_over_cents", "enrichment_policy"}
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


class EvaluationIn(BaseModel):
    name: str
    provider_keys: List[str]
    property_ids: List[str]
    mode: str = "contact"                      # contact | comps
    referee_key: Optional[str] = None          # a PHONE_VALIDATION provider (contact mode)
    ground_truth: Optional[Dict[str, Dict[str, Any]]] = None
    confirm: Optional[str] = None


@router.post("/provider-evaluations")
def run_provider_evaluation(payload: EvaluationIn, db: Session = Depends(get_db),
                            user: User = Depends(require_tenant_user),
                            _g: User = Depends(require_not_observation)):
    """PLAN an evaluation: validate it and compute its maximum spend. Calls
    nothing paid. A sandbox-only plan (SYNTHETIC) runs at once; a plan with a
    real provider is saved as PLANNED and runs only through /execute with
    "RUN PAID EVALUATION <id>". Admin only."""
    from app.services.evosense import provider_eval as PE
    _admin(user, db)
    org_id = svc.write_org_id(db, user)
    try:
        ev = PE.plan(db, org_id, name=payload.name, provider_keys=payload.provider_keys,
                     property_ids=payload.property_ids, mode=payload.mode,
                     ground_truth=payload.ground_truth, referee_key=payload.referee_key, user=user)
        if ev.synthetic:
            PE.execute(db, ev, user=user)
    except PE.EvaluationRefused as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    db.commit()
    return PE.payload(ev)


class ExecuteIn(BaseModel):
    confirm: Optional[str] = None


@router.post("/provider-evaluations/{evaluation_id}/execute")
def execute_provider_evaluation(evaluation_id: str, payload: ExecuteIn, db: Session = Depends(get_db),
                                user: User = Depends(require_tenant_user),
                                _g: User = Depends(require_not_observation)):
    """Run a PLANNED evaluation. A paid plan needs the owner's exact phrase
    "RUN PAID EVALUATION <id>" and never spends beyond its planned maximum."""
    from app.models.evosense_models import EvoSenseProviderEvaluation
    from app.services.evosense import provider_eval as PE
    _admin(user, db)
    org_id = svc.write_org_id(db, user)
    ev = (db.query(EvoSenseProviderEvaluation)
          .filter(EvoSenseProviderEvaluation.id == evaluation_id,
                  EvoSenseProviderEvaluation.organization_id == org_id).first())
    if ev is None:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    try:
        PE.execute(db, ev, confirm=payload.confirm, user=user)
    except PE.EvaluationRefused as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    db.commit()
    return PE.payload(ev)


class GroundTruthIn(BaseModel):
    kind: str                                   # contact | closed_sale
    property_id: Optional[str] = None
    data: Dict[str, Any]
    source_note: str


@router.get("/ground-truth")
def list_ground_truth(db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """Facts the workspace already lawfully knows, used only to score provider
    evaluations. Admin only (they can hold people's phone numbers)."""
    from app.models.evosense_models import EvoSenseEvalGroundTruth
    from app.services.evosense import provider_eval as PE
    _admin(user, db)
    org_id = _read_org(db, user)
    rows = (db.query(EvoSenseEvalGroundTruth).filter(EvoSenseEvalGroundTruth.organization_id == org_id)
            .order_by(EvoSenseEvalGroundTruth.created_at.desc()).limit(500).all())
    return {"items": [PE.ground_truth_payload(r) for r in rows]}


@router.post("/ground-truth")
def add_ground_truth(payload: GroundTruthIn, db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    from app.services.evosense import provider_eval as PE
    _admin(user, db)
    org_id = svc.write_org_id(db, user)
    try:
        row = PE.add_ground_truth(db, org_id, kind=payload.kind, data=payload.data,
                                  source_note=payload.source_note, property_id=payload.property_id, user=user)
    except PE.EvaluationRefused as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    C.log_event(db, org_id, "ground_truth.added", user=user, actor_type=C.ACTOR_USER,
                summary="Evaluation ground truth added (%s)" % payload.kind)
    db.commit()
    return PE.ground_truth_payload(row)


@router.delete("/ground-truth/{truth_id}")
def delete_ground_truth(truth_id: str, db: Session = Depends(get_db),
                        user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    from app.models.evosense_models import EvoSenseEvalGroundTruth
    _admin(user, db)
    org_id = svc.write_org_id(db, user)
    row = (db.query(EvoSenseEvalGroundTruth)
           .filter(EvoSenseEvalGroundTruth.id == truth_id,
                   EvoSenseEvalGroundTruth.organization_id == org_id).first())
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(row)
    C.log_event(db, org_id, "ground_truth.deleted", user=user, actor_type=C.ACTOR_USER,
                summary="Evaluation ground truth deleted (%s)" % row.kind)
    db.commit()
    return {"ok": True}


class GateIn(BaseModel):
    criterion: str
    met: bool
    evidence: str = ""
    value: Optional[str] = None


@router.get("/truth-gate")
def truth_gates(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    """The DFW sold-comps truth gate for every comps provider."""
    from app.services.evosense import truth_gate as TG
    org_id = _read_org(db, user)
    return {"items": [TG.evaluate(db, org_id, k) for k, p in PV.PROVIDERS.items()
                      if C.COMPS in (p.capabilities or ()) and p.connector_kind == C.REAL]}


@router.post("/truth-gate/{provider_key}")
def attest_truth_gate(provider_key: str, payload: GateIn, db: Session = Depends(get_db),
                      user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    """Record one attested criterion WITH its evidence. Admin only."""
    from app.services.evosense import truth_gate as TG
    _admin(user, db)
    org_id = svc.write_org_id(db, user)
    try:
        out = TG.attest(db, org_id, provider_key, payload.criterion, met=payload.met,
                        evidence=payload.evidence, value=payload.value, user=user)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    return out


@router.get("/provider-evaluations/setup")
def provider_evaluation_setup(mode: str = "contact", db: Session = Depends(get_db),
                              user: User = Depends(require_tenant_or_observer)):
    """What an evaluation would use: the proposed authorized sample (real
    properties this workspace already researches), every candidate provider
    with its readiness, and the confirmation a paid run would need. Reads
    only - calls no provider."""
    from app.services.evosense import provider_eval as PE
    if mode not in (PE.MODE_CONTACT, PE.MODE_COMPS):
        raise HTTPException(status_code=422, detail="Mode must be contact or comps.")
    org_id = _read_org(db, user)
    return {"sample": PE.proposed_sample(db, org_id, mode), "providers": PE.candidates(db, org_id),
            "confirmation_format": PE.PAID_CONFIRMATION + " <n>",
            "referee_max_per_lookup": PE.REFEREE_MAX_PER_LOOKUP}


@router.post("/provider-evaluations/{evaluation_id}/purge")
def purge_provider_evaluation(evaluation_id: str, db: Session = Depends(get_db),
                              user: User = Depends(require_tenant_user),
                              _g: User = Depends(require_not_observation)):
    """Delete an evaluation's returned values (contacts, comps, ground truth)
    and keep only its aggregate metrics. Admin only; irreversible."""
    from app.models.evosense_models import EvoSenseProviderEvaluation
    from app.services.evosense import provider_eval as PE
    _admin(user, db)
    org_id = svc.write_org_id(db, user)
    ev = (db.query(EvoSenseProviderEvaluation)
          .filter(EvoSenseProviderEvaluation.id == evaluation_id,
                  EvoSenseProviderEvaluation.organization_id == org_id).first())
    if ev is None:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    PE.purge(db, ev, user=user)
    db.commit()
    return PE.payload(ev)


@router.get("/provider-evaluations")
def list_provider_evaluations(db: Session = Depends(get_db),
                              user: User = Depends(require_tenant_or_observer)):
    from app.models.evosense_models import EvoSenseProviderEvaluation
    from app.services.evosense import provider_eval as PE
    org_id = _read_org(db, user)
    rows = (db.query(EvoSenseProviderEvaluation)
            .filter(EvoSenseProviderEvaluation.organization_id == org_id)
            .order_by(EvoSenseProviderEvaluation.created_at.desc()).limit(50).all())
    return {"items": [PE.payload(r, with_records=False) for r in rows]}


@router.get("/provider-evaluations/{evaluation_id}")
def get_provider_evaluation(evaluation_id: str, db: Session = Depends(get_db),
                            user: User = Depends(require_tenant_or_observer)):
    from app.models.evosense_models import EvoSenseProviderEvaluation
    from app.services.evosense import provider_eval as PE
    org_id = _read_org(db, user)
    ev = (db.query(EvoSenseProviderEvaluation)
          .filter(EvoSenseProviderEvaluation.id == evaluation_id,
                  EvoSenseProviderEvaluation.organization_id == org_id).first())
    if ev is None:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    # The returned values (people's phones and emails) are shown to a
    # workspace admin only; everyone else sees the metrics.
    out = PE.payload(ev, with_records=getattr(user, "role", None) in ADMIN_ROLES)
    if ev.status == "planned":
        out["readiness"] = PE.plan_readiness(db, ev)
    if not getattr(user, "role", None) in ADMIN_ROLES and out.get("report"):
        out["report"] = dict(out["report"], examples=[])
    return out


@router.get("/capabilities")
def capability_registry(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    """What each provider can actually supply for this workspace, per
    capability, with platform state and tenant state kept apart."""
    from app.services.evosense import capabilities as CAPS
    org_id = _read_org(db, user)
    return {"capabilities": CAPS.matrix(db, org_id),
            "states": [CAPS.S_OPERATIONAL, CAPS.S_UNVERIFIED, CAPS.S_DEGRADED, CAPS.S_NOT_ENABLED,
                       CAPS.S_BLOCKED, CAPS.S_NOT_CONFIGURED, CAPS.S_MANUAL, CAPS.S_SANDBOX,
                       CAPS.S_EVALUATION, CAPS.S_GATED]}


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
    _admin(user, db)
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
    _admin(user, db)
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
    scoring_options: Optional[Dict[str, bool]] = None


@router.patch("/controls")
def patch_controls(payload: ControlsPatch, db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    ctl = C.controls(db, org_id)
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        if k == "score_weights":
            _admin(user, db)
            from app.services.evosense import scoring as SC
            ctl.score_weights = C.jdump(SC.clean_weights(v)) if v else None
            continue
        if k == "scoring_options":
            _admin(user, db)
            from app.services.evosense import scoring as SC
            opts = SC.clean_options(v) if v else None
            ctl.scoring_options = C.jdump(opts) if opts and opts != SC.DEFAULT_OPTIONS else None
            continue
        if k.startswith("paused_"):
            if v is None:
                continue
            if v is False and getattr(ctl, k) and getattr(user, "role", None) not in ADMIN_ROLES:
                raise HTTPException(status_code=403, detail="Anyone can pause; only a workspace admin can resume.")
        else:
            _admin(user, db)
            if v is not None and v < 0:
                raise HTTPException(status_code=422, detail="%s cannot be negative" % k)
        setattr(ctl, k, v)
    ctl.updated_by_id = user.id
    C.log_event(db, org_id, "controls.changed", user=user, actor_type=C.ACTOR_USER,
                summary="Controls changed: %s" % ", ".join("%s=%s" % kv for kv in data.items()),
                details=data)
    db.commit()
    return V.controls_payload(ctl, db, org_id)


# ── Re-derivation (Priority 3): dry run, then an explicit, confirmed apply ──
#
# Admin-only and organization-scoped. A dry run writes nothing but the run
# record; apply and rollback each require the operator to type the run id.

class ReprocessIn(BaseModel):
    strategy_id: Optional[str] = None


class ConfirmIn(BaseModel):
    confirm: str = ""


def _run_or_404(db, org_id, run_id):
    from app.services.evosense import reprocess as RP
    run = RP.get_run(db, org_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Not found")
    return run


@router.post("/reprocess/dry-run")
def reprocess_dry_run(payload: ReprocessIn, db: Session = Depends(get_db),
                      user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    from app.services.evosense import reprocess as RP
    _admin(user, db)
    org_id = svc.write_org_id(db, user)
    if payload.strategy_id:
        ST.get(db, org_id, payload.strategy_id)          # 404 for another tenant's strategy
    run = RP.dry_run(db, org_id, strategy_id=payload.strategy_id, user=user)
    db.commit()
    return RP.run_json(run)


@router.get("/reprocess")
def reprocess_runs(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    from app.models.evosense_models import EvoSenseReprocessRun
    from app.services.evosense import reprocess as RP
    org_id = _read_org(db, user)
    rows = (db.query(EvoSenseReprocessRun).filter(EvoSenseReprocessRun.organization_id == org_id)
            .order_by(EvoSenseReprocessRun.created_at.desc()).limit(50).all())
    return {"items": [RP.run_json(r, with_diff=False) for r in rows]}


@router.get("/reprocess/{run_id}")
def reprocess_run(run_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    from app.services.evosense import reprocess as RP
    org_id = _read_org(db, user)
    return RP.run_json(_run_or_404(db, org_id, run_id))


@router.post("/reprocess/{run_id}/apply")
def reprocess_apply(run_id: str, payload: ConfirmIn, db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    from app.services.evosense import reprocess as RP
    org_id = svc.write_org_id(db, user)
    run = _run_or_404(db, org_id, run_id)
    _admin(user, db)
    if payload.confirm != "APPLY %s" % run.id:
        raise HTTPException(status_code=422, detail="Type APPLY %s to confirm." % run.id)
    try:
        out = RP.apply(db, org_id, run, user=user)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    db.commit()
    return out


@router.post("/reprocess/{run_id}/rollback")
def reprocess_rollback(run_id: str, payload: ConfirmIn, db: Session = Depends(get_db),
                       user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    from app.services.evosense import reprocess as RP
    org_id = svc.write_org_id(db, user)
    run = _run_or_404(db, org_id, run_id)
    _admin(user, db)
    if payload.confirm != "ROLLBACK %s" % run.id:
        raise HTTPException(status_code=422, detail="Type ROLLBACK %s to confirm." % run.id)
    try:
        out = RP.rollback(db, org_id, run, user=user)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    db.commit()
    return out


@router.get("/events")
def events(limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db),
           user: User = Depends(require_tenant_or_observer)):
    org_id = _read_org(db, user)
    rows = (db.query(EvoSenseEvent).filter(EvoSenseEvent.organization_id == org_id)
            .order_by(EvoSenseEvent.created_at.desc()).limit(limit).all())
    return {"items": [{"id": e.id, "action": e.action, "summary": e.summary, "actor": e.actor_type,
                       "property_id": e.property_id, "strategy_id": e.strategy_id,
                       "is_test": e.is_test, "at": V._iso(e.created_at)} for e in rows]}
