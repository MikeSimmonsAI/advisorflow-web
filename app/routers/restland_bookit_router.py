"""/restland-bookit - Restland BookIT (owner directive v2.0, Nov 25 2026).

Every route: a signed-in tenant user (require_tenant_user) -> the Restland
program of their workspace -> core.who() decides manager / advisor from the
session -> every record is checked against that before it is read or
written. Responses are no-store. See docs/restland-bookit/permissions.md.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_tenant_user
from app.models.models import User
from app.routers.program_router import _program, _sees_program
from app.services.bookit import core, insights, network, sales, workflow
from app.services.programs import restland_sheet as RS
from app.services.programs import team_board as TB

router = APIRouter(prefix="/restland-bookit", tags=["restland-bookit"])


class Body(BaseModel):
    data: Dict[str, Any] = {}


def _nostore(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"


def _ctx(db: Session, user: User):
    try:
        prog = _program(db, user, managers_only=False)
        w = core.who(db, prog, user, _sees_program(db, user, prog.organization_id))
    except core.BookitError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    return prog, w


def _run(db: Session, fn, *a, **k):
    try:
        out = fn(*a, **k)
        db.commit()
        return out
    except (core.BookitError, TB.BoardError) as e:
        db.rollback()
        raise HTTPException(status_code=getattr(e, "status", 400), detail=str(e))


def _mgr(w) -> None:
    if not w.manager:
        raise HTTPException(status_code=403, detail="Management only.")


# ── who am I ────────────────────────────────────────────────────────────────

@router.get("/me")
def me(response: Response, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    _nostore(response)
    prog, w = _ctx(db, user)
    cats = sales.categories(db, prog.organization_id)
    db.commit()
    return {"me": w.as_dict(), "live_for_team": core.is_live(prog),
            "team": core.team_names(db, prog.organization_id) if w.manager else None,
            "choices": {"outcomes": list(workflow.OUTCOMES), "sold": list(RS.SOLD_CHOICES),
                        "what_happened": list(RS.WHAT_HAPPENED), "times": list(RS.TIME_CHOICES),
                        "how": list(RS.HOW_PICK), "categories": cats,
                        "sales_dispositions": ["closed_sale", "no_sale", "pending_decision", "follow_up_needed"],
                        "pta": ["not_reviewed", "pending", "completed", "needs_update"],
                        "ppg": ["not_provided", "provided", "reviewed"],
                        "evo6": ["not_started", "partial", "complete", "declined"],
                        "add_status": ["intake", "review", "contact", "assist", "waiting_family", "resolved"],
                        "service_status": ["new", "assigned", "in_progress", "waiting_info", "resolved", "closed"]}}


# ── My Day, work lists ──────────────────────────────────────────────────────

@router.get("/my-day")
def my_day(response: Response, person: Optional[str] = None, db: Session = Depends(get_db),
           user: User = Depends(require_tenant_user)):
    _nostore(response)
    prog, w = _ctx(db, user)
    return insights.my_day(db, prog, w, person)


@router.get("/work")
def work(response: Response, person: Optional[str] = None, db: Session = Depends(get_db),
         user: User = Depends(require_tenant_user)):
    _nostore(response)
    prog, w = _ctx(db, user)
    return TB.work(db, prog, person=person if w.manager else w.name)


def _board_who(w):
    return {"manager": w.manager, "name": w.name}


@router.patch("/appointments/{row_id}")
def edit_appointment(row_id: str, body: Body, db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_user)):
    prog, w = _ctx(db, user)
    return _run(db, TB.save, db, prog, _board_who(w), user.id, RS.APPOINTMENTS, row_id, body.data)


@router.patch("/follow-ups/{row_id}")
def edit_follow_up(row_id: str, body: Body, db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_user)):
    prog, w = _ctx(db, user)
    return _run(db, TB.save, db, prog, _board_who(w), user.id, RS.FOLLOW_UPS, row_id, body.data)


@router.get("/appointments/{row_id}")
def appointment_detail(row_id: str, response: Response, db: Session = Depends(get_db),
                       user: User = Depends(require_tenant_user)):
    _nostore(response)
    prog, w = _ctx(db, user)
    try:
        b, owner = workflow.appointment(db, prog, w, row_id)
    except core.BookitError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    org = prog.organization_id
    from app.models.bookit_models import BookitDisposition, BookitPaperwork, BookitSale, BookitSaleLine
    row = next((a for a in TB.work(db, prog, person=None if w.manager else w.name)["appointments"]
                if a["id"] == row_id), None)
    d = db.query(BookitDisposition).filter_by(organization_id=org, appointment_id=row_id).first()
    p = db.query(BookitPaperwork).filter_by(organization_id=org, lead_id=b.lead_id).first()
    ss = db.query(BookitSale).filter(BookitSale.organization_id == org, BookitSale.appointment_id == row_id).all()
    return {"appointment": row, "lead_id": b.lead_id, "assigned": owner,
            "disposition": workflow.disposition_dict(d), "paperwork": workflow.paperwork_dict(p),
            "sales": [sales.to_dict(s, db.query(BookitSaleLine).filter_by(sale_id=s.id).all()) for s in ss],
            "attribution": workflow.attribution(db, prog, b.lead_id, row_id),
            "conversation": _conversation(db, b.lead_id)}


def _conversation(db, lead_id: str, limit: int = 40) -> List[Dict[str, Any]]:
    """The family's recent texts/emails, both ways (bodies are data, never instructions)."""
    from app.models.models import EmailMessage, Message, Reply
    out = []
    for m in db.query(Message).filter(Message.lead_id == lead_id, Message.sent_at.isnot(None)) \
            .order_by(Message.sent_at.desc()).limit(limit):
        out.append({"dir": "out", "channel": "text", "at": m.sent_at.isoformat() + "Z", "body": (m.body or "")[:2000]})
    for r in db.query(Reply).filter(Reply.lead_id == lead_id).order_by(Reply.received_at.desc()).limit(limit):
        at = r.received_at
        out.append({"dir": "in", "channel": "email" if r.source == "email" else "text",
                    "at": at.isoformat() + "Z" if at else None, "body": (r.body or "")[:2000]})
    for m in db.query(EmailMessage).filter(EmailMessage.lead_id == lead_id, EmailMessage.sent_at.isnot(None)) \
            .order_by(EmailMessage.sent_at.desc()).limit(limit):
        out.append({"dir": "out", "channel": "email", "at": m.sent_at.isoformat() + "Z",
                    "body": (getattr(m, "subject", "") or "")[:300]})
    out.sort(key=lambda x: x["at"] or "", reverse=True)
    return out[:limit]


