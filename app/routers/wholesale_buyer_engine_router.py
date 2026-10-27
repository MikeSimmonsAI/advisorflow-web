"""BUYER INTELLIGENCE - the one Buyers screen's API (Find / Qualify / Match).

    GET  /wholesale/buyer-intel/summary            the four cards (real counts, this workspace)
    GET  /wholesale/buyer-intel/taxonomy           segments, sub-segments, statuses
    GET  /wholesale/buyer-intel/buyers             the list, every filter combinable
    GET  /wholesale/buyer-intel/buyers/{id}        overview, buy boxes, evidence, enrichment, AI, matches
    PATCH .../buyers/{id}/facts                    a person records a dated fact (contact works, decision-maker ...)
    POST .../buyers/{id}/buy-boxes/{box}/confirm   a person confirmed the criteria with the buyer
    POST .../buyers/{id}/evidence                  a purchase a person knows of (classed by its proof)
    POST .../buyers/{id}/request-qualification     queue it for a person (work queue)
    POST .../buyers/{id}/opt-out | /unlink
    GET  .../identity-review ; POST .../identity-review/{id}   possible related LLCs - a person decides
    POST .../evidence/refresh ; GET .../evidence/status ; POST .../evidence/sync-deals
    GET  .../match-candidates ; POST .../match ; POST .../match/save ; POST .../match/prepare
    GET  .../demand                                 buyer demand -> suggested hunt criteria (read only)
    POST .../practice-buyer ; POST .../buyers/{id}/qualify
    POST .../conversations/{id}/(reply|pause|resume|takeover|note|followup|schedule)

Organization-scoped everywhere (another workspace's id is a 404). Field logins
are fenced off (wholesale_team.FIELD_BLOCKED_PREFIXES). Nothing here sends a
message, buys a lookup or starts a hunt.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_not_observation, require_tenant_or_observer, require_tenant_user
from app.models.models import User
from app.models.wholesale_models import ACTOR_USER, WholesaleBuyBox, WholesaleBuyer
from app.services import wholesale_buyer_engine as ENG
from app.services import wholesale_buyer_evidence as EVD
from app.services import wholesale_buyer_finder as finder
from app.services import wholesale_buyer_match as BM
from app.services import wholesale_buyer_qualify as Q
from app.services import wholesale_service as svc
from app.services.entitlements import require_feature

router = APIRouter(prefix="/wholesale/buyer-intel", tags=["wholesale-buyer-intelligence"],
                   dependencies=[Depends(require_feature("wholesale_real_estate"))])


def _read(db, user) -> str:
    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=409, detail="No customer organization selected.")
    return org_id


def _buyer(db, org_id: str, buyer_id: str) -> WholesaleBuyer:
    b = db.query(WholesaleBuyer).filter(WholesaleBuyer.id == buyer_id,
                                        WholesaleBuyer.organization_id == org_id).first()
    if b is None:
        raise HTTPException(status_code=404, detail="Buyer not found")
    return b


@router.get("/taxonomy")
def taxonomy(user: User = Depends(require_tenant_or_observer)):
    return ENG.taxonomy()


@router.get("/summary")
def summary(include_test: bool = Query(False), db: Session = Depends(get_db),
            user: User = Depends(require_tenant_or_observer)):
    return ENG.summary(db, _read(db, user), include_test=include_test)


@router.get("/buyers")
def buyers(segment: str = Query("all"), sub: Optional[str] = None, state: Optional[str] = None,
           county: Optional[str] = None, city: Optional[str] = None, zip: Optional[str] = None,
           price_min: Optional[float] = None, price_max: Optional[float] = None,
           units_min: Optional[float] = None, units_max: Optional[float] = None,
           acres_min: Optional[float] = None, acres_max: Optional[float] = None,
           recent: Optional[str] = None, strategy: Optional[str] = None, verification: Optional[str] = None,
           readiness: Optional[str] = None, ai: Optional[str] = None, contactable: Optional[str] = None,
           interaction: Optional[str] = None, funding: Optional[str] = None, match: Optional[str] = None,
           q: Optional[str] = Query(None, max_length=80), show_linked: bool = False,
           include_test: bool = False, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
           db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    if segment not in ENG.SEGMENT_KEYS:
        raise HTTPException(status_code=400, detail="Unknown segment")
    f = {k: v for k, v in dict(segment=segment, sub=sub, state=state, county=county, city=city, zip=zip,
                                price_min=price_min, price_max=price_max, units_min=units_min, units_max=units_max,
                                acres_min=acres_min, acres_max=acres_max, recent=recent, strategy=strategy,
                                verification=verification, readiness=readiness, ai=ai, contactable=contactable,
                                interaction=interaction, funding=funding, match=match, q=q,
                                show_linked=show_linked).items() if v not in (None, "")}
    return ENG.listing(db, _read(db, user), f, include_test=include_test, limit=limit, offset=offset)


@router.get("/buyers/{buyer_id}")
def buyer_detail(buyer_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    org_id = _read(db, user)
    return ENG.detail(db, org_id, buyer_id)


class FactsIn(BaseModel):
    contact_verified: Optional[bool] = None
    contact_verified_how: Optional[str] = None
    contact_bad: Optional[bool] = None
    decision_maker_name: Optional[str] = None
    decision_maker_confirmed: Optional[bool] = None
    stated_buying_status: Optional[str] = None
    stated_funding: Optional[str] = None
    stated_budget_min: Optional[float] = None
    stated_budget_max: Optional[float] = None
    stated_close_days: Optional[int] = None
    stated_capacity: Optional[str] = None
    identity_confirmed: Optional[bool] = None
    proof_of_funds_on_file: Optional[bool] = None
    proof_of_funds_expires: Optional[str] = None
    ai_paused: Optional[bool] = None


@router.patch("/buyers/{buyer_id}/facts")
def set_facts(buyer_id: str, payload: FactsIn, db: Session = Depends(get_db),
              user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    """A person records what they established - each fact gets today's date so it can go stale."""
    org_id = svc.write_org_id(db, user)
    b = _buyer(db, org_id, buyer_id)
    now = datetime.utcnow()
    changed = []
    if payload.contact_verified:
        b.contact_verified_at, b.contact_verified_how = now, (payload.contact_verified_how or
                                                              "Checked by %s" % (user.full_name or "your team"))[:120]
        changed.append("contact verified")
    if payload.contact_bad:
        b.contact_bad_at = now
        changed.append("contact marked not working")
    if payload.decision_maker_name is not None:
        b.decision_maker_name = payload.decision_maker_name.strip()[:120] or None
        changed.append("decision-maker")
    if payload.decision_maker_confirmed is not None:
        b.decision_maker_confirmed = payload.decision_maker_confirmed
    if payload.stated_buying_status is not None:
        if payload.stated_buying_status not in ("buying", "paused", "not_buying"):
            raise HTTPException(status_code=400, detail="Buying status must be buying, paused or not_buying")
        b.stated_buying_status, b.stated_buying_at = payload.stated_buying_status, now
        changed.append("buying status")
    for k in ("stated_funding", "stated_budget_min", "stated_budget_max", "stated_close_days", "stated_capacity"):
        v = getattr(payload, k)
        if v is not None:
            setattr(b, k, v)
            changed.append(k.replace("stated_", "").replace("_", " "))
    if payload.identity_confirmed is not None:
        b.identity_status = "confirmed" if payload.identity_confirmed else None
        changed.append("identity")
    if payload.proof_of_funds_on_file is not None:
        b.proof_of_funds_on_file = payload.proof_of_funds_on_file
        changed.append("proof of funds")
    if payload.proof_of_funds_expires is not None:
        try:
            b.proof_of_funds_expires = datetime.strptime(payload.proof_of_funds_expires[:10], "%Y-%m-%d").date() \
                if payload.proof_of_funds_expires else None
        except ValueError:
            raise HTTPException(status_code=400, detail="Expiry must be YYYY-MM-DD")
    if payload.ai_paused is not None:
        b.ai_paused = payload.ai_paused
        changed.append("AI paused" if payload.ai_paused else "AI resumed")
    if changed:
        b.last_interaction_at = now if (payload.contact_verified or payload.stated_buying_status) else \
            b.last_interaction_at
        svc.log_event(db, org_id, "buyer.facts_recorded", actor_type=ACTOR_USER, actor_user_id=user.id,
                      summary="Buyer facts recorded for %s: %s" % (ENG._name(b), ", ".join(changed)),
                      details={"buyer_id": b.id, "changed": changed})
    db.commit()
    return ENG.detail(db, org_id, b.id)


