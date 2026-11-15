"""CUSTOM DEMO REQUESTS — asked for on the deal, decided by a manager.

Book Demo (scheduling a walkthrough of an existing demo) is unchanged. This
is the other thing: the customer needs to see something specific.

  1. The salesperson asks, on the deal, saying what the customer needs to see.
     Discovery already on file is offered as the starting text. The deal's
     demo goes to "Waiting for a decision" (`pending_review`). That is NOT
     build work: it is not counted as a demo to build anywhere.
  2. A manager who leads that salesperson (God Mode / owners: anyone; the Sr
     EVP and Area Sales Managers: their own team; other brands: a sales
     manager) decides:
       - route it to an existing demo  -> the demo link is set, status Ready;
       - approve a custom build        -> a builder and a target date are set,
                                          status Requested (build work).
  3. Builders and managers work it from one queue under My Team: what needs a
     decision, what is assigned, what is overdue. Ready and delivered demos
     leave the outstanding count.

Everything is recorded on the deal's timeline. Every decision is re-checked
here on the server; hidden buttons in the browser are not the control.
"""
from __future__ import annotations

from datetime import date, datetime, time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models.models import User
from app.models.sales_models import (DEMO_DELIVERED, DEMO_IN_PROGRESS, DEMO_PENDING_REVIEW,
                                     DEMO_READY, DEMO_REQUESTED, ROLE_SALES_MANAGER, ROLE_SALES_REP,
                                     SCOPE_BRAND_SALES_ORG, STAGE_CONTACTED, STAGE_DEMO_BUILD,
                                     STAGE_DISCOVERY, STAGE_PROSPECT, BrandSalesOrg, DiscoveryRecord,
                                     Membership, Opportunity, OpportunityEvent, demo_is_outstanding)
from app.services import sales_team
from app.services.sales_access import (assert_can_edit_opportunity, assert_can_view_opportunity,
                                       is_sales_manager, require_sales_member, sales_org_ids)

router = APIRouter(prefix="/sales", tags=["sales-demo-requests"])

STATUS_LABELS = {
    None: "Not requested", "not_requested": "Not requested",
    DEMO_PENDING_REVIEW: "Waiting for a decision", DEMO_REQUESTED: "Approved — to build",
    DEMO_IN_PROGRESS: "Being built", DEMO_READY: "Ready", DEMO_DELIVERED: "Delivered",
}
DEMO_EVENT_TYPES = ("demo_requested_custom", "demo_routed_existing", "demo_build_approved",
                    "demo_status", "demo_request_sent_back")
EARLY_STAGES = (STAGE_PROSPECT, STAGE_CONTACTED, STAGE_DISCOVERY)


class RequestIn(BaseModel):
    needs: Optional[str] = None


class DecisionIn(BaseModel):
    action: str                         # route_existing | approve_build | send_back
    demo_site_id: Optional[str] = None
    builder_user_id: Optional[str] = None
    target_date: Optional[date] = None
    note: Optional[str] = None


# ── helpers ──────────────────────────────────────────────────────────────────

def _name(db: Session, uid: Optional[str]) -> Optional[str]:
    if not uid:
        return None
    u = db.query(User).filter(User.id == uid).first()
    return (u.full_name or u.email) if u else None


def _event(db: Session, opp: Opportunity, actor: User, kind: str, summary: str,
           detail: Optional[str] = None) -> None:
    db.add(OpportunityEvent(opportunity_id=opp.id, event_type=kind, summary=summary,
                            detail=detail, actor_user_id=actor.id))


def _load(db: Session, opp_id: str, user: User) -> Opportunity:
    opp = db.query(Opportunity).filter(Opportunity.id == opp_id).first()
    if opp is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    assert_can_view_opportunity(user, opp, db)
    return opp


def may_decide(db: Session, user: User, opp: Opportunity) -> bool:
    """A manager who leads this deal's salesperson."""
    if not is_sales_manager(user, db, opp.brand_sales_org_id):
        return False
    scope = sales_team.opp_scope(db, user, opp.brand_sales_org_id)
    if scope is None:
        return True
    # A leader decides for the people under him, not for his own deals.
    return bool(opp.owner_user_id) and opp.owner_user_id in scope and opp.owner_user_id != user.id


def _discovery_needs(db: Session, opp: Opportunity) -> str:
    d = db.query(DiscoveryRecord).filter(DiscoveryRecord.opportunity_id == opp.id).first()
    return ((d.demo_requirements if d else None) or "").strip()