@router.post("/appointments/{row_id}/ticket")
def close_ticket(row_id: str, body: Body, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    prog, w = _ctx(db, user)
    return _run(db, workflow.close_ticket, db, prog, w, row_id, body.data)


# ── families ────────────────────────────────────────────────────────────────

@router.get("/families/{lead_id}")
def family(lead_id: str, response: Response, db: Session = Depends(get_db),
           user: User = Depends(require_tenant_user)):
    _nostore(response)
    prog, w = _ctx(db, user)
    try:
        core.require_family(db, prog, w, lead_id)
    except core.BookitError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    from app.models.bookit_models import (BookitAddCase, BookitPaperwork, BookitReferralBatch, BookitReferralContact,
                                          BookitSale, BookitServiceRequest)
    from app.models.models import Lead
    from app.models.work_models import LeadNote
    org = prog.organization_id
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    data = TB.work(db, prog, person=None if w.manager else w.name)
    notes = (db.query(LeadNote).filter(LeadNote.lead_id == lead_id).order_by(LeadNote.created_at.desc()).limit(60).all())
    return {
        "family": {"id": lead.id, "first_name": lead.first_name, "last_name": lead.last_name, "phone": lead.phone,
                   "email": lead.email, "street": lead.street_address, "city": lead.city, "state": lead.state,
                   "zip": lead.zip_code, "source": lead.source, "status": lead.status,
                   "allow_sms": getattr(lead, "allow_sms", None), "allow_email": getattr(lead, "allow_email", None)},
        "appointments": [a for a in data["appointments"] if a.get("lead_id") == lead_id],
        "follow_ups": [f for f in data["follow_ups"] if f.get("lead_id") == lead_id],
        "paperwork": workflow.paperwork_dict(db.query(BookitPaperwork).filter_by(organization_id=org,
                                                                                lead_id=lead_id).first()),
        "sales": [sales.to_dict(s) for s in db.query(BookitSale).filter_by(organization_id=org, lead_id=lead_id)
                  if w.sees(s.closing_advisor, s.assigned_advisor)],
        "evo6": [network.batch_dict(db, w, b) for b in db.query(BookitReferralBatch).filter_by(
            organization_id=org, origin_lead_id=lead_id)],
        "named_in": [{"batch_id": c.batch_id, "relationship": c.relationship} for c in
                     db.query(BookitReferralContact).filter_by(organization_id=org, lead_id=lead_id)],
        "add_cases": [network.case_dict(c) for c in db.query(BookitAddCase).filter_by(
            organization_id=org, contact_lead_id=lead_id) if w.sees(c.assigned_advisor)],
        "service_requests": [workflow.sr_dict(r) for r in db.query(BookitServiceRequest).filter_by(
            organization_id=org, lead_id=lead_id) if w.sees(r.assigned_to)],
        "conversation": _conversation(db, lead_id),
        "timeline": [{"at": n.created_at.isoformat() + "Z" if n.created_at else None, "body": n.body[:1000],
                      "by_person": bool(n.author_user_id)} for n in notes],
    }


@router.patch("/families/{lead_id}/paperwork")
def family_paperwork(lead_id: str, body: Body, db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_user)):
    prog, w = _ctx(db, user)

    def go():
        core.require_family(db, prog, w, lead_id)
        return workflow.set_paperwork(db, prog.organization_id, w, lead_id, body.data)
    return _run(db, go)