@router.post("/buyers/{buyer_id}/buy-boxes/{box_id}/confirm")
def confirm_box(buyer_id: str, box_id: str, db: Session = Depends(get_db),
                user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    b = _buyer(db, org_id, buyer_id)
    box = db.query(WholesaleBuyBox).filter(WholesaleBuyBox.id == box_id, WholesaleBuyBox.buyer_id == b.id,
                                           WholesaleBuyBox.organization_id == org_id).first()
    if box is None:
        raise HTTPException(status_code=404, detail="Buy box not found")
    box.confirmed_at, box.confirmed_by = datetime.utcnow(), (user.full_name or user.email or user.id)[:120]
    if not box.source or box.source == "county_records":
        box.source = "staff"
    svc.log_event(db, org_id, "buyer.buy_box_confirmed", actor_type=ACTOR_USER, actor_user_id=user.id,
                  summary="Buy box confirmed with %s" % ENG._name(b), details={"buyer_id": b.id, "box_id": box.id})
    db.commit()
    return ENG.detail(db, org_id, b.id)


@router.post("/buyers/{buyer_id}/evidence")
def add_evidence(buyer_id: str, payload: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    b = _buyer(db, org_id, buyer_id)
    row = EVD.add_evidence(db, org_id, b, payload, user)
    svc.log_event(db, org_id, "buyer.evidence_added", actor_type=ACTOR_USER, actor_user_id=user.id,
                  summary="%s recorded for %s" % (row.evidence_class.replace("_", " ").title(), ENG._name(b)),
                  details={"buyer_id": b.id, "evidence_id": row.id})
    db.commit()
    return ENG.detail(db, org_id, b.id)


@router.post("/buyers/{buyer_id}/request-qualification")
def request_qualification(buyer_id: str, db: Session = Depends(get_db),
                          user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    from app.services import wholesale_exceptions as EX
    org_id = svc.write_org_id(db, user)
    b = _buyer(db, org_id, buyer_id)
    if b.do_not_contact:
        raise HTTPException(status_code=409, detail="This buyer asked not to be contacted.")
    ex, new = EX.raise_exception(db, org_id, kind="buyer_criteria_verification", subject_type="buyer",
                                 subject_id=b.id, title="Qualify buyer: %s" % ENG._name(b),
                                 detail="Confirm they are buying now, what they buy (types, areas, price, units), "
                                        "funding and who decides.", priority=40, source="buyer_intelligence",
                                 user=user, is_test=bool(b.is_test))
    db.commit()
    return {"exception_id": ex.id, "created": new}


@router.post("/buyers/{buyer_id}/opt-out")
def opt_out(buyer_id: str, payload: Dict[str, Any] = Body(default={}), db: Session = Depends(get_db),
            user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    b = _buyer(db, org_id, buyer_id)
    b.do_not_contact = True
    b.do_not_contact_reason = (payload.get("reason") or "Asked not to be contacted")[:200]
    from app.models.wholesale_buyer_engine_models import CONV_OPEN, CONV_STOPPED, WholesaleBuyerConversation
    for c in db.query(WholesaleBuyerConversation).filter(WholesaleBuyerConversation.buyer_id == b.id,
                                                         WholesaleBuyerConversation.status == CONV_OPEN).all():
        c.status = CONV_STOPPED
    svc.log_event(db, org_id, "buyer.opted_out", actor_type=ACTOR_USER, actor_user_id=user.id,
                  summary="%s marked do-not-contact" % ENG._name(b), details={"buyer_id": b.id})
    db.commit()
    return {"buyer_id": b.id, "do_not_contact": True}


@router.post("/buyers/{buyer_id}/unlink")
def unlink(buyer_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
           _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    r = EVD.unlink(db, org_id, buyer_id, user)
    db.commit()
    return r


@router.get("/identity-review")
def identity_review(include_test: bool = False, db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_or_observer)):
    return {"items": EVD.review_queue(db, _read(db, user), include_test=include_test),
            "rule": "Similar names or a shared address are never merged automatically. 'Same investor' links the "
                    "records (nothing is deleted); 'different' is remembered and not asked again."}


@router.post("/identity-review/{entity_id}")
def identity_decide(entity_id: str, payload: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    r = EVD.decide(db, org_id, entity_id, str(payload.get("decision") or ""), user)
    svc.log_event(db, org_id, "buyer.identity_decided", actor_type=ACTOR_USER, actor_user_id=user.id,
                  summary="Identity review: %s" % r["status"], details=r)
    db.commit()
    return r


class RefreshIn(BaseModel):
    county: str = "dallas"


@router.get("/evidence/status")
def evidence_status(county: str = "dallas", user: User = Depends(require_tenant_or_observer)):
    if county not in finder.COUNTIES:
        raise HTTPException(status_code=400, detail="County must be one of: %s" % ", ".join(finder.COUNTIES))
    res = finder.load(county) or {}
    return {"county": county, "status": finder.status(county), "has_deed_records": res.get("format", 1) >= 2,
            "generated_at": res.get("generated_at"), "source": res.get("source")}


@router.post("/evidence/refresh")
def evidence_refresh(payload: RefreshIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
                     _g: User = Depends(require_not_observation)):
    """Attach the county's recorded deeds to the buyers already in the list.
    Free public records only. If the last scan predates deed-level records, a
    new scan starts (a few minutes); call again when it is done."""
    if payload.county not in finder.COUNTIES:
        raise HTTPException(status_code=400, detail="County must be one of: %s" % ", ".join(finder.COUNTIES))
    org_id = svc.write_org_id(db, user)
    res = finder.load(payload.county) or {}
    if res.get("format", 1) < 2:
        st = finder.status(payload.county)
        if st.get("state") != "running":
            st = finder.start(payload.county)
        return {"started_scan": True, "status": st,
                "message": "Reading the %s County appraisal file for deed-level records (a few minutes). "
                           "Run this again when it finishes." % payload.county.title()}
    r = EVD.refresh_county(db, org_id, payload.county, res)
    r["closed_deals_recorded"] = EVD.sync_closed_deals(db, org_id)
    svc.log_event(db, org_id, "buyers.evidence_refreshed", actor_type=ACTOR_USER, actor_user_id=user.id,
                  summary="%d recorded deed(s) attached to %d buyer(s) from %s County records"
                          % (r["deeds_added"], r["attached_buyers"], payload.county.title()), details=r)
    db.commit()
    return {"started_scan": False, **r}


@router.post("/evidence/sync-deals")
def sync_deals(db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
               _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    n = EVD.sync_closed_deals(db, org_id)
    db.commit()
    return {"verified_purchases_added": n}


@router.get("/match-candidates")
def match_candidates(q: Optional[str] = Query(None, max_length=80), include_test: bool = False,
                     db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    """Properties you can find buyers for: open deals, then discoveries in work order."""
    from sqlalchemy import or_

    from app.models.evosense_models import EvoSenseProperty
    from app.models.wholesale_models import WholesaleDeal, WholesaleProperty
    org_id = _read(db, user)
    deals = (db.query(WholesaleDeal, WholesaleProperty)
             .join(WholesaleProperty, WholesaleProperty.id == WholesaleDeal.property_id)
             .filter(WholesaleDeal.organization_id == org_id, WholesaleDeal.stage.notin_(("closed", "dead"))))
    evo = db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org_id,
                                            EvoSenseProperty.promoted_deal_id.is_(None))
    if not include_test:
        deals = deals.filter(WholesaleDeal.is_test.is_(False))
        evo = evo.filter(EvoSenseProperty.is_test.is_(False))
    if q:
        like = "%%%s%%" % q.strip()
        deals = deals.filter(or_(WholesaleProperty.street_address.ilike(like), WholesaleProperty.zip_code.ilike(like),
                                 WholesaleProperty.city.ilike(like)))
        evo = evo.filter(or_(EvoSenseProperty.street_address.ilike(like), EvoSenseProperty.zip_code.ilike(like),
                             EvoSenseProperty.city.ilike(like)))
    else:
        evo = evo.filter(EvoSenseProperty.work_priority.in_(("CRITICAL", "REVIEW_URGENT", "HIGH")))
    evo = evo.order_by(EvoSenseProperty.work_priority_rank.asc().nullslast(),
                       EvoSenseProperty.opportunity_score.desc().nullslast()).limit(25).all()
    out = [{"type": "deal", "id": d.id, "label": ", ".join(x for x in (p.street_address, p.city, p.zip_code) if x),
            "category": p.property_category or p.property_type, "stage": d.stage, "is_test": bool(d.is_test)}
           for d, p in deals.order_by(WholesaleDeal.updated_at.desc()).limit(25).all()]
    out += [{"type": "evosense", "id": e.id,
             "label": ", ".join(x for x in (e.street_address, e.city, e.zip_code) if x),
             "category": e.property_category or e.property_type, "work_priority": e.work_priority,
             "is_test": bool(e.is_test)} for e in evo]
    return {"items": out}


class MatchIn(BaseModel):
    subject_type: str
    subject_id: str
    asking_price: Optional[float] = None
    include_disqualified: bool = True


@router.post("/match")
def match(payload: MatchIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    """Score every buyer against one property. Reads only - nothing is stored or sent."""
    return BM.match(db, _read(db, user), payload.subject_type, payload.subject_id,
                    asking_price=payload.asking_price, include_disqualified=payload.include_disqualified)


class SaveIn(BaseModel):
    deal_id: str
    buyer_id: str


@router.post("/match/save")
def match_save(payload: SaveIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
               _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    r = BM.save(db, org_id, payload.deal_id, payload.buyer_id, user)
    db.commit()
    return r


class PrepareIn(BaseModel):
    evosense_id: str


@router.post("/match/prepare")
def match_prepare(payload: PrepareIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
                  _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    r = BM.prepare(db, org_id, payload.evosense_id, user)
    db.commit()
    return r


@router.get("/demand")
def demand(segment: Optional[str] = None, only_verified: bool = True, include_test: bool = False,
           db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    return BM.demand(db, _read(db, user), segment=segment, only_verified=only_verified, include_test=include_test)


# ── AI qualification ───────────────────────────────────────────────────────

@router.post("/practice-buyer")
def practice_buyer(db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
                   _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    b = Q.create_practice_buyer(db, org_id, user)
    db.commit()
    return {"buyer_id": b.id, "name": b.company_name, "is_test": True}


class QualifyIn(BaseModel):
    mode: str = "practice"


@router.post("/buyers/{buyer_id}/qualify")
def qualify(buyer_id: str, payload: QualifyIn, db: Session = Depends(get_db),
            user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    c = Q.start(db, org_id, buyer_id, user, mode=payload.mode)
    db.commit()
    return ENG.conv_json(c)


class TextIn(BaseModel):
    text: str


@router.post("/conversations/{conv_id}/reply")
def conv_reply(conv_id: str, payload: TextIn, db: Session = Depends(get_db),
               user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    r = Q.reply(db, org_id, conv_id, payload.text, user)
    db.commit()
    from app.models.wholesale_buyer_engine_models import WholesaleBuyerConversation
    c = db.query(WholesaleBuyerConversation).filter(WholesaleBuyerConversation.id == conv_id).first()
    return {"result": r, "conversation": ENG.conv_json(c)}


def _conv_action(fn):
    def handler(conv_id: str, payload: Dict[str, Any] = Body(default={}), db: Session = Depends(get_db),
                user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
        org_id = svc.write_org_id(db, user)
        r = fn(db, org_id, conv_id, user, payload)
        db.commit()
        from app.models.wholesale_buyer_engine_models import WholesaleBuyerConversation
        c = db.query(WholesaleBuyerConversation).filter(WholesaleBuyerConversation.id == conv_id).first()
        return {"result": r, "conversation": ENG.conv_json(c)}
    return handler


def _followup(db, org_id, conv_id, user, payload):
    c = Q._conv(db, org_id, conv_id)
    b = Q._buyer(db, org_id, c.buyer_id)
    due = None
    if payload.get("due_at"):
        try:
            due = datetime.fromisoformat(str(payload["due_at"])[:19])
        except ValueError:
            raise HTTPException(status_code=400, detail="due_at must be an ISO date-time")
    ex = Q.followup(db, org_id, b, c, (payload.get("what") or "Follow up with the buyer")[:120], due, user,
                    quote=payload.get("note"))
    Q._turn(c, "system", "Follow-up scheduled%s." % (" for %s" % due.isoformat()[:16] if due else ""))
    return {"exception_id": ex.id}


def _schedule(db, org_id, conv_id, user, payload):
    """A call goes on the work queue with a due time; a property walkthrough is a
    real appointment on the existing Appointments calendar (tied to a deal)."""
    c = Q._conv(db, org_id, conv_id)
    b = Q._buyer(db, org_id, c.buyer_id)
    if b.do_not_contact:
        raise HTTPException(status_code=409, detail="This buyer asked not to be contacted.")
    kind = payload.get("kind") or "call"
    if kind == "call":
        due = None
        if payload.get("starts_at"):
            from app.services.wholesale_appointments import parse_when
            due = parse_when(payload["starts_at"])
        ex = Q.followup(db, org_id, b, c, "Call the buyer", due, user, quote=payload.get("note"))
        Q._turn(c, "system", "Call scheduled%s." % (" for %s" % due.isoformat()[:16] if due else ""))
        return {"kind": "call", "exception_id": ex.id}
    if kind == "walkthrough":
        from app.services import wholesale_appointments as APPT
        if not payload.get("deal_id"):
            raise HTTPException(status_code=422, detail="A walkthrough is for a property - choose the deal.")
        a = APPT.book(db, org_id, user, {"deal_id": payload["deal_id"], "starts_at": payload.get("starts_at"),
                                         "duration_minutes": payload.get("duration_minutes") or 30,
                                         "title": "Buyer walkthrough - %s" % ENG._name(b),
                                         "notes": "Buyer: %s. %s" % (ENG._name(b), payload.get("note") or "")})
        Q._turn(c, "system", "Walkthrough booked on Appointments for %s." % a.starts_at.isoformat()[:16])
        return {"kind": "walkthrough", "appointment_id": a.id}
    raise HTTPException(status_code=400, detail="kind must be call or walkthrough")


router.post("/conversations/{conv_id}/pause")(_conv_action(lambda db, o, c, u, p: Q.pause(db, o, c, u)))
router.post("/conversations/{conv_id}/resume")(_conv_action(lambda db, o, c, u, p: Q.resume(db, o, c, u)))
router.post("/conversations/{conv_id}/takeover")(
    _conv_action(lambda db, o, c, u, p: Q.takeover(db, o, c, u, note=p.get("note"))))
router.post("/conversations/{conv_id}/note")(
    _conv_action(lambda db, o, c, u, p: Q.staff_note(db, o, c, p.get("text") or "", u)))
router.post("/conversations/{conv_id}/followup")(_conv_action(_followup))
router.post("/conversations/{conv_id}/schedule")(_conv_action(_schedule))