def _builders(db: Session, brand_id: str) -> List[Dict[str, Any]]:
    rows = (db.query(Membership, User).join(User, User.id == Membership.user_id)
            .filter(Membership.scope_type == SCOPE_BRAND_SALES_ORG, Membership.scope_id == brand_id,
                    Membership.is_active.is_(True),
                    Membership.role.in_((ROLE_SALES_MANAGER, ROLE_SALES_REP))).all())
    out = {u.id: (u.full_name or u.email) for _, u in rows}
    return sorted([{"user_id": k, "name": v} for k, v in out.items()], key=lambda r: r["name"] or "")


def _existing_demos(db: Session, brand_id: str) -> List[Dict[str, Any]]:
    from app.models.demo_site_models import DemoSite
    from app.services import demo_sites as _demos
    from app.services.appointment_invites import PUBLIC_BASE_URL, brand_identity_for_brand
    base = brand_identity_for_brand(db, brand_id).get("app_base_url") or PUBLIC_BASE_URL
    now = datetime.utcnow()
    rows = (db.query(DemoSite, Opportunity)
            .outerjoin(Opportunity, Opportunity.id == DemoSite.opportunity_id)
            .filter(DemoSite.brand_sales_org_id == brand_id, DemoSite.is_active.is_(True),
                    DemoSite.revoked_at.is_(None),
                    or_(DemoSite.expires_at.is_(None), DemoSite.expires_at > now))
            .order_by(DemoSite.created_at.desc()).limit(50).all())
    out = []
    for site, o in rows:
        info = _demos.out(site, base)
        out.append({"id": site.id, "title": site.title, "for_company": o.company_name if o else None,
                    "url": info.get("url")})
    return out


def _history(db: Session, opp: Opportunity) -> List[Dict[str, Any]]:
    evs = (db.query(OpportunityEvent)
           .filter(OpportunityEvent.opportunity_id == opp.id,
                   OpportunityEvent.event_type.in_(DEMO_EVENT_TYPES))
           .order_by(OpportunityEvent.occurred_at.desc()).limit(40).all())
    return [{"type": e.event_type, "summary": e.summary, "detail": e.detail,
             "at": e.occurred_at.isoformat() + "Z" if e.occurred_at else None,
             "by": _name(db, e.actor_user_id)} for e in evs]


def _request_out(db: Session, opp: Opportunity, user: User) -> Dict[str, Any]:
    decide = may_decide(db, user, opp)
    st = opp.demo_status
    return {
        "opportunity_id": opp.id, "company_name": opp.company_name,
        "salesperson": _name(db, opp.owner_user_id),
        "status": st, "status_label": STATUS_LABELS.get(st, st),
        "needs": opp.demo_requirements, "discovery_needs": _discovery_needs(db, opp),
        "requested_at": opp.demo_requested_at.isoformat() + "Z" if opp.demo_requested_at else None,
        "builder_user_id": opp.demo_owner_user_id, "builder": _name(db, opp.demo_owner_user_id),
        "target_at": opp.demo_due_at.isoformat() + "Z" if opp.demo_due_at else None,
        "demo_url": opp.demo_url,
        "can_request": st in (None, "not_requested", DEMO_READY, DEMO_DELIVERED) and opp.status == "open",
        "can_decide": decide and st == DEMO_PENDING_REVIEW,
        "builders": _builders(db, opp.brand_sales_org_id) if decide else [],
        "existing_demos": _existing_demos(db, opp.brand_sales_org_id) if decide and st == DEMO_PENDING_REVIEW else [],
        "history": _history(db, opp),
    }


# ── on the deal ──────────────────────────────────────────────────────────────

@router.get("/opportunities/{opp_id}/demo-request")
def get_request(opp_id: str, user: User = Depends(require_sales_member), db: Session = Depends(get_db)):
    return _request_out(db, _load(db, opp_id, user), user)


@router.post("/opportunities/{opp_id}/demo-request")
def create_request(opp_id: str, body: RequestIn, user: User = Depends(require_sales_member),
                   db: Session = Depends(get_db)):
    opp = _load(db, opp_id, user)
    assert_can_edit_opportunity(user, opp, db)
    if opp.status != "open":
        raise HTTPException(status_code=409, detail="This deal is closed.")
    if opp.demo_status == DEMO_PENDING_REVIEW:
        raise HTTPException(status_code=409, detail="A custom demo request is already waiting for a decision.")
    if opp.demo_status in (DEMO_REQUESTED, DEMO_IN_PROGRESS) or demo_is_outstanding(opp):
        raise HTTPException(status_code=409, detail="A demo is already being built for this deal.")
    needs = (body.needs or "").strip() or _discovery_needs(db, opp) or (opp.demo_requirements or "").strip()
    if not needs:
        raise HTTPException(status_code=422, detail="Say what the customer needs to see.")
    opp.demo_requirements = needs[:4000]
    opp.demo_status = DEMO_PENDING_REVIEW
    opp.demo_requested_at = datetime.utcnow()
    opp.demo_ready_at = None
    _event(db, opp, user, "demo_requested_custom", "Custom demo requested — waiting for a decision",
           needs[:500])
    db.commit()
    return _request_out(db, opp, user)


