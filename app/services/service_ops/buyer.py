"""The OPTIONAL buyer portal (a service company's own customer - e.g. a
property manager - sees its own locations, visits, records, estimates and
invoices) and the multi-service BUYER NETWORK foundation.

A buyer login is a platform User homed in the service company with a
ServiceBuyerAccess row scoped to ONE ServiceCustomer (optionally to some of
its locations). It never reaches the Acquisition (sales) portal, the team
screens, or any other customer's records. Invitations return a one-time
setup link; nothing is ever emailed automatically.
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import Organization, User
from app.models.service_ops_models import (ServiceBuyerAccess, ServiceBuyerNetworkRequest, ServiceCustomer,
                                           ServiceEstimate, ServiceInvoice, ServiceJob, ServiceJobAttachment,
                                           ServiceLocation, ServiceOpsSettings, ServiceRequest, ServiceTeamMember)
from app.services.service_ops import billing as BL
from app.services.service_ops import core as K
from app.services.service_ops import jobs as J

REQUEST_KINDS = ("new_service", "schedule_change", "issue", "help")
REQUEST_LABEL = {"new_service": "Request service", "schedule_change": "Schedule change", "issue": "Report an issue",
                 "help": "Question"}
BUYER_JOB_STATUSES = ("scheduled", "assigned", "confirmed", "in_progress", "completed", "customer_review",
                      "approved", "cancelled", "needs_attention")


class BuyerCtx:
    def __init__(self, db: Session, user: User, org: Organization, access: ServiceBuyerAccess):
        self.db, self.user, self.org, self.org_id, self.access = db, user, org, org.id, access
        self.customer_id = access.customer_id
        self.location_ids = K.jload(access.location_ids, None)

    def loc_filter(self, q, col):
        q = q.filter(col.in_(self.location_ids)) if self.location_ids else q
        return q


def resolve_buyer(db: Session, user: User, org: Organization) -> BuyerCtx:
    s = K.settings(db, org.id)
    if s is None or not (s.ops_enabled and s.buyer_portal_enabled):
        raise HTTPException(402, "The customer portal is not switched on")
    a = (db.query(ServiceBuyerAccess)
         .filter(ServiceBuyerAccess.organization_id == org.id, ServiceBuyerAccess.user_id == user.id).first())
    if a is None or a.status == "revoked":
        raise HTTPException(403, "You don't have customer portal access here")
    if a.status == "invited":
        a.status = "active"
        db.commit()
    return BuyerCtx(db, user, org, a)


# ── staff side: invite / revoke ──────────────────────────────────────────
def access_json(db: Session, a: ServiceBuyerAccess) -> Dict[str, Any]:
    u = db.query(User).filter(User.id == a.user_id).first()
    return {"id": a.id, "customer_id": a.customer_id, "user_id": a.user_id, "name": u.full_name if u else None,
            "email": u.email if u else None, "location_ids": K.jload(a.location_ids, None),
            "can_approve_estimates": a.can_approve_estimates, "status": a.status,
            "invited_at": K.iso(a.invited_at), "revoked_at": K.iso(a.revoked_at)}


def invite_buyer(ctx: K.OpsCtx, data: Dict[str, Any]) -> Dict[str, Any]:
    ctx.need("buyers", "invite customers to the portal")
    db = ctx.db
    if not (ctx.settings and ctx.settings.buyer_portal_enabled):
        raise HTTPException(402, "The customer portal is not switched on for this company")
    cust = K.get_customer(db, ctx.org_id, data.get("customer_id") or "-")
    locs = data.get("location_ids")
    if locs:
        ok = {l.id for l in db.query(ServiceLocation).filter(ServiceLocation.organization_id == ctx.org_id,
                                                             ServiceLocation.customer_id == cust.id).all()}
        if any(l not in ok for l in locs):
            raise HTTPException(422, "A location doesn't belong to this customer")
    email = (data.get("email") or "").strip().lower()
    name = K.text(data.get("full_name"), 160)
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or not name:
        raise HTTPException(422, "A portal login needs a name and a valid email")
    user = db.query(User).filter(User.email == email).first()
    if user is not None:
        # Never turn a staff member or someone else's login into a buyer.
        if user.organization_id != ctx.org_id or K.member_for(db, ctx.org_id, user.id) is not None \
                or user.role not in ("advisor",):
            raise HTTPException(409, "That email already belongs to another login")
    else:
        from app.services.auth_service import hash_password
        user = User(organization_id=ctx.org_id, email=email, full_name=name, role="advisor",
                    password_hash=hash_password(secrets.token_urlsafe(48)), must_change_password=True)
        db.add(user)
        db.flush()
    a = (db.query(ServiceBuyerAccess).filter(ServiceBuyerAccess.organization_id == ctx.org_id,
                                             ServiceBuyerAccess.user_id == user.id).first())
    if a is not None and a.customer_id != cust.id:
        raise HTTPException(409, "That login belongs to a different customer")
    if a is None:
        a = ServiceBuyerAccess(organization_id=ctx.org_id, customer_id=cust.id, user_id=user.id,
                               invited_by_id=ctx.user.id)
        db.add(a)
    a.status, a.revoked_at = "invited", None
    a.location_ids = K.jdump(locs) if locs else None
    a.can_approve_estimates = bool(data.get("can_approve_estimates"))
    user.is_active = True
    db.flush()
    from app.services import staff_activation
    from app.models.staff_models import PURPOSE_SETUP
    _row, raw = staff_activation.issue(db, user, ctx.user, purpose=PURPOSE_SETUP)
    url = staff_activation.activation_url(data.get("base_url"), raw)
    db.commit()
    K.audit(db, ctx.org_id, ctx.user, "service_buyer_invite", "service_buyer_access", a.id,
            {"customer_id": cust.id, "email": email})
    return dict(access_json(db, a), setup_url=url)


def revoke_buyer(ctx: K.OpsCtx, aid: str) -> Dict[str, Any]:
    ctx.need("buyers", "change customer portal access")
    a = (ctx.db.query(ServiceBuyerAccess).filter(ServiceBuyerAccess.organization_id == ctx.org_id,
                                                 ServiceBuyerAccess.id == aid).first())
    if a is None:
        raise HTTPException(404, "Portal access not found")
    a.status, a.revoked_at, a.revoked_by_id = "revoked", datetime.utcnow(), ctx.user.id
    u = ctx.db.query(User).filter(User.id == a.user_id).first()
    if u is not None and K.member_for(ctx.db, ctx.org_id, u.id) is None:
        u.is_active = False                         # a buyer-only login stops signing in
    ctx.db.commit()
    K.audit(ctx.db, ctx.org_id, ctx.user, "service_buyer_revoke", "service_buyer_access", a.id, {})
    return access_json(ctx.db, a)


def list_buyers(ctx: K.OpsCtx) -> List[Dict[str, Any]]:
    ctx.need("buyers", "see customer portal access")
    return [access_json(ctx.db, a) for a in ctx.db.query(ServiceBuyerAccess)
            .filter(ServiceBuyerAccess.organization_id == ctx.org_id).all()]


# ── buyer side: read ONLY their own records ───────────────────────────────
def _staff_view(b: BuyerCtx) -> K.OpsCtx:
    """A read-only, sensitive-off context for reusing the job renderers."""
    return K.OpsCtx(b.db, b.user, b.org, "viewer", None)


def buyer_locations(b: BuyerCtx) -> List[Dict[str, Any]]:
    q = b.loc_filter(b.db.query(ServiceLocation).filter(ServiceLocation.organization_id == b.org_id,
                                                        ServiceLocation.customer_id == b.customer_id),
                     ServiceLocation.id)
    v = _staff_view(b)
    out = []
    for l in q.order_by(ServiceLocation.name).all():
        j = K.location_json(v, l, sensitive=True)       # their own property's instructions
        j.pop("assigned_member_ids", None)
        out.append(j)
    return out


def _buyer_jobs_q(b: BuyerCtx):
    return b.loc_filter(b.db.query(ServiceJob).filter(ServiceJob.organization_id == b.org_id,
                                                      ServiceJob.customer_id == b.customer_id,
                                                      ServiceJob.status.in_(BUYER_JOB_STATUSES)),
                        ServiceJob.location_id)


def _buyer_job(b: BuyerCtx, j: ServiceJob, full: bool = False) -> Dict[str, Any]:
    v = _staff_view(b)
    out = J.job_json(v, j, full=full)
    # Never the company's internal notes, staff ids or names beyond a first name.
    out["assigned"] = [{"name": (a["name"] or "").split(" ")[0]} for a in out["assigned"]]
    out.pop("next", None)
    out.pop("is_mine", None)
    if full:
        out.pop("internal_notes", None)
        out.pop("supplies", None)
        out["history"] = [{"kind": h["kind"], "to": h["to"], "at": h["at"]} for h in out["history"]
                          if h["kind"] in ("status", "photo", "rescheduled")]
        out.pop("history_check", None)
        if out.get("location_detail"):
            out["location_detail"].pop("assigned_member_ids", None)
    return out


def buyer_jobs(b: BuyerCtx, *, history: bool = False) -> List[Dict[str, Any]]:
    q = _buyer_jobs_q(b)
    if history:
        q = q.filter(ServiceJob.status.in_(("completed", "customer_review", "approved")))
        rows = q.order_by(ServiceJob.scheduled_start.desc()).limit(300).all()
    else:
        q = q.filter(ServiceJob.status.notin_(("completed", "customer_review", "approved")))
        rows = q.order_by(ServiceJob.scheduled_start).limit(300).all()
    return [_buyer_job(b, j) for j in rows]


def buyer_job(b: BuyerCtx, jid: str) -> ServiceJob:
    j = _buyer_jobs_q(b).filter(ServiceJob.id == jid).first()
    if j is None:
        raise HTTPException(404, "Visit not found")
    return j


def buyer_job_detail(b: BuyerCtx, jid: str) -> Dict[str, Any]:
    return _buyer_job(b, buyer_job(b, jid), full=True)


def buyer_photo(b: BuyerCtx, jid: str, aid: str):
    j = buyer_job(b, jid)
    if j.status not in ("completed", "customer_review", "approved"):
        raise HTTPException(404, "File not found")
    a = (b.db.query(ServiceJobAttachment).filter(ServiceJobAttachment.organization_id == b.org_id,
                                                 ServiceJobAttachment.job_id == j.id,
                                                 ServiceJobAttachment.id == aid,
                                                 ServiceJobAttachment.removed_at.is_(None)).first())
    if a is None:
        raise HTTPException(404, "File not found")
    from app.services import wholesale_files as FS
    return a, FS.fetch(a.storage_backend, a.storage_key, db=b.db)


def buyer_approve_visit(b: BuyerCtx, jid: str) -> Dict[str, Any]:
    j = buyer_job(b, jid)
    if j.status != "customer_review":
        raise HTTPException(409, "This visit isn't waiting for your review")
    J._move(b.db, j, "approved", b.user, data={"by": "customer"})
    j.approved_by_id = b.user.id
    b.db.commit()
    return _buyer_job(b, j)


def buyer_estimates(b: BuyerCtx) -> List[Dict[str, Any]]:
    rows = (b.db.query(ServiceEstimate).filter(ServiceEstimate.organization_id == b.org_id,
                                               ServiceEstimate.customer_id == b.customer_id,
                                               ServiceEstimate.status.in_(("sent", "accepted", "declined")))
            .order_by(ServiceEstimate.created_at.desc()).all())
    if b.location_ids:
        rows = [e for e in rows if e.location_id is None or e.location_id in b.location_ids]
    return [dict(BL.estimate_json(e), can_decide=b.access.can_approve_estimates and e.status == "sent")
            for e in rows]


def buyer_decide_estimate(b: BuyerCtx, eid: str, accept: bool) -> Dict[str, Any]:
    if not b.access.can_approve_estimates:
        raise HTTPException(403, "Your login can't approve estimates")
    e = (b.db.query(ServiceEstimate).filter(ServiceEstimate.organization_id == b.org_id,
                                            ServiceEstimate.customer_id == b.customer_id,
                                            ServiceEstimate.id == eid, ServiceEstimate.status == "sent").first())
    if e is None:
        raise HTTPException(404, "Estimate not found")
    ctx = K.OpsCtx(b.db, b.user, b.org, "viewer", None)
    return BL.estimate_json(BL.set_estimate_status(ctx, e.id, "accepted" if accept else "declined", by_buyer=True))


def _buyer_invoice(b: BuyerCtx, iid: str) -> ServiceInvoice:
    i = (b.db.query(ServiceInvoice).filter(ServiceInvoice.organization_id == b.org_id,
                                           ServiceInvoice.customer_id == b.customer_id, ServiceInvoice.id == iid,
                                           ServiceInvoice.status != "draft").first())
    if i is None or (b.location_ids and i.location_id and i.location_id not in b.location_ids):
        raise HTTPException(404, "Invoice not found")
    return i


def buyer_invoices(b: BuyerCtx) -> List[Dict[str, Any]]:
    rows = (b.db.query(ServiceInvoice).filter(ServiceInvoice.organization_id == b.org_id,
                                              ServiceInvoice.customer_id == b.customer_id,
                                              ServiceInvoice.status != "draft")
            .order_by(ServiceInvoice.created_at.desc()).all())
    if b.location_ids:
        rows = [i for i in rows if i.location_id is None or i.location_id in b.location_ids]
    return [BL.invoice_json(b.db, i) for i in rows]


def buyer_invoice_pdf(b: BuyerCtx, iid: str) -> bytes:
    return BL.invoice_pdf(b.db, _buyer_invoice(b, iid))


def buyer_report_payment(b: BuyerCtx, iid: str, data: Dict[str, Any]) -> Dict[str, Any]:
    i = _buyer_invoice(b, iid)
    ctx = K.OpsCtx(b.db, b.user, b.org, "viewer", None)
    BL.report_payment(ctx, i.id, data, by_buyer=True)
    return BL.invoice_json(b.db, i)


# ── service requests ──────────────────────────────────────────────────────
def request_json(r: ServiceRequest) -> Dict[str, Any]:
    return {"id": r.id, "customer_id": r.customer_id, "location_id": r.location_id, "job_id": r.job_id,
            "kind": r.kind, "kind_label": REQUEST_LABEL.get(r.kind, r.kind), "service_category": r.service_category,
            "body": r.body, "status": r.status, "response": r.response, "created_at": K.iso(r.created_at)}


def buyer_request(b: BuyerCtx, data: Dict[str, Any]) -> Dict[str, Any]:
    kind = data.get("kind")
    if kind not in REQUEST_KINDS:
        raise HTTPException(422, "Unknown request type")
    body = K.text(data.get("body"), 3000)
    if not body:
        raise HTTPException(422, "Tell us what you need")
    loc_id = data.get("location_id") or None
    if loc_id:
        ok = {l["id"] for l in buyer_locations(b)}
        if loc_id not in ok:
            raise HTTPException(404, "Location not found")
    job_id = data.get("job_id") or None
    if job_id:
        buyer_job(b, job_id)
    cat = data.get("service_category") if data.get("service_category") in K.CATEGORIES else None
    r = ServiceRequest(organization_id=b.org_id, customer_id=b.customer_id, location_id=loc_id, job_id=job_id,
                       kind=kind, service_category=cat, body=body, requested_by_id=b.user.id)
    b.db.add(r)
    b.db.commit()
    return request_json(r)


def buyer_requests(b: BuyerCtx) -> List[Dict[str, Any]]:
    return [request_json(r) for r in b.db.query(ServiceRequest)
            .filter(ServiceRequest.organization_id == b.org_id, ServiceRequest.customer_id == b.customer_id)
            .order_by(ServiceRequest.created_at.desc()).limit(200).all()]


def list_requests(ctx: K.OpsCtx, status: Optional[str] = None) -> List[Dict[str, Any]]:
    ctx.need("requests", "see customer requests")
    q = ctx.db.query(ServiceRequest).filter(ServiceRequest.organization_id == ctx.org_id)
    if status:
        q = q.filter(ServiceRequest.status == status)
    custs = {c.id: c.name for c in ctx.db.query(ServiceCustomer).filter(ServiceCustomer.organization_id == ctx.org_id)}
    return [dict(request_json(r), customer_name=custs.get(r.customer_id))
            for r in q.order_by(ServiceRequest.created_at.desc()).limit(300).all()]


def handle_request(ctx: K.OpsCtx, rid: str, data: Dict[str, Any]) -> Dict[str, Any]:
    ctx.need("requests", "answer customer requests")
    r = (ctx.db.query(ServiceRequest).filter(ServiceRequest.organization_id == ctx.org_id,
                                             ServiceRequest.id == rid).first())
    if r is None:
        raise HTTPException(404, "Request not found")
    st = data.get("status")
    if st not in ("acknowledged", "closed"):
        raise HTTPException(422, "Status must be acknowledged or closed")
    r.status, r.handled_by_id = st, ctx.user.id
    if data.get("response") is not None:
        r.response = K.text(data.get("response"), 2000)
    ctx.db.commit()
    return request_json(r)


# ── multi-service buyer network (EvoSys side) ─────────────────────────────
def network_json(r: ServiceBuyerNetworkRequest, org_name: Optional[str] = None) -> Dict[str, Any]:
    return {"id": r.id, "buyer_name": r.buyer_name, "contact_name": r.contact_name, "contact_email": r.contact_email,
            "contact_phone": r.contact_phone, "city": r.city, "state": r.state,
            "service_category": r.service_category, "category_label": K.CATEGORY_LABEL.get(r.service_category),
            "details": r.details, "buyer_authorized": r.buyer_authorized, "status": r.status,
            "offered_org_id": r.offered_org_id, "offered_org_name": org_name, "offered_at": K.iso(r.offered_at),
            "accepted_at": K.iso(r.accepted_at), "created_at": K.iso(r.created_at)}


def network_create(db: Session, actor: User, data: Dict[str, Any]) -> ServiceBuyerNetworkRequest:
    cats = data.get("service_categories") or [data.get("service_category")]
    cats = [c for c in cats if c]
    if not cats or any(c not in K.CATEGORIES for c in cats):
        raise HTTPException(422, "Pick the services the buyer wants")
    name = K.text(data.get("buyer_name"), 200)
    if not name:
        raise HTTPException(422, "Buyer name is required")
    first = None
    for c in cats:          # one request per service: each may go to a different company
        r = ServiceBuyerNetworkRequest(buyer_name=name, contact_name=K.text(data.get("contact_name"), 160),
                                       contact_email=K.text(data.get("contact_email"), 200),
                                       contact_phone=K.text(data.get("contact_phone"), 40),
                                       city=K.text(data.get("city"), 120), state=K.text(data.get("state"), 40),
                                       service_category=c, details=K.text(data.get("details"), 3000),
                                       buyer_authorized=bool(data.get("buyer_authorized")), created_by_id=actor.id)
        db.add(r)
        first = first or r
    db.commit()
    return first


def eligible_companies(db: Session, r: ServiceBuyerNetworkRequest) -> List[Dict[str, Any]]:
    """Service companies that could take this request: Service Operations on,
    the right trade, the buyer's city/state in their service area."""
    from app.models.acquisition_models import AcquisitionProfile
    out = []
    for s in db.query(ServiceOpsSettings).filter(ServiceOpsSettings.ops_enabled.is_(True)).all():
        org = db.query(Organization).filter(Organization.id == s.organization_id).first()
        p = db.query(AcquisitionProfile).filter(AcquisitionProfile.organization_id == s.organization_id).first()
        if org is None:
            continue
        trades = {(org.industry or "").lower()} | {l.service_category for l in db.query(ServiceLocation)
                                                    .filter(ServiceLocation.organization_id == org.id).limit(200)}
        trade_ok = r.service_category in trades or any(r.service_category in t for t in trades if t)
        area = [a.lower() for a in K.jload(p.service_area, [])] if p else []
        states = [x.upper() for x in K.jload(p.service_states, [])] if p else []
        geo_ok = (not r.city or not area or r.city.lower() in area) and \
                 (not r.state or not states or r.state.upper() in states)
        open_jobs = db.query(ServiceJob).filter(ServiceJob.organization_id == org.id,
                                                ServiceJob.status.in_(J.BUSY)).count()
        reasons = [x for x, ok in (("trade", trade_ok), ("area", geo_ok)) if not ok]
        out.append({"organization_id": org.id, "name": org.name, "eligible": not reasons,
                    "missing": reasons, "open_jobs": open_jobs})
    return sorted(out, key=lambda x: (not x["eligible"], x["open_jobs"]))


