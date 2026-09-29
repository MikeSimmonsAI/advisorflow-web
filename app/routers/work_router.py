"""COMMUNICATIONS COMMAND CENTER + LEAD NOTES / TASKS + WORKSPACE IDENTITY.

WS5, overnight build 2026-09-28. One router, three concerns that the
Communications screen needs together:

  /communications/*   the reply queue (server-side search / filter / sort /
                      pagination - no 200 cap), real KPI counts, one lead's
                      full thread, the compose gate, and a MANUAL send that
                      goes through the existing sms_service.send_sms.
  /work/*             authored notes and follow-up tasks on a lead.
  /work/identity      who the caller is IN THE WORKSPACE THEY ARE STANDING IN
                      (organization name, their name, their role there) so the
                      shell stops presenting the home account as the org.

SCOPE, ONCE: every query starts from the acting workspace organization
(`lead_scope.active_workspace_org_id`) and, for an owner-scoped role, the
caller's own leads - the same rule `/sms/replies` applies. A row in another
organization is a 404, never a 403.

WHAT NOTHING HERE DOES:
  * Never sends except POST /communications/send, and that only through
    `send_sms(..., send_source=MANUAL)` after the gate below says yes.
  * Never clears DNC / STOP / suppression, never writes consent, never infers
    consent from anything. A lead without `sms_consent is True` is refused.
  * Never turns a reply into a lead: every reply in this platform already
    belongs to a lead (`replies.lead_id` is NOT NULL, and the inbound webhook
    drops texts from unknown numbers), so "Convert to Lead" is not applicable
    to a reply and is not offered here. Contacts that are not leads are
    promoted through the existing POST /intake/contacts/{id}/promote.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, exists, func, or_
from sqlalchemy.orm import Session

from app.deps import (get_db, require_not_observation, require_tenant_or_observer,
                      require_tenant_user)
from app.models.models import (EmailMessage, Lead, Message, Organization, Reply,
                               ReplyClassification, User, VoiceCall)
from app.models.work_models import (LeadNote, LeadTask, ReplyState, NOTE_KINDS,
                                    REPLY_STATUSES, TASK_STATUSES)
from app.services import lead_scope

router = APIRouter(tags=["communications"])
log = logging.getLogger(__name__)

MAX_PAGE_SIZE = 100

# Sources that mean "the AI wrote this", from the one vocabulary for it.
from app.services import send_source as _ss  # noqa: E402
AI_SOURCES = (_ss.AI_CONVERSATION, _ss.PIPELINE_AUTO_REPLY, _ss.AI_EMPLOYEE)


def _utcnow() -> datetime:
    """Naive UTC. A function so tests can pin the clock for quiet hours."""
    return datetime.utcnow()


# ── scope ────────────────────────────────────────────────────────────────────

def _org_id(db: Session, user: User) -> str:
    org_id = lead_scope.active_workspace_org_id(user, db)
    if not org_id:
        raise HTTPException(status_code=403,
                            detail="This route needs an active customer workspace.")
    return org_id


def _lead_filters(db: Session, user: User) -> list:
    """Filters on Lead for the caller in the acting workspace (no god widening)."""
    org_id = _org_id(db, user)
    filters = [Lead.organization_id == org_id]
    if not lead_scope.is_manager_here(user, db):
        filters.append(Lead.assigned_to_id == user.id)
    return filters


def _lead_or_404(db: Session, user: User, lead_id: str) -> Lead:
    lead = db.query(Lead).filter(Lead.id == lead_id, *_lead_filters(db, user)).first()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


def _reply_or_404(db: Session, user: User, reply_id: str):
    row = (db.query(Reply, Lead).join(Lead, Reply.lead_id == Lead.id)
           .filter(Reply.id == reply_id, *_lead_filters(db, user)).first())
    if row is None:
        raise HTTPException(status_code=404, detail="Reply not found")
    return row


def _workspace_user_ids(db: Session, org_id: str) -> List[str]:
    """Users who work in this organization: home column OR active membership."""
    ids = {u for (u,) in db.query(User.id).filter(User.organization_id == org_id).all()}
    try:
        from app.models.sales_models import Membership, SCOPE_CUSTOMER_ORG
        ids |= {u for (u,) in db.query(Membership.user_id).filter(
            Membership.scope_type == SCOPE_CUSTOMER_ORG,
            Membership.scope_id == org_id,
            Membership.is_active.is_(True)).all()}
    except Exception:                                        # noqa: BLE001
        log.exception("workspace members could not be read for org %s", org_id)
    return sorted(ids)


def _assignee_or_400(db: Session, org_id: str, user_id: Optional[str]) -> Optional[str]:
    if not user_id:
        return None
    if user_id not in _workspace_user_ids(db, org_id):
        raise HTTPException(status_code=400,
                            detail="That person is not a member of this workspace.")
    return user_id


def _names(db: Session, ids) -> Dict[str, str]:
    ids = [i for i in set(ids) if i]
    if not ids:
        return {}
    return {u.id: (u.full_name or u.email or "Unknown")
            for u in db.query(User.id, User.full_name, User.email).filter(User.id.in_(ids)).all()}


def _iso(dt) -> Optional[str]:
    return dt.isoformat() if dt else None


def _cls(value) -> Optional[str]:
    return getattr(value, "value", value)


def _lead_name(lead) -> str:
    return (f"{lead.first_name or ''} {lead.last_name or ''}".strip()) or "Unknown contact"


# ── identity ─────────────────────────────────────────────────────────────────

@router.get("/work/identity")
def workspace_identity(db: Session = Depends(get_db),
                       user: User = Depends(require_tenant_or_observer)):
    """The organization this request is IN, the caller's name, and their role
    there. The shell's footer reads this instead of users.full_name/users.role,
    which describe the person's home account and made a user literally named
    "EvoSys Wholesale" read as the organization inside another workspace."""
    org_id = lead_scope.active_workspace_org_id(user, db)
    org = db.query(Organization).filter(Organization.id == org_id).first() if org_id else None
    role = lead_scope.effective_role(user, db)
    return {
        "organization_id": org.id if org else None,
        "organization_name": org.name if org else None,
        "brand_name": getattr(org, "brand_name", None) if org else None,
        "display_name": ((getattr(org, "brand_name", None) or org.name) if org else None),
        "user_id": user.id,
        "user_full_name": user.full_name,
        "workspace_role": role,
        "is_home_workspace": bool(org and user.organization_id == org.id),
        # Organizations store no marketing website of their own today; the
        # platform's website is the platform's, not the customer's. Honest null.
        "website_url": None,
    }


# ── reply queue ──────────────────────────────────────────────────────────────

def _ai_handling_clause():
    return exists().where(and_(
        Message.lead_id == Reply.lead_id,
        Message.send_source.in_(AI_SOURCES),
        Message.sent_at >= Reply.received_at,
    ))


def _dnc_clause():
    return or_(Reply.classification == ReplyClassification.DNC,
               func.lower(func.coalesce(Lead.status, "")) == "dnc")


def _needs_attention_clause():
    from app.services import reply_classification_service as _rcs
    return or_(and_(*_rcs.attention_filters()),
               and_(ReplyState.status == "needs_attention", Reply.reviewed_at.is_(None)))


def _callback_clause():
    return and_(or_(Reply.classification == ReplyClassification.CALLBACK,
                    ReplyState.status == "callback"),
                Reply.reviewed_at.is_(None))


def _base_reply_query(db: Session, user: User):
    return (db.query(Reply, Lead, ReplyState)
            .join(Lead, Reply.lead_id == Lead.id)
            .outerjoin(ReplyState, ReplyState.reply_id == Reply.id)
            .filter(*_lead_filters(db, user)))


def _parse_date(value: Optional[str], end: bool = False) -> Optional[datetime]:
    if not value:
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", ""))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date: %s" % value)
    if end and len(value) <= 10:
        d = d + timedelta(days=1)
    return d


VALID_CLASSIFICATIONS = {c.value for c in ReplyClassification}


@router.get("/communications/replies")
def communications_replies(
    q: Optional[str] = None,
    classification: Optional[str] = Query(None, description="comma-separated"),
    status: Optional[str] = Query(None, description="new|needs_attention|callback|reviewed|closed"),
    channel: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    needs_attention: bool = False,
    callbacks: bool = False,
    dnc: bool = False,
    ai_handling: bool = False,
    reviewed: Optional[bool] = None,
    assigned_to: Optional[str] = Query(None, description="user id | me | unassigned"),
    sort: str = "newest",
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_or_observer),
):
    query = _base_reply_query(db, user)

    if q and q.strip():
        term = q.strip()
        # LIKE wildcards in the user's text are literal, as in leads_query_router.
        esc = term.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = "%" + esc + "%"
        clauses = [
            func.lower(Reply.body).like(like, escape="\\"),
            func.lower(func.coalesce(Lead.first_name, "")).like(like, escape="\\"),
            func.lower(func.coalesce(Lead.last_name, "")).like(like, escape="\\"),
            func.lower(func.coalesce(Lead.first_name, "") + " "
                       + func.coalesce(Lead.last_name, "")).like(like, escape="\\"),
            func.lower(func.coalesce(Lead.email, "")).like(like, escape="\\"),
        ]
        digits = re.sub(r"\D", "", term)
        if len(digits) >= 3:
            clauses.append(Lead.phone.like("%%%s%%" % digits))
        query = query.filter(or_(*clauses))

    if classification:
        wanted = [c.strip().lower() for c in classification.split(",") if c.strip()]
        bad = [c for c in wanted if c not in VALID_CLASSIFICATIONS]
        if bad:
            raise HTTPException(status_code=400, detail="Unknown classification: %s" % ", ".join(bad))
        query = query.filter(Reply.classification.in_([ReplyClassification(c) for c in wanted]))

    if status:
        s = status.strip().lower()
        if s not in REPLY_STATUSES:
            raise HTTPException(status_code=400, detail="Unknown status: %s" % status)
        if s == "reviewed":
            query = query.filter(Reply.reviewed_at.isnot(None))
        elif s == "new":
            query = query.filter(Reply.reviewed_at.is_(None),
                                 or_(ReplyState.id.is_(None), ReplyState.status == "new"))
        else:
            query = query.filter(ReplyState.status == s)

    if channel:
        query = query.filter(func.lower(func.coalesce(Reply.source, "sms")) == channel.strip().lower())

    start = _parse_date(date_from)
    end = _parse_date(date_to, end=True)
    if start:
        query = query.filter(Reply.received_at >= start)
    if end:
        query = query.filter(Reply.received_at < end)

    if needs_attention:
        query = query.filter(_needs_attention_clause())
    if callbacks:
        query = query.filter(_callback_clause())
    if dnc:
        query = query.filter(_dnc_clause())
    if ai_handling:
        query = query.filter(_ai_handling_clause())
    if reviewed is True:
        query = query.filter(Reply.reviewed_at.isnot(None))
    elif reviewed is False:
        query = query.filter(Reply.reviewed_at.is_(None))

    if assigned_to:
        if assigned_to == "me":
            query = query.filter(ReplyState.assigned_to_id == user.id)
        elif assigned_to == "unassigned":
            query = query.filter(ReplyState.assigned_to_id.is_(None))
        else:
            query = query.filter(ReplyState.assigned_to_id == assigned_to)

    total = query.count()

    if sort == "oldest":
        query = query.order_by(Reply.received_at.asc(), Reply.id.asc())
    elif sort == "name":
        query = query.order_by(func.lower(func.coalesce(Lead.last_name, "")),
                               func.lower(func.coalesce(Lead.first_name, "")),
                               Reply.received_at.desc())
    elif sort == "newest":
        query = query.order_by(Reply.received_at.desc(), Reply.id.desc())
    else:
        raise HTTPException(status_code=400, detail="Unknown sort: %s" % sort)

    rows = query.offset((page - 1) * page_size).limit(page_size).all()
    names = _names(db, [s.assigned_to_id for _, _, s in rows if s])
    from app.services import reply_classification_service as _rcs

    items = []
    for reply, lead, state in rows:
        is_dnc = (lead.status or "").lower() == "dnc" or _cls(reply.classification) == "dnc"
        items.append({
            "id": reply.id,
            "lead_id": lead.id,
            "contact_name": _lead_name(lead),
            "phone": lead.phone,
            "email": lead.email,
            "body": reply.body,
            "channel": reply.source or "sms",
            "classification": _cls(reply.classification),
            "classification_confidence": reply.classification_confidence,
            "is_hot": bool(reply.is_hot),
            "received_at": _iso(reply.received_at),
            "reviewed_at": _iso(reply.reviewed_at),
            "status": ("reviewed" if reply.reviewed_at else (state.status if state else "new")),
            "needs_attention": bool(_rcs.reply_needs_attention(reply)
                                    or (state and state.status == "needs_attention" and not reply.reviewed_at)),
            "assigned_to_id": state.assigned_to_id if state else None,
            "assigned_to_name": names.get(state.assigned_to_id) if state else None,
            "is_dnc": is_dnc,
            "sms_consent": bool(lead.sms_consent),
        })

    return {"items": items, "total": total, "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size if total else 0}


@router.get("/communications/summary")
def communications_summary(db: Session = Depends(get_db),
                           user: User = Depends(require_tenant_or_observer)):
    """Real counts over the caller's whole scope, not the current page."""
    base = _base_reply_query(db, user)
    from app.services import reply_classification_service as _rcs  # noqa: F401
    return {
        "needs_attention": base.filter(_needs_attention_clause()).count(),
        "callbacks": base.filter(_callback_clause()).count(),
        "reviewed": base.filter(Reply.reviewed_at.isnot(None)).count(),
        "dnc_stop": base.filter(_dnc_clause()).count(),
        "ai_handling": base.filter(_ai_handling_clause()).count(),
        "unreviewed": base.filter(Reply.reviewed_at.is_(None)).count(),
        "total": base.count(),
        "open_tasks": (db.query(LeadTask)
                       .filter(LeadTask.organization_id == _org_id(db, user),
                               LeadTask.status == "open",
                               *([] if lead_scope.is_manager_here(user, db)
                                 else [LeadTask.assigned_to_id == user.id]))
                       .count()),
    }