@router.post("/opportunities/{opp_id}/demo-request/decision")
def decide(opp_id: str, body: DecisionIn, user: User = Depends(require_sales_member),
           db: Session = Depends(get_db)):
    opp = _load(db, opp_id, user)
    if not may_decide(db, user, opp):
        raise HTTPException(status_code=403,
                            detail="Only a manager who leads this salesperson can decide this request.")
    if opp.demo_status != DEMO_PENDING_REVIEW:
        raise HTTPException(status_code=409, detail="This request is not waiting for a decision.")
    note = (body.note or "").strip()[:1000] or None
    now = datetime.utcnow()

    if body.action == "route_existing":
        from app.models.demo_site_models import DemoSite
        site = db.query(DemoSite).filter(DemoSite.id == body.demo_site_id).first() if body.demo_site_id else None
        listed = {d["id"]: d for d in _existing_demos(db, opp.brand_sales_org_id)}
        if site is None or site.id not in listed:
            raise HTTPException(status_code=422, detail="Choose an existing demo from this brand.")
        opp.demo_url = listed[site.id]["url"]
        opp.demo_status = DEMO_READY
        opp.demo_ready_at = now
        opp.demo_owner_user_id = None
        opp.demo_due_at = None
        _event(db, opp, user, "demo_routed_existing", "Routed to an existing demo: %s" % site.title, note)
    elif body.action == "approve_build":
        builders = {b["user_id"] for b in _builders(db, opp.brand_sales_org_id)}
        if not body.builder_user_id or body.builder_user_id not in builders:
            raise HTTPException(status_code=422, detail="Choose a builder on this sales team.")
        if not body.target_date:
            raise HTTPException(status_code=422, detail="Set a target date.")
        if body.target_date < date.today():
            raise HTTPException(status_code=422, detail="The target date is in the past.")
        opp.demo_status = DEMO_REQUESTED
        opp.demo_owner_user_id = body.builder_user_id
        opp.demo_due_at = datetime.combine(body.target_date, time(17, 0))
        if opp.stage in EARLY_STAGES:
            opp.stage = STAGE_DEMO_BUILD
            opp.stage_changed_at = now
        _event(db, opp, user, "demo_build_approved",
               "Custom build approved — %s, target %s" % (_name(db, body.builder_user_id),
                                                         body.target_date.strftime("%b %d, %Y")), note)
    elif body.action == "send_back":
        if not note:
            raise HTTPException(status_code=422, detail="Say what is missing.")
        opp.demo_status = "not_requested"
        _event(db, opp, user, "demo_request_sent_back", "Custom demo request sent back", note)
    else:
        raise HTTPException(status_code=422, detail="Unknown decision.")
    db.commit()
    return _request_out(db, opp, user)


class ProgressIn(BaseModel):
    status: str                     # in_progress | ready | delivered
    demo_url: Optional[str] = None


@router.post("/opportunities/{opp_id}/demo-request/progress")
def progress(opp_id: str, body: ProgressIn, user: User = Depends(require_sales_member),
             db: Session = Depends(get_db)):
    """The assigned builder (or a manager who leads the deal) moves an
    APPROVED build along. A builder works on other people's deals, so this is
    allowed by assignment, not by owning the deal."""
    opp = db.query(Opportunity).filter(Opportunity.id == opp_id).first()
    if opp is None or opp.brand_sales_org_id not in (sales_org_ids(user, db) or []):
        raise HTTPException(status_code=404, detail="Opportunity not found")
    if not (opp.demo_owner_user_id == user.id or may_decide(db, user, opp)):
        raise HTTPException(status_code=403, detail="Only the assigned builder or the deal's manager can update this build.")
    if opp.demo_status not in (DEMO_REQUESTED, DEMO_IN_PROGRESS, DEMO_READY):
        raise HTTPException(status_code=409, detail="There is no approved build on this deal.")
    if body.status not in (DEMO_IN_PROGRESS, DEMO_READY, DEMO_DELIVERED):
        raise HTTPException(status_code=422, detail="Unknown status.")
    url = (body.demo_url or "").strip()
    if url:
        if not url.startswith("https://") or len(url) > 500:
            raise HTTPException(status_code=422, detail="The demo link must be an https:// address.")
        opp.demo_url = url
    if body.status in (DEMO_READY, DEMO_DELIVERED) and not opp.demo_url:
        raise HTTPException(status_code=422, detail="Add the demo link before marking it ready.")
    opp.demo_status = body.status
    if body.status == DEMO_READY and not opp.demo_ready_at:
        opp.demo_ready_at = datetime.utcnow()
    _event(db, opp, user, "demo_status", "Demo status: %s" % STATUS_LABELS.get(body.status, body.status))
    db.commit()
    return {"status": opp.demo_status, "status_label": STATUS_LABELS.get(opp.demo_status),
            "demo_url": opp.demo_url}