def network_offer(db: Session, actor: User, rid: str, org_id: str) -> ServiceBuyerNetworkRequest:
    r = db.query(ServiceBuyerNetworkRequest).filter(ServiceBuyerNetworkRequest.id == rid).first()
    if r is None:
        raise HTTPException(404, "Request not found")
    if not r.buyer_authorized:
        raise HTTPException(409, "The buyer hasn't agreed to be introduced to a service company")
    if r.status not in ("new", "declined"):
        raise HTTPException(409, "This request is already %s" % r.status)
    hit = next((c for c in eligible_companies(db, r) if c["organization_id"] == org_id), None)
    if hit is None or not hit["eligible"]:
        raise HTTPException(409, "That company isn't eligible for this request")
    r.status, r.offered_org_id, r.offered_at = "offered", org_id, datetime.utcnow()
    db.commit()
    K.audit(db, org_id, actor, "service_network_offer", "service_buyer_network_request", r.id, {})
    return r


def network_offers_for(ctx: K.OpsCtx) -> List[Dict[str, Any]]:
    ctx.need("customers", "see buyer requests")
    rows = (ctx.db.query(ServiceBuyerNetworkRequest)
            .filter(ServiceBuyerNetworkRequest.offered_org_id == ctx.org_id,
                    ServiceBuyerNetworkRequest.status.in_(("offered", "accepted"))).all())
    return [network_json(r) for r in rows]