@router.get("/communications/assignees")
def communications_assignees(db: Session = Depends(get_db),
                             user: User = Depends(require_tenant_or_observer)):
    org_id = _org_id(db, user)
    ids = _workspace_user_ids(db, org_id)
    rows = db.query(User).filter(User.id.in_(ids), User.is_active.isnot(False)).all() if ids else []
    return [{"id": u.id, "name": u.full_name or u.email} for u in
            sorted(rows, key=lambda u: (u.full_name or u.email or "").lower())]


# ── reply actions ────────────────────────────────────────────────────────────

def _state_for(db: Session, org_id: str, reply: Reply) -> ReplyState:
    state = db.query(ReplyState).filter(ReplyState.reply_id == reply.id).first()
    if state is None:
        state = ReplyState(organization_id=org_id, reply_id=reply.id, status="new")
        db.add(state)
        db.flush()
    return state


def _state_out(reply: Reply, state: ReplyState, db: Session) -> dict:
    names = _names(db, [state.assigned_to_id, state.reviewed_by_id])
    return {
        "reply_id": reply.id,
        "status": "reviewed" if reply.reviewed_at else state.status,
        "reviewed_at": _iso(reply.reviewed_at),
        "reviewed_by_id": state.reviewed_by_id,
        "reviewed_by_name": names.get(state.reviewed_by_id),
        "assigned_to_id": state.assigned_to_id,
        "assigned_to_name": names.get(state.assigned_to_id),
    }


