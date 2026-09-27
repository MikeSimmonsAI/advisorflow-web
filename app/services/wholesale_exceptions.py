"""The exception queue: AI + automation handle volume, a VA handles exceptions,
the owner handles judgement.

    raise_exception   open one exception (idempotent per open kind + subject)
    sweep             raise the exceptions the data already shows (idempotent)
    queue             what THIS user should work: a VA sees what is assigned to
                      them; an org admin sees the whole queue and escalations
    assign / resolve  with one of four outcomes: complete, unable_to_verify,
                      needs_more_info, escalate (-> back to the owner)

LEAST PRIVILEGE. A queue item carries just enough of its subject to do the job
(an address, a buyer's name and what is missing) - never the whole record, and
never another organization's. Every change is a wholesale event.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.wholesale_models import (ACTOR_SYSTEM, ACTOR_USER, EXCEPTION_KINDS,
                                         EXCEPTION_OPEN_STATUSES, EXCEPTION_OUTCOMES,
                                         WholesaleBuyBox, WholesaleBuyer, WholesaleBuyerOutreach,
                                         WholesaleProperty, WholesaleSellerProfile,
                                         WholesaleWorkException)
from app.services import wholesale_service as svc

KIND_LABELS = {
    "verify_owner": "Verify owner", "verify_contact": "Verify phone / email",
    "property_research": "Property research", "bad_or_missing_data": "Bad or missing data",
    "missing_photos_docs": "Missing photos / documents",
    "buyer_criteria_verification": "Verify buyer criteria",
    "proof_of_funds_verification": "Verify proof of funds", "buyer_research": "Buyer research",
    "missing_disposition_data": "Missing disposition data",
    "seller_callback_requested": "Seller callback requested", "ai_exception": "AI exception",
    "title_ownership_anomaly": "Title / ownership anomaly", "escalate_to_owner": "Escalated to owner",
}
OUTCOME_STATUS = {"complete": "complete", "unable_to_verify": "unable_to_verify",
                  "needs_more_info": "needs_more_info", "escalate": "escalated"}


def is_manager(db: Session, user) -> bool:
    if getattr(user, "role", None) in ("god_admin", "super_admin"):
        return True
    from app.services.lead_scope import effective_role
    return effective_role(user, db) == "org_admin"


def raise_exception(db: Session, org_id: str, *, kind: str, subject_type: str, subject_id: str,
                    title: str, detail: Optional[str] = None, priority: int = 50,
                    source: Optional[str] = None, user=None, is_test: bool = False,
                    assigned_to_id: Optional[str] = None):
    """Open one exception. If the same kind is already open for the same subject,
    return that one (a sweep can run every hour without piling up duplicates)."""
    if kind not in EXCEPTION_KINDS:
        raise HTTPException(422, "Unknown exception kind.")
    existing = (db.query(WholesaleWorkException)
                .filter(WholesaleWorkException.organization_id == org_id,
                        WholesaleWorkException.kind == kind,
                        WholesaleWorkException.subject_type == subject_type,
                        WholesaleWorkException.subject_id == subject_id,
                        WholesaleWorkException.status.in_(EXCEPTION_OPEN_STATUSES)).first())
    if existing is not None:
        return existing, False
    ex = WholesaleWorkException(organization_id=org_id, kind=kind, subject_type=subject_type,
                                subject_id=subject_id, title=title[:250], detail=detail,
                                priority=priority, source=source, is_test=is_test,
                                raised_by_actor=ACTOR_USER if user is not None else ACTOR_SYSTEM,
                                raised_by_id=getattr(user, "id", None))
    if assigned_to_id:
        ex.assigned_to_id, ex.assigned_at, ex.status = assigned_to_id, datetime.utcnow(), "assigned"
    db.add(ex)
    db.flush()
    svc.log_event(db, org_id, "exception.raised",
                  actor_type=ACTOR_USER if user is not None else ACTOR_SYSTEM,
                  actor_user_id=getattr(user, "id", None),
                  summary="Exception raised: %s - %s" % (KIND_LABELS[kind], ex.title),
                  details={"exception_id": ex.id, "kind": kind, "subject_type": subject_type,
                           "subject_id": subject_id, "source": source})
    return ex, True


def sweep(db: Session, org_id: str, user=None) -> Dict[str, int]:
    """Raise what the data already shows needs a person. Idempotent."""
    made = {}

    def bump(kind, created):
        if created:
            made[kind] = made.get(kind, 0) + 1

    # Properties with no owner on file.
    props = (db.query(WholesaleProperty).filter(WholesaleProperty.organization_id == org_id).all())
    with_seller = {r.property_id for r in db.query(WholesaleSellerProfile.property_id).filter(
        WholesaleSellerProfile.organization_id == org_id).all()}
    for p in props:
        addr = ", ".join(x for x in (p.street_address, p.city) if x)
        if p.id not in with_seller:
            bump("verify_owner", raise_exception(
                db, org_id, kind="verify_owner", subject_type="property", subject_id=p.id,
                title="Find and verify the owner of %s" % addr,
                detail="No owner or seller is attached to this property.",
                priority=40, source="sweep:no_owner", user=user, is_test=bool(p.is_test))[1])
    # Buyers: proof of funds claimed but not checked; no buy box at all.
    buyers = db.query(WholesaleBuyer).filter(WholesaleBuyer.organization_id == org_id,
                                             WholesaleBuyer.is_active.is_(True)).all()
    claimed = {r.buyer_id for r in db.query(WholesaleBuyerOutreach.buyer_id).filter(
        WholesaleBuyerOutreach.organization_id == org_id,
        WholesaleBuyerOutreach.pof_status == "claimed").all()}
    boxed = {r.buyer_id for r in db.query(WholesaleBuyBox.buyer_id).filter(
        WholesaleBuyBox.organization_id == org_id, WholesaleBuyBox.is_active.is_(True)).all()}
    for b in buyers:
        name = b.company_name or b.contact_name or "(unnamed buyer)"
        if b.id in claimed and not b.proof_of_funds_on_file:
            bump("proof_of_funds_verification", raise_exception(
                db, org_id, kind="proof_of_funds_verification", subject_type="buyer", subject_id=b.id,
                title="Check proof of funds for %s" % name,
                detail="The buyer says proof of funds is with us; nobody has looked at a document.",
                priority=30, source="sweep:pof_claimed", user=user, is_test=bool(b.is_test))[1])
        if b.id not in boxed:
            bump("buyer_criteria_verification", raise_exception(
                db, org_id, kind="buyer_criteria_verification", subject_type="buyer", subject_id=b.id,
                title="Get %s's buy box" % name,
                detail="No active buy box: this buyer cannot be matched to a deal.",
                priority=60, source="sweep:no_buy_box", user=user, is_test=bool(b.is_test))[1])
    # Sellers who asked to be called back (reply classified CALLBACK) and have
    # not been reviewed - Wholesale seller leads only.
    from app.models.models import Lead, Reply, ReplyClassification
    seller_leads = {r.lead_id for r in db.query(WholesaleSellerProfile.lead_id).filter(
        WholesaleSellerProfile.organization_id == org_id).all() if r.lead_id}
    if seller_leads:
        for rep in (db.query(Reply).join(Lead, Lead.id == Reply.lead_id)
                    .filter(Lead.organization_id == org_id, Reply.lead_id.in_(seller_leads),
                            Reply.classification == ReplyClassification.CALLBACK,
                            Reply.reviewed_at.is_(None)).all()):
            lead = db.query(Lead).filter(Lead.id == rep.lead_id).first()
            who = " ".join(x for x in (getattr(lead, "first_name", None), getattr(lead, "last_name", None)) if x)
            bump("seller_callback_requested", raise_exception(
                db, org_id, kind="seller_callback_requested", subject_type="lead", subject_id=rep.lead_id,
                title="Call back %s" % (who or "the seller"),
                detail="They asked to be called back: \"%s\"" % (rep.body or "")[:300],
                priority=10, source="sweep:reply_callback", user=user,
                is_test=bool(getattr(lead, "is_test", False)))[1])
    # EvoSense owners of record whose title needs a person: estate, life
    # estate, ET AL, institutional, truncated name.
    from app.models.evosense_models import EvoSenseOwner, EvoSenseOwnership, EvoSenseProperty
    flagged = (db.query(EvoSenseOwner).filter(EvoSenseOwner.organization_id == org_id,
                                              EvoSenseOwner.review_flags.isnot(None)).all())
    for o in flagged:
        try:
            flags = [f for f in json.loads(o.review_flags or "[]") if f]
        except (TypeError, ValueError):
            flags = []
        if not flags:
            continue
        for link in db.query(EvoSenseOwnership).filter(EvoSenseOwnership.organization_id == org_id,
                                                        EvoSenseOwnership.owner_id == o.id,
                                                        EvoSenseOwnership.is_current.is_(True)).all():
            ep = db.query(EvoSenseProperty).filter(EvoSenseProperty.id == link.property_id,
                                                   EvoSenseProperty.organization_id == org_id).first()
            if ep is None:
                continue
            bump("title_ownership_anomaly", raise_exception(
                db, org_id, kind="title_ownership_anomaly", subject_type="evosense_property",
                subject_id=ep.id, title="Check title / ownership: %s" % (ep.street_address or ""),
                detail="Owner of record %s is flagged: %s." % (o.display_name or "(unnamed)",
                                                               ", ".join(sorted(flags))),
                priority=45, source="sweep:owner_review_flags", user=user, is_test=bool(ep.is_test))[1])
    return made


def _subject(db: Session, org_id: str, ex: WholesaleWorkException) -> Dict[str, Any]:
    """Just enough of the subject to do the job."""
    if ex.subject_type == "property":
        p = db.query(WholesaleProperty).filter(WholesaleProperty.id == ex.subject_id,
                                               WholesaleProperty.organization_id == org_id).first()
        if p is not None:
            return {"type": "property", "label": ", ".join(x for x in (p.street_address, p.city,
                                                                           p.state, p.zip_code) if x),
                    "county": p.county, "parcel_apn": getattr(p, "parcel_apn", None)}
    if ex.subject_type == "buyer":
        b = db.query(WholesaleBuyer).filter(WholesaleBuyer.id == ex.subject_id,
                                            WholesaleBuyer.organization_id == org_id).first()
        if b is not None:
            return {"type": "buyer", "label": b.company_name or b.contact_name,
                    "contact_name": b.contact_name, "email": b.email, "phone": b.phone}
    if ex.subject_type == "evosense_property":
        from app.models.evosense_models import EvoSenseProperty
        p = db.query(EvoSenseProperty).filter(EvoSenseProperty.id == ex.subject_id,
                                               EvoSenseProperty.organization_id == org_id).first()
        if p is not None:
            return {"type": "evosense_property", "label": ", ".join(
                x for x in (p.street_address, p.city, p.state, p.zip_code) if x),
                "county": p.county, "parcel_apn": p.parcel_apn}
    if ex.subject_type == "lead":
        from app.models.models import Lead
        lead = db.query(Lead).filter(Lead.id == ex.subject_id, Lead.organization_id == org_id).first()
        if lead is not None:
            return {"type": "lead", "label": " ".join(x for x in (lead.first_name, lead.last_name) if x)
                    or "Seller", "phone": lead.phone}
    return {"type": ex.subject_type, "label": None}


def exception_json(db: Session, org_id: str, ex: WholesaleWorkException, names=None) -> Dict[str, Any]:
    iso = lambda d: d.isoformat() if d else None  # noqa: E731
    return {"id": ex.id, "kind": ex.kind, "kind_label": KIND_LABELS.get(ex.kind, ex.kind),
            "title": ex.title, "detail": ex.detail, "priority": ex.priority, "status": ex.status,
            "subject": _subject(db, org_id, ex), "assigned_to_id": ex.assigned_to_id,
            "assigned_to_name": (names or {}).get(ex.assigned_to_id), "assigned_at": iso(ex.assigned_at),
            "outcome": ex.outcome, "outcome_note": ex.outcome_note, "resolved_at": iso(ex.resolved_at),
            "source": ex.source, "raised_by_actor": ex.raised_by_actor, "created_at": iso(ex.created_at),
            "is_test": bool(ex.is_test)}


def get(db: Session, org_id: str, exception_id: str) -> WholesaleWorkException:
    ex = (db.query(WholesaleWorkException)
          .filter(WholesaleWorkException.id == exception_id,
                  WholesaleWorkException.organization_id == org_id).first())
    if ex is None:
        raise HTTPException(404, "Not found")
    return ex


def can_work(db: Session, user, ex: WholesaleWorkException) -> bool:
    return is_manager(db, user) or ex.assigned_to_id == user.id


def queue(db: Session, org_id: str, user, *, include_closed: bool = False,
          scope: str = "mine") -> List[WholesaleWorkException]:
    q = db.query(WholesaleWorkException).filter(WholesaleWorkException.organization_id == org_id)
    if not include_closed:
        q = q.filter(WholesaleWorkException.status.in_(EXCEPTION_OPEN_STATUSES))
    manager = is_manager(db, user)
    if not manager or scope == "mine":
        q = q.filter(WholesaleWorkException.assigned_to_id == user.id)
    elif scope == "escalated":
        q = q.filter(WholesaleWorkException.status == "escalated")
    elif scope == "unassigned":
        q = q.filter(WholesaleWorkException.assigned_to_id.is_(None))
    return q.order_by(WholesaleWorkException.priority, WholesaleWorkException.created_at).all()


def assign(db: Session, org_id: str, ex: WholesaleWorkException, user, assignee_id: Optional[str]):
    if not is_manager(db, user):
        raise HTTPException(403, "Only an administrator assigns exceptions.")
    if assignee_id:
        from app.models.models import User
        from app.services.lead_scope import active_workspace_org_id  # noqa: F401
        target = db.query(User).filter(User.id == assignee_id).first()
        if target is None or not _in_org(db, target, org_id):
            raise HTTPException(422, "That person is not in this workspace.")
    before = ex.assigned_to_id
    ex.assigned_to_id = assignee_id or None
    ex.assigned_at = datetime.utcnow() if assignee_id else None
    if ex.status in ("open", "assigned", "escalated"):
        ex.status = "assigned" if assignee_id else "open"
    svc.log_event(db, org_id, "exception.assigned", actor_type=ACTOR_USER, actor_user_id=user.id,
                  summary="Exception assigned: %s" % ex.title,
                  before={"assigned_to_id": before}, after={"assigned_to_id": ex.assigned_to_id},
                  details={"exception_id": ex.id})
    return ex


def _in_org(db, user, org_id) -> bool:
    if getattr(user, "organization_id", None) == org_id:
        return True
    try:
        from app.services.workspace_access import workspace_role
        return workspace_role(user, db, org_id) is not None
    except Exception:  # noqa: BLE001
        return False


def resolve(db: Session, org_id: str, ex: WholesaleWorkException, user, *, outcome: str,
            note: Optional[str] = None):
    if outcome not in EXCEPTION_OUTCOMES:
        raise HTTPException(422, "Outcome must be one of: %s" % ", ".join(EXCEPTION_OUTCOMES))
    if not can_work(db, user, ex):
        raise HTTPException(404, "Not found")          # a VA cannot see another person's item
    if outcome in ("unable_to_verify", "needs_more_info", "escalate") and not (note or "").strip():
        raise HTTPException(422, "Say what you found - a note is required for this outcome.")
    before = ex.status
    ex.outcome, ex.outcome_note = outcome, (note or "").strip() or None
    ex.status = OUTCOME_STATUS[outcome]
    if outcome in ("complete", "unable_to_verify"):
        ex.resolved_at, ex.resolved_by_id = datetime.utcnow(), user.id
    if outcome == "escalate":
        ex.assigned_to_id, ex.assigned_at = None, None     # back to the owner's queue
    svc.log_event(db, org_id, "exception.resolved" if ex.resolved_at else "exception.updated",
                  actor_type=ACTOR_USER, actor_user_id=user.id,
                  summary="Exception %s: %s" % (outcome.replace("_", " "), ex.title),
                  before={"status": before}, after={"status": ex.status, "outcome": outcome},
                  details={"exception_id": ex.id, "note": ex.outcome_note})
    return ex