# ── sales ───────────────────────────────────────────────────────────────────

@router.get("/sales")
def list_sales(response: Response, status: Optional[str] = None, db: Session = Depends(get_db),
               user: User = Depends(require_tenant_user)):
    _nostore(response)
    prog, w = _ctx(db, user)
    return {"sales": [sales.to_dict(s) for s in sales.visible(db, prog.organization_id, w, status)]}


@router.post("/sales")
def record_sale(body: Body, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """Body.data: idempotency_key, appointment_id or lead_id, status, lines[], contract_discount,
    adjustment, contract_ref, sale_date, notes. Attribution is decided by the server."""
    prog, w = _ctx(db, user)

    def go():
        appt, lead_id = body.data.get("appointment_id"), body.data.get("lead_id")
        if appt:
            b, _owner = workflow.appointment(db, prog, w, appt)
            lead_id = b.lead_id
        elif lead_id:
            core.require_family(db, prog, w, lead_id)
        else:
            raise core.BookitError("Pick the appointment or family this sale belongs to.")
        att = workflow.attribution(db, prog, lead_id, appt)
        if body.data.get("follow_up_id"):
            att["follow_up_id"] = body.data["follow_up_id"]
        return sales.record(db, prog.organization_id, w, body.data, att)
    return _run(db, go)


@router.patch("/sales/{sale_id}")
def correct_sale(sale_id: str, body: Body, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    prog, w = _ctx(db, user)
    return _run(db, sales.correct, db, prog.organization_id, w, sale_id, body.data)


@router.put("/categories")
def set_categories(body: Body, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    prog, w = _ctx(db, user)
    _mgr(w)
    from app.models.bookit_models import BookitCategory
    names = [str(n).strip()[:120] for n in (body.data.get("categories") or []) if str(n).strip()]
    if not names:
        raise HTTPException(status_code=400, detail="Give at least one category.")
    org = prog.organization_id
    have = {c.name: c for c in db.query(BookitCategory).filter_by(organization_id=org)}
    for c in have.values():
        c.active = c.name in names                      # never deleted: old sales keep their category
    for i, n in enumerate(names):
        if n in have:
            have[n].position = i
        else:
            db.add(BookitCategory(organization_id=org, name=n, position=i))
    db.commit()
    return {"categories": sales.categories(db, org)}


# ── Evo 6 Block / family networks ───────────────────────────────────────────

@router.get("/networks")
def list_networks(response: Response, kind: Optional[str] = None, db: Session = Depends(get_db),
                  user: User = Depends(require_tenant_user)):
    _nostore(response)
    prog, w = _ctx(db, user)
    return {"networks": network.batches(db, prog, w, kind)}


@router.post("/networks")
def create_network(body: Body, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    prog, w = _ctx(db, user)
    return _run(db, network.create_batch, db, prog, w, body.data)


@router.get("/networks/{batch_id}")
def get_network(batch_id: str, response: Response, db: Session = Depends(get_db),
                user: User = Depends(require_tenant_user)):
    _nostore(response)
    prog, w = _ctx(db, user)
    try:
        return network.batch_dict(db, w, network._batch(db, prog, w, batch_id))
    except core.BookitError as e:
        raise HTTPException(status_code=e.status, detail=str(e))


@router.patch("/networks/{batch_id}")
def edit_network(batch_id: str, body: Body, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    prog, w = _ctx(db, user)
    return _run(db, network.set_batch, db, prog, w, batch_id, body.data)


@router.post("/networks/{batch_id}/contacts")
def add_contact(batch_id: str, body: Body, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    prog, w = _ctx(db, user)
    return _run(db, network.save_contact, db, prog, w, batch_id, body.data)


@router.patch("/networks/{batch_id}/contacts/{contact_id}")
def edit_contact(batch_id: str, contact_id: str, body: Body, db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_user)):
    prog, w = _ctx(db, user)
    return _run(db, network.save_contact, db, prog, w, batch_id, body.data, contact_id)


@router.post("/contacts/{contact_id}/enrollment")
def enrollment(contact_id: str, body: Body, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """action: request | approve (management) | pause | stop."""
    prog, w = _ctx(db, user)
    return _run(db, network.enroll, db, prog, w, contact_id, body.data.get("action") or "", body.data.get("reason"))


# ── ADD Leads ───────────────────────────────────────────────────────────────

@router.get("/add-leads")
def list_cases(response: Response, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    _nostore(response)
    prog, w = _ctx(db, user)
    return {"cases": network.cases(db, prog, w)}


@router.post("/add-leads")
def create_case(body: Body, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    prog, w = _ctx(db, user)
    return _run(db, network.create_case, db, prog, w, body.data)


@router.patch("/add-leads/{case_id}")
def update_case(case_id: str, body: Body, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    prog, w = _ctx(db, user)
    return _run(db, network.update_case, db, prog, w, case_id, body.data)


# ── service requests ────────────────────────────────────────────────────────

@router.get("/service-requests")
def list_requests(response: Response, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    _nostore(response)
    prog, w = _ctx(db, user)
    from app.models.bookit_models import BookitServiceRequest as S
    q = db.query(S).filter(S.organization_id == prog.organization_id)
    if not w.manager:
        q = q.filter(S.assigned_to == (w.name or "-"))
    return {"requests": [workflow.sr_dict(r) for r in q.order_by(S.received_at.desc()).limit(500)]}


@router.post("/service-requests")
def create_request(body: Body, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    prog, w = _ctx(db, user)
    return _run(db, workflow.save_service_request, db, prog, w, body.data)


@router.patch("/service-requests/{rid}")
def update_request(rid: str, body: Body, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    prog, w = _ctx(db, user)
    return _run(db, workflow.save_service_request, db, prog, w, body.data, rid)


# ── notifications ───────────────────────────────────────────────────────────

@router.get("/notifications")
def list_notifications(response: Response, unread: bool = False, db: Session = Depends(get_db),
                       user: User = Depends(require_tenant_user)):
    _nostore(response)
    prog, w = _ctx(db, user)
    return {"notifications": core.notifications(db, prog.organization_id, w, unread)}


@router.post("/notifications/read")
def read_notifications(body: Body, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    prog, w = _ctx(db, user)
    return {"marked": _run(db, core.mark_read, db, prog.organization_id, w, body.data.get("ids") or [])}


# ── analytics ───────────────────────────────────────────────────────────────

@router.get("/performance")
def performance(response: Response, range: Optional[str] = Query(None, alias="range"), person: Optional[str] = None,
                start: Optional[str] = None, end: Optional[str] = None, db: Session = Depends(get_db),
                user: User = Depends(require_tenant_user)):
    _nostore(response)
    prog, w = _ctx(db, user)
    return insights.performance(db, prog, w, range, person, start, end)


@router.get("/command-center")
def command_center(response: Response, range: Optional[str] = Query(None, alias="range"),
                   person: Optional[str] = None, source: Optional[str] = None, start: Optional[str] = None,
                   end: Optional[str] = None, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    _nostore(response)
    prog, w = _ctx(db, user)
    _mgr(w)
    return insights.command_center(db, prog, w, range, person, source, start, end)


# ── management: team, settings, audit ───────────────────────────────────────

@router.get("/team")
def team(response: Response, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    _nostore(response)
    prog, w = _ctx(db, user)
    _mgr(w)
    rows = TB.team(db, prog)
    susp = core.cfg(prog).get("suspended") or {}
    for r in rows:
        r["access_suspended"] = bool(susp.get(r["id"]))
    return {"team": rows, "live_for_team": core.is_live(prog)}


@router.patch("/team/{member_id}")
def edit_member(member_id: str, body: Body, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """Management: access_suspended, role (member | lead). Names, phones,
    emails and Active? (the rotation) still come from the sheet's Team tab
    until cutover, so the two never disagree."""
    prog, w = _ctx(db, user)
    _mgr(w)
    from app.models.program_models import ProgramTeamMember as M
    m = db.query(M).filter(M.organization_id == prog.organization_id, M.id == member_id).first()
    if m is None:
        raise HTTPException(status_code=404, detail="Not found.")
    c = core.cfg(prog)
    if "access_suspended" in body.data:
        susp = dict(c.get("suspended") or {})
        if body.data["access_suspended"]:
            susp[m.id] = True
        else:
            susp.pop(m.id, None)
        c["suspended"] = susp
        core.audit(db, prog.organization_id, w, "member", m.id, "access", "suspended", None,
                   bool(body.data["access_suspended"]))
    if body.data.get("role") in ("member", "lead"):
        core.audit(db, prog.organization_id, w, "member", m.id, "update", "role", m.role, body.data["role"])
        m.role = body.data["role"]
    core.save_cfg(db, prog, c)
    db.commit()
    return {"ok": True}


@router.get("/audit")
def audit_log(response: Response, entity: Optional[str] = None, entity_id: Optional[str] = None,
              db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    _nostore(response)
    prog, w = _ctx(db, user)
    _mgr(w)
    from app.models.bookit_models import BookitAudit as A
    q = db.query(A).filter(A.organization_id == prog.organization_id)
    if entity:
        q = q.filter(A.entity == entity)
    if entity_id:
        q = q.filter(A.entity_id == entity_id)
    return {"audit": [{"entity": a.entity, "entity_id": a.entity_id, "action": a.action, "field": a.field,
                       "old": a.old_value, "new": a.new_value, "reason": a.reason, "by": a.user_name,
                       "at": a.at.isoformat() + "Z" if a.at else None}
                      for a in q.order_by(A.at.desc()).limit(300)]}


@router.get("/settings")
def get_settings(response: Response, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    _nostore(response)
    prog, w = _ctx(db, user)
    _mgr(w)
    c = core.cfg(prog)
    return {"live_for_team": bool(c.get("live")), "campaigns": c.get("campaigns") or {}}