class ReviewIn(BaseModel):
    reviewed: bool = True


@router.post("/communications/replies/{reply_id}/review")
def review_reply(reply_id: str, req: ReviewIn = ReviewIn(),
                 db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_user),
                 _obs: User = Depends(require_not_observation)):
    reply, lead = _reply_or_404(db, user, reply_id)
    state = _state_for(db, lead.organization_id, reply)
    if req.reviewed:
        now = _utcnow()
        reply.reviewed_at = now          # the existing authority for "reviewed"
        state.reviewed_at = now
        state.reviewed_by_id = user.id
        state.status = "reviewed"
    else:
        reply.reviewed_at = None
        state.reviewed_at = None
        state.reviewed_by_id = None
        state.status = "new"
    db.commit()
    return _state_out(reply, state, db)


class AssignIn(BaseModel):
    assigned_to_id: Optional[str] = None


@router.post("/communications/replies/{reply_id}/assign")
def assign_reply(reply_id: str, req: AssignIn,
                 db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_user),
                 _obs: User = Depends(require_not_observation)):
    reply, lead = _reply_or_404(db, user, reply_id)
    state = _state_for(db, lead.organization_id, reply)
    state.assigned_to_id = _assignee_or_400(db, lead.organization_id, req.assigned_to_id)
    db.commit()
    return _state_out(reply, state, db)