def network_answer(ctx: K.OpsCtx, rid: str, accept: bool) -> Dict[str, Any]:
    """The company accepts (the buyer becomes ITS customer, with one location)
    or declines (EvoSys may offer it to another company)."""
    ctx.need("customers", "answer buyer requests")
    r = (ctx.db.query(ServiceBuyerNetworkRequest)
         .filter(ServiceBuyerNetworkRequest.id == rid, ServiceBuyerNetworkRequest.offered_org_id == ctx.org_id).first())
    if r is None:
        raise HTTPException(404, "Request not found")
    if r.status == "accepted" and accept:
        return network_json(r)
    if r.status != "offered":
        raise HTTPException(409, "This request is already %s" % r.status)
    if accept:
        c = K.create_customer(ctx, {"name": r.buyer_name, "contact_name": r.contact_name,
                                    "contact_email": r.contact_email, "contact_phone": r.contact_phone})
        K.create_location(ctx, c.id, {"name": r.buyer_name, "city": r.city, "state": r.state,
                                      "service_category": r.service_category})
        r.status, r.accepted_at, r.service_customer_id = "accepted", datetime.utcnow(), c.id
    else:
        r.status, r.offered_org_id = "declined", None
    ctx.db.commit()
    K.audit(ctx.db, ctx.org_id, ctx.user, "service_network_%s" % ("accept" if accept else "decline"),
            "service_buyer_network_request", r.id, {})
    return network_json(r)
