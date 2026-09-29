"""/wholesale/ops - the Wholesale pilot's human workflow.

    GET    /wholesale/ops/leads/{lead_id}/temperature         AI + human + effective + audit
    PUT    /wholesale/ops/leads/{lead_id}/temperature         human override {temperature, reason}
    DELETE /wholesale/ops/leads/{lead_id}/temperature         clear override (back to AI), audited
    GET    /wholesale/ops/leads/{lead_id}/control             Pause AI / Take over state + history
    POST   /wholesale/ops/leads/{lead_id}/control/pause|takeover|resume
    GET    /wholesale/ops/leads/{lead_id}/calls               stored calls, voicemail truth, click-to-call
    GET    /wholesale/ops/callbacks                           Callback Center buckets
    POST   /wholesale/ops/callbacks                           schedule a callback
    POST   /wholesale/ops/callbacks/{callback_id}/complete    (also accepts "exc:<exception id>")
    POST   /wholesale/ops/callbacks/{callback_id}/cancel
    POST   /wholesale/ops/callbacks/from-exception/{exception_id}   adopt a seller_callback_requested
    GET    /wholesale/ops/deals/{deal_id}                     one payload for the deal's ops panel
    GET    /wholesale/ops/deals/{deal_id}/notes
    POST   /wholesale/ops/deals/{deal_id}/notes
    PATCH  /wholesale/ops/notes/{note_id}                     author or admin
    DELETE /wholesale/ops/notes/{note_id}                     author or admin (soft delete)
    GET    /wholesale/ops/pilot                               pilot controls + distribution status
    PUT    /wholesale/ops/pilot                               admin
    GET    /wholesale/ops/skip-trace                          cost summary from the real ledgers

Every route: require_feature("wholesale_real_estate"), the ACTING workspace
(wholesale_service.write_org_id), and a 404 for anything of another
organization. Writes refuse Executive Observation Mode. NOTHING HERE SENDS,
CALLS OR BUYS ANYTHING.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_not_observation, require_tenant_or_observer, require_tenant_user
from app.models.models import User
from app.models.wholesale_ops_models import WholesaleDealNote
from app.services import wholesale_ops as OPS
from app.services import wholesale_service as svc
from app.services.entitlements import require_feature

FEATURE = "wholesale_real_estate"

router = APIRouter(prefix="/wholesale/ops", tags=["wholesale-ops"],
                   dependencies=[Depends(require_feature(FEATURE))])


def _org(db: Session, user: User) -> str:
    return svc.write_org_id(db, user)


def _is_admin(db: Session, user: User) -> bool:
    if (getattr(user, "role", None) or "").lower() in ("super_admin", "god_admin"):
        return True
    from app.services.lead_scope import effective_role
    return (effective_role(user, db) or "").lower() in ("org_admin", "super_admin")


def _event(db, org_id, user, action, summary, *, deal_id=None, property_id=None, after=None):
    from app.models.wholesale_models import ACTOR_USER
    svc.log_event(db, org_id, action, actor_type=ACTOR_USER, actor_user_id=user.id,
                  deal_id=deal_id, property_id=property_id, summary=summary, after=after)


# ── Temperature ─────────────────────────────────────────────────────────────

class TemperatureIn(BaseModel):
    temperature: str
    reason: str
    deal_id: Optional[str] = None


class ReasonIn(BaseModel):
    reason: Optional[str] = None


@router.get("/leads/{lead_id}/temperature")
def get_temperature(lead_id: str, db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_or_observer)):
    org_id = _org(db, user)
    return OPS.temperature_payload(db, org_id, OPS.get_lead(db, org_id, lead_id))


@router.put("/leads/{lead_id}/temperature")
def set_temperature(lead_id: str, payload: TemperatureIn, db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user),
                    _g: User = Depends(require_not_observation)):
    org_id = _org(db, user)
    lead = OPS.get_lead(db, org_id, lead_id)
    deal_id = svc.get_deal(db, org_id, payload.deal_id).id if payload.deal_id else None
    row = OPS.set_temperature(db, org_id, lead, user, payload.temperature, payload.reason, deal_id)
    _event(db, org_id, user, "temperature.override",
           "Marked %s (AI said %s)" % (row.human_temperature, row.ai_temperature),
           deal_id=deal_id, after={"lead_id": lead.id, "temperature": row.human_temperature})
    db.commit()
    return OPS.temperature_payload(db, org_id, lead)


@router.delete("/leads/{lead_id}/temperature")
def clear_temperature(lead_id: str, reason: Optional[str] = Query(None),
                      db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
                      _g: User = Depends(require_not_observation)):
    org_id = _org(db, user)
    lead = OPS.get_lead(db, org_id, lead_id)
    row = OPS.clear_temperature(db, org_id, lead, user, reason)
    _event(db, org_id, user, "temperature.override_cleared",
           "Returned to the AI recommendation (%s)" % row.ai_temperature,
           deal_id=row.deal_id, after={"lead_id": lead.id})
    db.commit()
    return OPS.temperature_payload(db, org_id, lead)


# ── Conversation control ────────────────────────────────────────────────────

@router.get("/leads/{lead_id}/control")
def get_control(lead_id: str, db: Session = Depends(get_db),
                user: User = Depends(require_tenant_or_observer)):
    org_id = _org(db, user)
    lead = OPS.get_lead(db, org_id, lead_id)
    return OPS.control_json(db, org_id, lead.id)


def _change_control(lead_id, action, payload, db, user):
    org_id = _org(db, user)
    lead = OPS.get_lead(db, org_id, lead_id)
    OPS.apply_control(db, org_id, lead, user, action, getattr(payload, "reason", None))
    _event(db, org_id, user, "conversation.%s" % action,
           {"pause": "AI paused", "takeover": "Conversation taken over by a person",
            "resume": "AI resumed"}[action], after={"lead_id": lead.id})
    db.commit()
    return OPS.control_json(db, org_id, lead.id)


@router.post("/leads/{lead_id}/control/pause")
def pause_ai(lead_id: str, payload: Optional[ReasonIn] = None, db: Session = Depends(get_db),
             user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    return _change_control(lead_id, "pause", payload, db, user)


@router.post("/leads/{lead_id}/control/takeover")
def take_over(lead_id: str, payload: Optional[ReasonIn] = None, db: Session = Depends(get_db),
              user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    return _change_control(lead_id, "takeover", payload, db, user)


@router.post("/leads/{lead_id}/control/resume")
def resume_ai(lead_id: str, payload: Optional[ReasonIn] = None, db: Session = Depends(get_db),
              user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    return _change_control(lead_id, "resume", payload, db, user)


@router.get("/leads/{lead_id}/calls")
def lead_calls(lead_id: str, db: Session = Depends(get_db),
               user: User = Depends(require_tenant_or_observer)):
    org_id = _org(db, user)
    return OPS.calls_payload(db, org_id, OPS.get_lead(db, org_id, lead_id))


# ── Callback Center ─────────────────────────────────────────────────────────

class CallbackIn(BaseModel):
    lead_id: Optional[str] = None
    deal_id: Optional[str] = None
    due_at: str
    notes: Optional[str] = None
    assigned_to_id: Optional[str] = None


class FinishIn(BaseModel):
    note: Optional[str] = None


class AdoptIn(BaseModel):
    due_at: Optional[str] = None
    notes: Optional[str] = None


@router.get("/callbacks")
def callbacks(mine: bool = False, include_test: bool = False,
              completed_limit: int = Query(50, ge=1, le=200),
              db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    org_id = _org(db, user)
    return OPS.callback_center(db, org_id, user=user, mine=mine, include_test=include_test,
                               completed_limit=completed_limit)


@router.post("/callbacks")
def create_callback(payload: CallbackIn, db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user),
                    _g: User = Depends(require_not_observation)):
    org_id = _org(db, user)
    cb = OPS.create_callback(db, org_id, user, lead_id=payload.lead_id, deal_id=payload.deal_id,
                             due_at=payload.due_at, notes=payload.notes,
                             assigned_to_id=payload.assigned_to_id)
    _event(db, org_id, user, "callback.scheduled", "Callback scheduled",
           deal_id=cb.deal_id, property_id=cb.property_id, after={"callback_id": cb.id})
    db.commit()
    return OPS.callback_json(db, cb, OPS._now())


@router.post("/callbacks/from-exception/{exception_id}")
def adopt_exception(exception_id: str, payload: Optional[AdoptIn] = None,
                    db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
                    _g: User = Depends(require_not_observation)):
    org_id = _org(db, user)
    cb = OPS.adopt_exception(db, org_id, user, exception_id,
                             due_at=getattr(payload, "due_at", None),
                             notes=getattr(payload, "notes", None))
    db.commit()
    return OPS.callback_json(db, cb, OPS._now())


@router.post("/callbacks/{callback_id}/complete")
def complete_callback(callback_id: str, payload: Optional[FinishIn] = None,
                      db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
                      _g: User = Depends(require_not_observation)):
    org_id = _org(db, user)
    cb = OPS.finish_callback(db, org_id, user, callback_id, "completed", getattr(payload, "note", None))
    _event(db, org_id, user, "callback.completed", "Callback completed",
           deal_id=cb.deal_id, property_id=cb.property_id, after={"callback_id": cb.id})
    db.commit()
    return OPS.callback_json(db, cb, OPS._now())


@router.post("/callbacks/{callback_id}/cancel")
def cancel_callback(callback_id: str, payload: Optional[FinishIn] = None,
                    db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
                    _g: User = Depends(require_not_observation)):
    org_id = _org(db, user)
    cb = OPS.finish_callback(db, org_id, user, callback_id, "cancelled", getattr(payload, "note", None))
    _event(db, org_id, user, "callback.cancelled", "Callback cancelled",
           deal_id=cb.deal_id, property_id=cb.property_id, after={"callback_id": cb.id})
    db.commit()
    return OPS.callback_json(db, cb, OPS._now())


# ── Deal ops panel + notes ──────────────────────────────────────────────────

class NoteIn(BaseModel):
    body: str


@router.get("/deals/{deal_id}")
def deal_ops(deal_id: str, db: Session = Depends(get_db),
             user: User = Depends(require_tenant_or_observer)):
    org_id = _org(db, user)
    deal = svc.get_deal(db, org_id, deal_id)
    now = OPS._now()
    lead = (OPS.get_lead(db, org_id, deal.seller_lead_id) if deal.seller_lead_id else None)
    from app.models.wholesale_ops_models import WholesaleSellerCallback
    from sqlalchemy import or_
    conds = [WholesaleSellerCallback.deal_id == deal.id]
    if deal.seller_lead_id:
        conds.append(WholesaleSellerCallback.lead_id == deal.seller_lead_id)
    cbs = (db.query(WholesaleSellerCallback)
           .filter(WholesaleSellerCallback.organization_id == org_id, or_(*conds))
           .order_by(WholesaleSellerCallback.due_at.desc()).limit(50).all())
    return {
        "deal_id": deal.id, "lead_id": getattr(lead, "id", None),
        "seller_name": OPS._lead_label(lead),
        "dnc": (OPS._temp_value(getattr(lead, "status", None)) == "DNC") if lead is not None else None,
        "temperature": OPS.temperature_payload(db, org_id, lead) if lead is not None else None,
        "control": OPS.control_json(db, org_id, lead.id) if lead is not None else None,
        "calls": OPS.calls_payload(db, org_id, lead) if lead is not None else None,
        "callbacks": [OPS.callback_json(db, c, now, lead=lead if c.lead_id == getattr(lead, "id", None) else None)
                      for c in cbs],
        "notes": OPS.list_notes(db, org_id, deal.id),
        "distribution": OPS.distribution_status(db, org_id),
    }


@router.get("/deals/{deal_id}/notes")
def deal_notes(deal_id: str, db: Session = Depends(get_db),
               user: User = Depends(require_tenant_or_observer)):
    org_id = _org(db, user)
    deal = svc.get_deal(db, org_id, deal_id)
    return {"notes": OPS.list_notes(db, org_id, deal.id)}


@router.post("/deals/{deal_id}/notes")
def add_note(deal_id: str, payload: NoteIn, db: Session = Depends(get_db),
             user: User = Depends(require_tenant_user),
             _g: User = Depends(require_not_observation)):
    org_id = _org(db, user)
    deal = svc.get_deal(db, org_id, deal_id)
    note = WholesaleDealNote(organization_id=org_id, deal_id=deal.id, property_id=deal.property_id,
                             lead_id=deal.seller_lead_id, author_user_id=user.id,
                             body=OPS._clean_body(payload.body), created_at=OPS._now())
    db.add(note)
    db.flush()
    _event(db, org_id, user, "note.added", "Note added", deal_id=deal.id,
           property_id=deal.property_id, after={"note_id": note.id})
    db.commit()
    return OPS.note_json(db, note)


def _own_note(db, org_id, user, note_id) -> WholesaleDealNote:
    note = OPS.get_note(db, org_id, note_id)
    if note.author_user_id != user.id and not _is_admin(db, user):
        raise HTTPException(status_code=403, detail="Only the author or an administrator can change a note.")
    return note


@router.patch("/notes/{note_id}")
def edit_note(note_id: str, payload: NoteIn, db: Session = Depends(get_db),
              user: User = Depends(require_tenant_user),
              _g: User = Depends(require_not_observation)):
    org_id = _org(db, user)
    note = _own_note(db, org_id, user, note_id)
    before = note.body
    note.body = OPS._clean_body(payload.body)
    note.edited_at = OPS._now()
    _event(db, org_id, user, "note.edited", "Note edited", deal_id=note.deal_id,
           property_id=note.property_id, after={"note_id": note.id, "previous": before[:500]})
    db.commit()
    return OPS.note_json(db, note)


@router.delete("/notes/{note_id}")
def delete_note(note_id: str, db: Session = Depends(get_db),
                user: User = Depends(require_tenant_user),
                _g: User = Depends(require_not_observation)):
    org_id = _org(db, user)
    note = _own_note(db, org_id, user, note_id)
    note.deleted_at = OPS._now()
    _event(db, org_id, user, "note.deleted", "Note removed", deal_id=note.deal_id,
           property_id=note.property_id, after={"note_id": note.id})
    db.commit()
    return {"deleted": True, "id": note.id}


# ── Pilot controls and skip-trace economics ─────────────────────────────────

class PilotIn(BaseModel):
    status: Optional[str] = None
    max_records: Optional[int] = None
    source: Optional[str] = None
    strategy_id: Optional[str] = None
    skip_trace_budget_cents: Optional[int] = None
    outreach_daily_limit: Optional[int] = None
    notes: Optional[str] = None


@router.get("/pilot")
def get_pilot(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    return OPS.pilot_payload(db, _org(db, user))


@router.put("/pilot")
def put_pilot(payload: PilotIn, db: Session = Depends(get_db),
              user: User = Depends(require_tenant_user),
              _g: User = Depends(require_not_observation)):
    org_id = _org(db, user)
    if not _is_admin(db, user):
        raise HTTPException(status_code=403, detail="Only an administrator can change pilot controls.")
    data = payload.model_dump(exclude_unset=True)
    OPS.update_pilot(db, org_id, user, data)
    _event(db, org_id, user, "pilot.updated", "Pilot controls updated", after=data)
    db.commit()
    return OPS.pilot_payload(db, org_id)


@router.get("/skip-trace")
def skip_trace(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    return OPS.skip_trace_summary(db, _org(db, user))