class StatusIn(BaseModel):
    status: str


@router.post("/communications/replies/{reply_id}/status")
def set_reply_status(reply_id: str, req: StatusIn,
                     db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_user),
                     _obs: User = Depends(require_not_observation)):
    s = (req.status or "").strip().lower()
    if s not in REPLY_STATUSES or s == "reviewed":
        raise HTTPException(status_code=400,
                            detail="Status must be one of new, needs_attention, callback, closed "
                                   "(use /review to mark reviewed).")
    reply, lead = _reply_or_404(db, user, reply_id)
    state = _state_for(db, lead.organization_id, reply)
    state.status = s
    db.commit()
    return _state_out(reply, state, db)


class CallbackTaskIn(BaseModel):
    title: Optional[str] = None
    due_at: Optional[datetime] = None
    assigned_to_id: Optional[str] = None


@router.post("/communications/replies/{reply_id}/callback-task", status_code=201)
def reply_callback_task(reply_id: str, req: CallbackTaskIn = CallbackTaskIn(),
                        db: Session = Depends(get_db),
                        user: User = Depends(require_tenant_user),
                        _obs: User = Depends(require_not_observation)):
    reply, lead = _reply_or_404(db, user, reply_id)
    org_id = lead.organization_id
    assignee = _assignee_or_400(db, org_id, req.assigned_to_id) or lead.assigned_to_id or user.id
    task = LeadTask(organization_id=org_id, lead_id=lead.id,
                    title=(req.title or "Call back %s" % _lead_name(lead))[:300],
                    due_at=req.due_at, status="open", assigned_to_id=assignee,
                    created_by_id=user.id, source="callback", reply_id=reply.id)
    db.add(task)
    state = _state_for(db, org_id, reply)
    if not reply.reviewed_at:
        state.status = "callback"
    db.commit()
    return _task_out(task, _names(db, [task.assigned_to_id, task.created_by_id]), lead)


# ── thread + compose gate + send ────────────────────────────────────────────

