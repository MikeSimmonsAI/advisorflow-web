"""Conversation Intelligence API - memory, state, takeover, composer, queues, search.

SCOPE. Every endpoint starts from `lead_scope.authorized_lead_query` - the same
query the leads list uses - so a caller sees memory only for leads they can
already open, and a guessed lead id from another workspace is a 404, never a
403 that confirms it exists. Writes also require `require_not_observation`.

NOTHING HERE SENDS. The composer returns a draft with its quality verdict; a
person sends it through the existing composers, which apply every send gate.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.deps import get_db, require_not_observation, require_tenant_user
from app.models.conversation_models import (ACTIVE, CAT_OBJECTION, CAT_QUESTION, RESOLVED,
                                            ConversationMemoryItem, ConversationState)
from app.models.models import Lead, User
from app.services import conversation_intel as ci
from app.services import lead_scope

router = APIRouter(prefix="/conversation-intel", tags=["conversation-intel"])


def _lead_or_404(db: Session, user: User, request: Request, lead_id: str) -> Lead:
    lead = lead_scope.authorized_lead_query(db, user, request=request).filter(Lead.id == lead_id).first()
    if lead is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return lead


@router.get("/leads/{lead_id}")
def get_context(lead_id: str, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_tenant_user)):
    lead = _lead_or_404(db, user, request, lead_id)
    return ci.build_context(db, lead)


class ModeIn(BaseModel):
    mode: str = Field(..., pattern="^(ai_active|human_active|ai_paused)$")
    reason: Optional[str] = Field(None, max_length=300)


@router.post("/leads/{lead_id}/mode")
def set_mode(lead_id: str, body: ModeIn, request: Request, db: Session = Depends(get_db),
             user: User = Depends(require_tenant_user), _o: User = Depends(require_not_observation)):
    lead = _lead_or_404(db, user, request, lead_id)
    ci.set_mode(db, lead, body.mode, user=user, reason=body.reason)
    # keep the legacy AI-conversation switch in step, so the cadence engine agrees
    try:
        from app.models.models import PipelineConversation
        for conv in db.query(PipelineConversation).filter(PipelineConversation.lead_id == lead.id,
                                                          PipelineConversation.organization_id == lead.organization_id):
            conv.paused = body.mode != "ai_active"
            conv.paused_reason = (body.reason or ("Human takeover" if body.mode == "human_active" else
                                                  "AI paused" if body.mode == "ai_paused" else None))
        db.commit()
    except Exception:                                            # noqa: BLE001
        db.rollback()
    return ci.build_context(db, lead)


class ComposeIn(BaseModel):
    channel: str = Field("email", pattern="^(sms|email)$")
    style: str = Field("default", pattern="^(default|shorter|warmer|more_direct)$")


@router.post("/leads/{lead_id}/compose")
def compose(lead_id: str, body: ComposeIn, request: Request, db: Session = Depends(get_db),
            user: User = Depends(require_tenant_user), _o: User = Depends(require_not_observation)):
    lead = _lead_or_404(db, user, request, lead_id)
    ctx = ci.build_context(db, lead)
    out = ci.suggest(db, lead, ctx, channel=body.channel, style=body.style, user=user)
    out["context"] = {"summary": ctx["summary"], "current_intent": ctx["current_intent"],
                      "open_questions": ctx["open_questions"],
                      "objections": [o for o in ctx["objections"] if o["status"] == ACTIVE],
                      "next_best_action": ctx["next_best_action"]}
    return out


class CheckIn(BaseModel):
    text: str = Field(..., max_length=5000)
    channel: str = Field("email", pattern="^(sms|email)$")


@router.post("/leads/{lead_id}/check")
def check(lead_id: str, body: CheckIn, request: Request, db: Session = Depends(get_db),
          user: User = Depends(require_tenant_user)):
    lead = _lead_or_404(db, user, request, lead_id)
    return ci.check_reply(ci.build_context(db, lead), body.text, body.channel)


@router.post("/leads/{lead_id}/memory/{item_id}/resolve")
def resolve_item(lead_id: str, item_id: str, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_user), _o: User = Depends(require_not_observation)):
    lead = _lead_or_404(db, user, request, lead_id)
    item = db.query(ConversationMemoryItem).filter(
        ConversationMemoryItem.id == item_id, ConversationMemoryItem.lead_id == lead.id,
        ConversationMemoryItem.organization_id == lead.organization_id).first()
    if item is None:
        raise HTTPException(status_code=404, detail="Memory item not found")
    if item.status == ACTIVE:
        item.status = RESOLVED
        item.resolved_at = datetime.utcnow()
        item.resolved_by_source_id = "user:%s" % user.id
        db.commit()
    return ci.build_context(db, lead, do_sync=False)


def _scoped_states(db, user, request):
    ids = lead_scope.authorized_lead_query(db, user, Lead.id, request=request).subquery()
    return db.query(ConversationState).filter(ConversationState.lead_id.in_(ids))


def _row(st: ConversationState, lead: Optional[Lead]):
    return {"lead_id": st.lead_id, "name": (" ".join(p for p in (lead.first_name, lead.last_name) if p) if lead else None),
            "state": st.state, "mode": st.mode, "priority": st.priority, "health": st.health,
            "intent": st.current_intent, "needs_human_reason": st.needs_human_reason,
            "has_open_question": st.has_open_question,
            "follow_up_due": st.follow_up_due_at.date().isoformat() if st.follow_up_due_at else None,
            "last_meaningful_event": st.last_meaningful_event,
            "last_inbound_at": st.last_inbound_at.isoformat() + "Z" if st.last_inbound_at else None}


@router.get("/queue")
def queue(request: Request, priority: Optional[str] = None, limit: int = Query(50, ge=1, le=200),
          db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    q = _scoped_states(db, user, request)
    counts = Counter(p for (p,) in q.with_entities(ConversationState.priority).all())
    if priority:
        q = q.filter(ConversationState.priority == priority)
    rows = q.order_by(ConversationState.updated_at.desc()).limit(limit).all()
    leads = {l.id: l for l in db.query(Lead).filter(Lead.id.in_([r.lead_id for r in rows])).all()} if rows else {}
    order = ["respond_now", "needs_human", "follow_up_today", "waiting_on_customer", "nurture", "low_priority"]
    return {"counts": {k: counts.get(k, 0) for k in order}, "items": [_row(r, leads.get(r.lead_id)) for r in rows]}


@router.get("/search")
def search(request: Request, q: str = Query(..., min_length=2, max_length=100),
           db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """Find conversations by what was said or established: 'price', 'next month', 'inherited'..."""
    ids = lead_scope.authorized_lead_query(db, user, Lead.id, request=request).subquery()
    like = "%" + q.lower() + "%"
    from sqlalchemy import func
    hits = (db.query(ConversationMemoryItem)
            .filter(ConversationMemoryItem.lead_id.in_(ids),
                    or_(func.lower(ConversationMemoryItem.value).like(like),
                        func.lower(ConversationMemoryItem.source_quote).like(like),
                        func.lower(ConversationMemoryItem.key).like(like)))
            .order_by(ConversationMemoryItem.observed_at.desc()).limit(200).all())
    by_lead = {}
    for h in hits:
        by_lead.setdefault(h.lead_id, []).append({"key": h.key, "value": h.value, "quote": h.source_quote,
                                                  "status": h.status,
                                                  "at": h.observed_at.isoformat() + "Z" if h.observed_at else None})
    leads = {l.id: l for l in db.query(Lead).filter(Lead.id.in_(list(by_lead))).all()} if by_lead else {}
    return {"query": q, "results": [{"lead_id": k, "name": " ".join(p for p in (leads[k].first_name, leads[k].last_name) if p)
                                     if k in leads else None, "matches": v[:5]} for k, v in by_lead.items()]}


@router.get("/insights")
def insights(request: Request, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """Manager view from real rows only. No scores, no percentages."""
    states = _scoped_states(db, user, request).all()
    ids = [s.lead_id for s in states]
    objections = Counter()
    if ids:
        for (key,) in db.query(ConversationMemoryItem.key).filter(
                ConversationMemoryItem.lead_id.in_(ids), ConversationMemoryItem.category == CAT_OBJECTION,
                ConversationMemoryItem.status == ACTIVE).all():
            objections[key.split(".", 1)[1].replace("_", " ")] += 1
    open_q = sum(1 for s in states if s.has_open_question)
    return {
        "conversations": len(states),
        "unanswered_questions": open_q,
        "respond_now": sum(1 for s in states if s.priority == "respond_now"),
        "overdue_replies": sum(1 for s in states if s.health == "at_risk"),
        "needs_human": sum(1 for s in states if s.priority == "needs_human"),
        "waiting_on_staff": sum(1 for s in states if s.state == "waiting_on_staff"),
        "follow_up_today": sum(1 for s in states if s.priority == "follow_up_today"),
        "not_now": sum(1 for s in states if s.state == "not_now"),
        "stopped": sum(1 for s in states if s.state == "stopped"),
        "human_active": sum(1 for s in states if s.mode == "human_active"),
        "high_intent": sum(1 for s in states if s.current_intent in ("wants_appointment", "wants_call", "interested",
                                                                         "pricing")),
        "common_objections": objections.most_common(8),
    }