# ── the queue under My Team ──────────────────────────────────────────────────

def _item(db: Session, o: Opportunity, now: datetime, names: Dict[str, str]) -> Dict[str, Any]:
    return {"opportunity_id": o.id, "company_name": o.company_name,
            "salesperson": names.get(o.owner_user_id), "status": o.demo_status,
            "status_label": STATUS_LABELS.get(o.demo_status, o.demo_status),
            "needs": (o.demo_requirements or "")[:240],
            "requested_at": o.demo_requested_at.isoformat() + "Z" if o.demo_requested_at else None,
            "builder": names.get(o.demo_owner_user_id), "builder_user_id": o.demo_owner_user_id,
            "target_at": o.demo_due_at.isoformat() + "Z" if o.demo_due_at else None,
            "overdue": bool(o.demo_due_at and o.demo_due_at < now and demo_is_outstanding(o)),
            "demo_url": o.demo_url, "href": "/sales/opportunities/%s" % o.id}


@router.get("/demo-requests")
def queue(brand_sales_org_id: Optional[str] = Query(None),
          user: User = Depends(require_sales_member), db: Session = Depends(get_db)):
    brand_ids = sales_org_ids(user, db) or []
    if brand_sales_org_id:
        if brand_sales_org_id not in brand_ids:
            raise HTTPException(status_code=404, detail="Not found")
        brand_ids = [brand_sales_org_id]
    now = datetime.utcnow()
    review, assigned, overdue, mine, recently_done = [], [], [], [], []
    leads_any = False
    for bid in brand_ids:
        manager = is_sales_manager(user, db, bid)
        scope = sales_team.opp_scope(db, user, bid) if manager else {user.id}
        leads = manager and (scope is None or len(scope) > 1)
        leads_any = leads_any or leads
        q = db.query(Opportunity).filter(Opportunity.brand_sales_org_id == bid,
                                         Opportunity.status == "open")
        if scope is not None:
            q = q.filter(or_(Opportunity.owner_user_id.in_(scope),
                             Opportunity.demo_owner_user_id == user.id))
        opps = q.all()
        names: Dict[str, str] = {}
        for uid in {x for o in opps for x in (o.owner_user_id, o.demo_owner_user_id) if x}:
            names[uid] = _name(db, uid)
        for o in opps:
            in_scope = scope is None or (o.owner_user_id in (scope or ()))
            it = _item(db, o, now, names)
            decides = leads and in_scope and (scope is None or o.owner_user_id != user.id)
            if o.demo_status == DEMO_PENDING_REVIEW and decides:
                review.append(it)
            if demo_is_outstanding(o) and ((leads and in_scope) or o.demo_owner_user_id == user.id):
                assigned.append(it)
                if it["overdue"]:
                    overdue.append(it)
            if o.owner_user_id == user.id and o.demo_status not in (None, "not_requested"):
                mine.append(it)
            if (o.demo_status in (DEMO_READY, DEMO_DELIVERED) and o.demo_ready_at
                    and (now - o.demo_ready_at).days <= 14
                    and ((leads and in_scope) or o.demo_owner_user_id == user.id)):
                recently_done.append(it)
    review.sort(key=lambda i: i["requested_at"] or "")
    assigned.sort(key=lambda i: (i["target_at"] or "9999", i["requested_at"] or ""))
    return {"summary": {"needs_review": len(review), "assigned": len(assigned), "overdue": len(overdue)},
            "needs_review": review, "assigned": assigned, "overdue": overdue,
            "my_requests": mine, "recently_ready": recently_done[:20], "is_manager": leads_any}