def compose_gate(db: Session, lead: Lead, user: User) -> Dict[str, Any]:
    """May a person text this lead RIGHT NOW from the composer, and if not, why.

    Stricter than, never looser than, the send path: every check that
    sms_service.send_sms makes still runs inside it on Send. Consent is read,
    never written; DNC/STOP is displayed, never cleared.
    """
    reasons: List[Dict[str, str]] = []

    def block(code, label):
        reasons.append({"code": code, "label": label})

    if not (lead.phone or "").strip():
        block("NO_PHONE", "This contact has no phone number.")
    if (lead.status or "").lower() == "dnc":
        block("DNC", "Marked Do Not Contact. Sending is blocked.")
    stop_reply = (db.query(Reply.id)
                  .filter(Reply.lead_id == lead.id,
                          Reply.classification == ReplyClassification.DNC).first())
    if stop_reply and not any(r["code"] == "DNC" for r in reasons):
        block("STOP_REPLY", "This contact replied STOP / asked not to be contacted.")
    if lead.sms_consent is not True:
        block("NO_SMS_CONSENT", "No SMS consent of record for this contact. "
                                "Consent is never assumed from a phone number or a reply.")
    try:
        from app.services.compliance_service import check_compliance_preflight
        check_compliance_preflight(db, lead, "sms", allow_test=True)
    except ValueError as e:
        msg = str(e)
        if not ("DNC" in msg and any(r["code"] == "DNC" for r in reasons)):
            block("COMPLIANCE", msg)
    if (lead.phone or "").strip():
        try:
            from app.services import contact_hours
            hours = contact_hours.check(lead, _utcnow())
            if not hours.get("permitted"):
                block("QUIET_HOURS", hours.get("reason") or "Outside permitted contact hours.")
        except Exception:                                    # noqa: BLE001
            log.exception("contact hours check failed for lead %s", lead.id)
            block("QUIET_HOURS", "Contact hours could not be determined.")
    sender = None
    try:
        from app.routers.compose_router import acting_advisor
        from app.services.sms_service import describe_sms_sender
        sender = describe_sms_sender(acting_advisor(db, lead, user), db)
        if not sender.get("ready"):
            block("NO_SENDER", sender.get("reason") or "No SMS sender is configured.")
    except Exception:                                        # noqa: BLE001
        log.exception("sms sender could not be described for lead %s", lead.id)
        block("NO_SENDER", "The SMS sender could not be read.")
    return {
        "channel": "sms",
        "allowed": not reasons,
        "reasons": reasons,
        "from_number": (sender or {}).get("from_number"),
    }


def _consent_out(lead: Lead) -> dict:
    return {
        "sms_consent": lead.sms_consent is True,
        "sms_consent_source": lead.sms_consent_source,
        "sms_consent_at": _iso(lead.sms_consent_timestamp),
        "allow_email": lead.allow_email,
    }


@router.get("/communications/compose-gate/{lead_id}")
def get_compose_gate(lead_id: str, db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_or_observer)):
    lead = _lead_or_404(db, user, lead_id)
    return compose_gate(db, lead, user)


@router.get("/communications/thread/{lead_id}")
def communications_thread(lead_id: str, limit: int = Query(300, ge=1, le=1000),
                          db: Session = Depends(get_db),
                          user: User = Depends(require_tenant_or_observer)):
    lead = _lead_or_404(db, user, lead_id)
    events: List[dict] = []

    for r in (db.query(Reply).filter(Reply.lead_id == lead.id)
              .order_by(Reply.received_at.desc()).limit(limit).all()):
        events.append({"type": "message", "direction": "inbound", "channel": r.source or "sms",
                       "id": r.id, "at": _iso(r.received_at), "body": r.body,
                       "classification": _cls(r.classification), "sender": _lead_name(lead),
                       "actor": "customer"})

    msgs = (db.query(Message).filter(Message.lead_id == lead.id)
            .order_by(Message.sent_at.desc()).limit(limit).all())
    emails = (db.query(EmailMessage).filter(EmailMessage.lead_id == lead.id)
              .order_by(EmailMessage.sent_at.desc()).limit(limit).all())
    calls = (db.query(VoiceCall).filter(VoiceCall.lead_id == lead.id,
                                        VoiceCall.organization_id == lead.organization_id)
             .order_by(VoiceCall.created_at.desc()).limit(limit).all())
    notes = (db.query(LeadNote).filter(LeadNote.lead_id == lead.id,
                                       LeadNote.organization_id == lead.organization_id)
             .order_by(LeadNote.created_at.desc()).limit(limit).all())
    names = _names(db, [m.sender_id for m in msgs] + [m.sent_by_user_id for m in msgs]
                   + [e.sender_id for e in emails] + [c.advisor_id for c in calls]
                   + [n.author_user_id for n in notes])

    for m in msgs:
        ai = (m.send_source or "") in AI_SOURCES
        events.append({"type": "message", "direction": "outbound", "channel": "sms",
                       "id": m.id, "at": _iso(m.sent_at), "body": m.body,
                       "send_source": m.send_source, "state": m.send_state or m.delivery_status,
                       "sender": ("AI" if ai else names.get(m.sent_by_user_id) or names.get(m.sender_id)),
                       "actor": "ai" if ai else ("human" if m.send_source else "unrecorded")})
    for e in emails:
        events.append({"type": "message", "direction": "outbound", "channel": "email",
                       "id": e.id, "at": _iso(e.sent_at), "subject": e.subject,
                       "body": None, "send_source": e.send_source, "state": e.status,
                       "sender": names.get(e.sent_by_user_id) or names.get(e.sender_id),
                       "actor": "ai" if (e.send_source or "") in AI_SOURCES else "human"})
    for c in calls:
        events.append({"type": "call", "direction": c.direction or "outbound", "channel": "voice",
                       "id": c.id, "at": _iso(c.started_at or c.created_at),
                       "status": c.status, "outcome": c.outcome,
                       "duration_seconds": c.duration_seconds,
                       "voicemail_left": bool(c.voicemail_left),
                       "summary": c.summary or c.voicemail_transcript,
                       "sender": names.get(c.advisor_id)})
    for n in notes:
        events.append({"type": "note", "channel": "internal", "id": n.id,
                       "at": _iso(n.created_at), "body": n.body, "kind": n.kind,
                       "sender": names.get(n.author_user_id)})

    events.sort(key=lambda ev: ev.get("at") or "")
    assigned = _names(db, [lead.assigned_to_id])

    from app.services.compliance_service import is_phone_suppressed
    try:
        suppressed = bool(lead.phone) and is_phone_suppressed(db, lead.organization_id, lead.phone)
    except Exception:                                        # noqa: BLE001
        suppressed = None

    return {
        "lead": {
            "id": lead.id, "name": _lead_name(lead), "first_name": lead.first_name,
            "last_name": lead.last_name, "phone": lead.phone, "email": lead.email,
            "status": lead.status, "city": lead.city, "state": lead.state,
            "assigned_to_id": lead.assigned_to_id,
            "assigned_to_name": assigned.get(lead.assigned_to_id),
            "is_dnc": (lead.status or "").lower() == "dnc",
            "suppressed": suppressed,
            "org_contact_id": lead.org_contact_id,
            "created_at": _iso(lead.created_at),
            "source": lead.source,
            **_consent_out(lead),
        },
        "events": events,
        "compose": compose_gate(db, lead, user),
    }


class SendIn(BaseModel):
    lead_id: str
    body: str = Field(..., min_length=1, max_length=1600)
    reply_id: Optional[str] = None


@router.post("/communications/send")
def communications_send(req: SendIn,
                        db: Session = Depends(get_db),
                        user: User = Depends(require_tenant_user),
                        _obs: User = Depends(require_not_observation)):
    """ONE MANUAL SMS, through the existing send path, after the gate."""
    lead = _lead_or_404(db, user, req.lead_id)
    if req.reply_id:
        _reply_or_404(db, user, req.reply_id)
    body = req.body.strip()
    if not body:
        raise HTTPException(status_code=400, detail="Message is empty.")
    gate = compose_gate(db, lead, user)
    if not gate["allowed"]:
        raise HTTPException(status_code=409, detail={
            "message": gate["reasons"][0]["label"], "reasons": gate["reasons"]})

    from app.routers.compose_router import acting_advisor
    from app.services.sms_service import send_sms
    try:
        message = send_sms(db, acting_advisor(db, lead, user), lead, body,
                           include_booking_link=False,
                           send_source=_ss.MANUAL, sent_by_user_id=user.id)
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=409, detail={"message": str(e),
                                                     "reasons": [{"code": "SEND_BLOCKED", "label": str(e)}]})
    except RuntimeError as e:          # DemoBoundaryViolation and friends
        db.rollback()
        raise HTTPException(status_code=409, detail={"message": str(e),
                                                     "reasons": [{"code": "SEND_REFUSED", "label": str(e)}]})
    return {"message_id": message.id, "state": message.send_state or message.twilio_status,
            "send_source": message.send_source, "body": message.body,
            "sent_at": _iso(message.sent_at)}


# ── notes ────────────────────────────────────────────────────────────────────

def _note_out(n: LeadNote, names: Dict[str, str], user: User) -> dict:
    return {"id": n.id, "lead_id": n.lead_id, "body": n.body, "kind": n.kind,
            "pinned": bool(n.pinned), "reply_id": n.reply_id,
            "author_user_id": n.author_user_id, "author_name": names.get(n.author_user_id),
            "created_at": _iso(n.created_at), "mine": n.author_user_id == user.id}


@router.get("/work/leads/{lead_id}/notes")
def list_notes(lead_id: str, db: Session = Depends(get_db),
               user: User = Depends(require_tenant_or_observer)):
    lead = _lead_or_404(db, user, lead_id)
    notes = (db.query(LeadNote)
             .filter(LeadNote.organization_id == lead.organization_id, LeadNote.lead_id == lead.id)
             .order_by(LeadNote.pinned.desc(), LeadNote.created_at.desc()).limit(500).all())
    names = _names(db, [n.author_user_id for n in notes])
    return {"items": [_note_out(n, names, user) for n in notes],
            # The legacy single free-text field, shown read-only beside the log.
            "legacy_notes": lead.notes}


class NoteIn(BaseModel):
    body: str = Field(..., min_length=1, max_length=10000)
    kind: str = "note"
    pinned: bool = False
    reply_id: Optional[str] = None


@router.post("/work/leads/{lead_id}/notes", status_code=201)
def create_note(lead_id: str, req: NoteIn, db: Session = Depends(get_db),
                user: User = Depends(require_tenant_user),
                _obs: User = Depends(require_not_observation)):
    lead = _lead_or_404(db, user, lead_id)
    if req.kind not in NOTE_KINDS:
        raise HTTPException(status_code=400, detail="kind must be note or internal")
    if not req.body.strip():
        raise HTTPException(status_code=400, detail="Note is empty.")
    if req.reply_id:
        reply, rlead = _reply_or_404(db, user, req.reply_id)
        if rlead.id != lead.id:
            raise HTTPException(status_code=400, detail="That reply belongs to a different contact.")
    note = LeadNote(organization_id=lead.organization_id, lead_id=lead.id,
                    author_user_id=user.id, body=req.body.strip(), kind=req.kind,
                    pinned=req.pinned, reply_id=req.reply_id)
    db.add(note)
    db.commit()
    return _note_out(note, _names(db, [user.id]), user)


class NotePatch(BaseModel):
    pinned: Optional[bool] = None


@router.patch("/work/leads/{lead_id}/notes/{note_id}")
def patch_note(lead_id: str, note_id: str, req: NotePatch, db: Session = Depends(get_db),
               user: User = Depends(require_tenant_user),
               _obs: User = Depends(require_not_observation)):
    lead = _lead_or_404(db, user, lead_id)
    note = (db.query(LeadNote).filter(LeadNote.id == note_id, LeadNote.lead_id == lead.id,
                                      LeadNote.organization_id == lead.organization_id).first())
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")
    if req.pinned is not None:
        note.pinned = req.pinned
    db.commit()
    return _note_out(note, _names(db, [note.author_user_id]), user)


@router.delete("/work/leads/{lead_id}/notes/{note_id}", status_code=204)
def delete_note(lead_id: str, note_id: str, db: Session = Depends(get_db),
                user: User = Depends(require_tenant_user),
                _obs: User = Depends(require_not_observation)):
    lead = _lead_or_404(db, user, lead_id)
    note = (db.query(LeadNote).filter(LeadNote.id == note_id, LeadNote.lead_id == lead.id,
                                      LeadNote.organization_id == lead.organization_id).first())
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")
    if note.author_user_id != user.id:
        raise HTTPException(status_code=403, detail="Only the author can delete a note.")
    db.delete(note)
    db.commit()
    return None


# ── tasks ────────────────────────────────────────────────────────────────────

def _task_out(t: LeadTask, names: Dict[str, str], lead: Optional[Lead] = None) -> dict:
    now = _utcnow()
    return {"id": t.id, "lead_id": t.lead_id, "lead_name": _lead_name(lead) if lead else None,
            "title": t.title, "details": t.details, "due_at": _iso(t.due_at),
            "status": t.status, "overdue": bool(t.status == "open" and t.due_at and t.due_at < now),
            "assigned_to_id": t.assigned_to_id, "assigned_to_name": names.get(t.assigned_to_id),
            "created_by_id": t.created_by_id, "created_by_name": names.get(t.created_by_id),
            "completed_at": _iso(t.completed_at), "source": t.source, "reply_id": t.reply_id,
            "created_at": _iso(t.created_at)}


def _task_scope(db: Session, user: User):
    org_id = _org_id(db, user)
    q = db.query(LeadTask, Lead).outerjoin(Lead, LeadTask.lead_id == Lead.id).filter(
        LeadTask.organization_id == org_id)
    if not lead_scope.is_manager_here(user, db):
        # An owner-scoped person sees tasks on their own leads and tasks given to them.
        q = q.filter(or_(LeadTask.assigned_to_id == user.id, Lead.assigned_to_id == user.id))
    return q


@router.get("/work/tasks")
def list_tasks(lead_id: Optional[str] = None,
               status: Optional[str] = "open",
               due: Optional[str] = Query(None, description="overdue|today|upcoming|none"),
               assigned: Optional[str] = Query(None, description="me|unassigned|<user id>"),
               page: int = Query(1, ge=1),
               page_size: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
               db: Session = Depends(get_db),
               user: User = Depends(require_tenant_or_observer)):
    q = _task_scope(db, user)
    if lead_id:
        _lead_or_404(db, user, lead_id)
        q = q.filter(LeadTask.lead_id == lead_id)
    if status and status != "all":
        if status not in TASK_STATUSES:
            raise HTTPException(status_code=400, detail="Unknown status: %s" % status)
        q = q.filter(LeadTask.status == status)
    now = _utcnow()
    today_end = datetime(now.year, now.month, now.day) + timedelta(days=1)
    if due == "overdue":
        q = q.filter(LeadTask.due_at.isnot(None), LeadTask.due_at < now)
    elif due == "today":
        q = q.filter(LeadTask.due_at.isnot(None), LeadTask.due_at < today_end)
    elif due == "upcoming":
        q = q.filter(LeadTask.due_at >= now)
    elif due == "none":
        q = q.filter(LeadTask.due_at.is_(None))
    elif due:
        raise HTTPException(status_code=400, detail="Unknown due filter: %s" % due)
    if assigned == "me":
        q = q.filter(LeadTask.assigned_to_id == user.id)
    elif assigned == "unassigned":
        q = q.filter(LeadTask.assigned_to_id.is_(None))
    elif assigned:
        q = q.filter(LeadTask.assigned_to_id == assigned)
    total = q.count()
    rows = (q.order_by(LeadTask.due_at.is_(None), LeadTask.due_at.asc(), LeadTask.created_at.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    names = _names(db, [t.assigned_to_id for t, _ in rows] + [t.created_by_id for t, _ in rows])
    return {"items": [_task_out(t, names, lead) for t, lead in rows], "total": total,
            "page": page, "page_size": page_size}


class TaskIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    lead_id: Optional[str] = None
    details: Optional[str] = None
    due_at: Optional[datetime] = None
    assigned_to_id: Optional[str] = None
    reply_id: Optional[str] = None
    source: Optional[str] = "manual"


@router.post("/work/tasks", status_code=201)
def create_task(req: TaskIn, db: Session = Depends(get_db),
                user: User = Depends(require_tenant_user),
                _obs: User = Depends(require_not_observation)):
    org_id = _org_id(db, user)
    lead = _lead_or_404(db, user, req.lead_id) if req.lead_id else None
    if req.reply_id:
        _, rlead = _reply_or_404(db, user, req.reply_id)
        if lead is not None and rlead.id != lead.id:
            raise HTTPException(status_code=400, detail="That reply belongs to a different contact.")
        lead = lead or rlead
    if not req.title.strip():
        raise HTTPException(status_code=400, detail="Title is required.")
    task = LeadTask(organization_id=org_id, lead_id=lead.id if lead else None,
                    title=req.title.strip(), details=req.details, due_at=req.due_at,
                    status="open",
                    assigned_to_id=_assignee_or_400(db, org_id, req.assigned_to_id) or user.id,
                    created_by_id=user.id, source=(req.source or "manual")[:40],
                    reply_id=req.reply_id)
    db.add(task)
    db.commit()
    return _task_out(task, _names(db, [task.assigned_to_id, task.created_by_id]), lead)


class TaskPatch(BaseModel):
    status: Optional[str] = None
    assigned_to_id: Optional[str] = None
    unassign: bool = False
    title: Optional[str] = None
    due_at: Optional[datetime] = None
    details: Optional[str] = None


@router.patch("/work/tasks/{task_id}")
def update_task(task_id: str, req: TaskPatch, db: Session = Depends(get_db),
                user: User = Depends(require_tenant_user),
                _obs: User = Depends(require_not_observation)):
    row = _task_scope(db, user).filter(LeadTask.id == task_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Task not found")
    task, lead = row
    if req.status is not None:
        if req.status not in TASK_STATUSES:
            raise HTTPException(status_code=400, detail="Unknown status: %s" % req.status)
        task.status = req.status
        task.completed_at = _utcnow() if req.status == "done" else None
    if req.unassign:
        task.assigned_to_id = None
    elif req.assigned_to_id:
        task.assigned_to_id = _assignee_or_400(db, task.organization_id, req.assigned_to_id)
    if req.title is not None:
        if not req.title.strip():
            raise HTTPException(status_code=400, detail="Title is required.")
        task.title = req.title.strip()[:300]
    if req.due_at is not None:
        task.due_at = req.due_at
    if req.details is not None:
        task.details = req.details
    db.commit()
    return _task_out(task, _names(db, [task.assigned_to_id, task.created_by_id]), lead)
