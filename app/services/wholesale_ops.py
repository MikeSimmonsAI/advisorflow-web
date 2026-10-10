"""Wholesale pilot operations - the human workflow around a seller.

HOT/WARM/COLD with human override, the Callback Center, deal notes, Pause AI /
Take Over / Resume AI, pilot controls and the skip-trace cost summary.

NOTHING HERE SENDS ANYTHING. The only outbound-affecting code is
`ai_send_refusal`, which can only REFUSE a send (it is called from the send
gates); it can never permit one another gate refused.

Every function takes the acting `org_id` and filters by it. A record of
another organization is a 404, never a 403, so an id cannot be probed.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from app.models.models import Lead, User
from app.models.wholesale_ops_models import (
    CALLBACK_STATUSES, PILOT_STATUSES, TEMPERATURES,
    WholesaleConversationControl, WholesaleConversationControlEvent, WholesaleDealNote,
    WholesalePilotControl, WholesaleSellerCallback, WholesaleTemperatureOverride,
)

log = logging.getLogger(__name__)

# A callback is DUE NOW from this long before its time until this long after
# it; earlier than that it is UPCOMING, later it is OVERDUE (and stays overdue
# until a person completes or cancels it).
DUE_NOW_BEFORE = timedelta(minutes=30)
DUE_NOW_AFTER = timedelta(minutes=30)
BUCKETS = ("due_now", "upcoming", "overdue", "completed")

# While AI is paused / a human has taken over, ONLY these send sources may
# reach the seller. `None` (a path that does not stamp its source) is NOT on
# the list: an unlabelled send cannot prove a person pressed the button, so it
# is refused - fail closed, never "probably manual".
HUMAN_SEND_SOURCES = ("manual",)


def _iso(dt) -> Optional[str]:
    return dt.isoformat() + "Z" if dt else None


def _now() -> datetime:
    return datetime.utcnow()


# ── Loading inside the tenant ───────────────────────────────────────────────

def get_lead(db, org_id: str, lead_id: str) -> Lead:
    lead = (db.query(Lead)
            .filter(Lead.id == lead_id, Lead.organization_id == org_id).first())
    if lead is None:
        raise HTTPException(status_code=404, detail="Seller not found")
    return lead


def user_name(db, user_id: Optional[str]) -> Optional[str]:
    if not user_id:
        return None
    u = db.query(User).filter(User.id == user_id).first()
    return (getattr(u, "full_name", None) or getattr(u, "email", None)) if u else None


def assignable_user(db, org_id: str, user_id: Optional[str]) -> Optional[str]:
    """An assignee must belong to THIS organization."""
    if not user_id:
        return None
    u = db.query(User).filter(User.id == user_id, User.organization_id == org_id).first()
    if u is None:
        raise HTTPException(status_code=400, detail="That person is not in this workspace.")
    return u.id


# ── HOT / WARM / COLD ───────────────────────────────────────────────────────

def _temp_value(raw) -> str:
    v = getattr(raw, "value", raw)
    return (str(v).upper() if v else "UNKNOWN")


def ai_temperature(db, lead) -> Tuple[str, str]:
    """The machine classification and WHY, read from the same rules as
    engagement_service.classify_lead_temperature (it is called, not copied, for
    the value). The reason names the rule that decided it."""
    from app.models.models import Reply
    from app.services.engagement_service import classify_lead_temperature
    try:
        value = _temp_value(classify_lead_temperature(db, lead))
    except Exception:                                       # noqa: BLE001
        log.exception("wholesale_ops: temperature classification failed for %s", lead.id)
        value = _temp_value(getattr(lead, "engagement_temperature", None))
    status = _temp_value(getattr(lead, "status", None)).lower()
    if value == "HOT":
        if status == "booked":
            reason = "An appointment is booked."
        elif db.query(Reply.id).filter(Reply.lead_id == lead.id, Reply.is_hot.is_(True)).first():
            reason = "A reply was classified as hot interest."
        else:
            reason = "Replied while in an urgent tier."
    elif value == "COLD":
        if status in ("dead", "dnc"):
            reason = "The record is marked %s." % ("Do Not Contact" if status == "dnc" else "dead")
        else:
            cad = getattr(lead, "cadence_state", None)
            cst = _temp_value(getattr(cad, "status", None)).lower() if cad else ""
            reason = ("The follow-up sequence finished without a response."
                      if cst == "completed" else
                      "The sequence stopped (do-not-contact)." if cst == "stopped_dnc" else
                      "No reply in 30+ days since the last contact.")
    elif value == "WARM":
        reason = "In an active follow-up sequence and has been contacted; no hot signal yet."
    else:
        reason = "Not enough activity yet to classify."
    return value, reason


def _latest_override(db, org_id: str, lead_id: str) -> Optional[WholesaleTemperatureOverride]:
    return (db.query(WholesaleTemperatureOverride)
            .filter(WholesaleTemperatureOverride.organization_id == org_id,
                    WholesaleTemperatureOverride.lead_id == lead_id)
            .order_by(WholesaleTemperatureOverride.created_at.desc(),
                      WholesaleTemperatureOverride.id.desc()).first())


def override_json(db, row: WholesaleTemperatureOverride) -> Dict[str, Any]:
    return {"id": row.id, "action": row.action, "ai_temperature": row.ai_temperature,
            "ai_reason": row.ai_reason, "human_temperature": row.human_temperature,
            "reason": row.reason, "actor_user_id": row.actor_user_id,
            "actor_name": user_name(db, row.actor_user_id), "deal_id": row.deal_id,
            "at": _iso(row.created_at)}


def temperature_payload(db, org_id: str, lead, *, history: bool = True) -> Dict[str, Any]:
    ai_value, ai_reason = ai_temperature(db, lead)
    latest = _latest_override(db, org_id, lead.id)
    human = latest.human_temperature if (latest is not None and latest.action == "set") else None
    out = {
        "lead_id": lead.id,
        "ai": {"temperature": ai_value, "reason": ai_reason},
        "human": ({"temperature": human, "reason": latest.reason,
                   "by": user_name(db, latest.actor_user_id), "at": _iso(latest.created_at)}
                  if human else None),
        "effective": human or ai_value,
        "effective_source": "human" if human else "ai",
        # The AI has moved since the person decided - shown, never applied.
        "ai_disagrees": bool(human and ai_value != human),
    }
    if history:
        rows = (db.query(WholesaleTemperatureOverride)
                .filter(WholesaleTemperatureOverride.organization_id == org_id,
                        WholesaleTemperatureOverride.lead_id == lead.id)
                .order_by(WholesaleTemperatureOverride.created_at.desc()).limit(50).all())
        out["history"] = [override_json(db, r) for r in rows]
    return out


def set_temperature(db, org_id: str, lead, user, temperature: str, reason: Optional[str],
                    deal_id: Optional[str] = None) -> WholesaleTemperatureOverride:
    t = (temperature or "").strip().upper()
    if t not in TEMPERATURES:
        raise HTTPException(status_code=422, detail="Temperature must be HOT, WARM or COLD.")
    if not (reason or "").strip():
        raise HTTPException(status_code=422, detail="Say why - the reason is kept in the audit history.")
    ai_value, ai_reason = ai_temperature(db, lead)
    row = WholesaleTemperatureOverride(
        organization_id=org_id, lead_id=lead.id, deal_id=deal_id, action="set",
        ai_temperature=ai_value, ai_reason=ai_reason, human_temperature=t,
        reason=reason.strip()[:2000], actor_user_id=getattr(user, "id", None),
        created_at=_now())
    db.add(row)
    db.flush()
    return row


def clear_temperature(db, org_id: str, lead, user, reason: Optional[str]) -> WholesaleTemperatureOverride:
    latest = _latest_override(db, org_id, lead.id)
    if latest is None or latest.action != "set":
        raise HTTPException(status_code=409, detail="There is no human override to clear.")
    ai_value, ai_reason = ai_temperature(db, lead)
    row = WholesaleTemperatureOverride(
        organization_id=org_id, lead_id=lead.id, deal_id=latest.deal_id, action="clear",
        ai_temperature=ai_value, ai_reason=ai_reason, human_temperature=None,
        reason=(reason or "Returned to the AI recommendation").strip()[:2000],
        actor_user_id=getattr(user, "id", None), created_at=_now())
    db.add(row)
    db.flush()
    return row


# ── Callback Center ─────────────────────────────────────────────────────────

def bucket_of(cb_status: str, due_at: Optional[datetime], now: datetime) -> Optional[str]:
    if cb_status == "completed":
        return "completed"
    if cb_status != "due" or due_at is None:
        return None
    if due_at < now - DUE_NOW_AFTER:
        return "overdue"
    if due_at <= now + DUE_NOW_BEFORE:
        return "due_now"
    return "upcoming"


def _lead_label(lead) -> Optional[str]:
    if lead is None:
        return None
    return " ".join(x for x in (lead.first_name, lead.last_name) if x) or None


def callback_json(db, cb: WholesaleSellerCallback, now: datetime, lead=None) -> Dict[str, Any]:
    if lead is None and cb.lead_id:
        lead = db.query(Lead).filter(Lead.id == cb.lead_id,
                                     Lead.organization_id == cb.organization_id).first()
    return {"id": cb.id, "kind": "callback", "lead_id": cb.lead_id, "deal_id": cb.deal_id,
            "property_id": cb.property_id, "seller_name": _lead_label(lead),
            "phone": getattr(lead, "phone", None),
            "dnc": _temp_value(getattr(lead, "status", None)) == "DNC" if lead is not None else None,
            "due_at": _iso(cb.due_at), "status": cb.status,
            "bucket": bucket_of(cb.status, cb.due_at, now), "notes": cb.notes,
            "outcome_note": cb.outcome_note, "assigned_to_id": cb.assigned_to_id,
            "assigned_to_name": user_name(db, cb.assigned_to_id),
            "completed_at": _iso(cb.completed_at), "source": cb.source,
            "source_ref": cb.source_ref, "is_test": bool(cb.is_test),
            "created_at": _iso(cb.created_at)}


def _exception_items(db, org_id: str, now: datetime, include_test: bool) -> List[Dict[str, Any]]:
    """Open `seller_callback_requested` exceptions not yet adopted into a
    callback. READ ONLY: the exception rows are neither changed nor deleted."""
    from app.models.wholesale_models import EXCEPTION_OPEN_STATUSES, WholesaleWorkException
    adopted = {r[0] for r in db.query(WholesaleSellerCallback.source_ref).filter(
        WholesaleSellerCallback.organization_id == org_id,
        WholesaleSellerCallback.source == "exception").all() if r[0]}
    q = (db.query(WholesaleWorkException)
         .filter(WholesaleWorkException.organization_id == org_id,
                 WholesaleWorkException.kind == "seller_callback_requested",
                 WholesaleWorkException.status.in_(EXCEPTION_OPEN_STATUSES)))
    if not include_test:
        q = q.filter(WholesaleWorkException.is_test.is_(False))
    out = []
    for ex in q.order_by(WholesaleWorkException.created_at.asc()).limit(500).all():
        if ex.id in adopted:
            continue
        lead = None
        if ex.subject_type == "lead":
            lead = db.query(Lead).filter(Lead.id == ex.subject_id,
                                         Lead.organization_id == org_id).first()
        due = ex.due_at or ex.created_at
        out.append({"id": "exc:%s" % ex.id, "kind": "requested", "exception_id": ex.id,
                    "lead_id": getattr(lead, "id", None), "deal_id": None, "property_id": None,
                    "seller_name": _lead_label(lead), "phone": getattr(lead, "phone", None),
                    "dnc": _temp_value(getattr(lead, "status", None)) == "DNC" if lead else None,
                    "due_at": _iso(due), "status": "due", "bucket": bucket_of("due", due, now),
                    "notes": ex.detail, "outcome_note": None,
                    "assigned_to_id": ex.assigned_to_id,
                    "assigned_to_name": user_name(db, ex.assigned_to_id),
                    "completed_at": None, "source": "exception", "source_ref": ex.id,
                    "is_test": bool(ex.is_test), "created_at": _iso(ex.created_at),
                    "title": ex.title})
    return out


def callback_center(db, org_id: str, *, user=None, mine: bool = False, include_test: bool = False,
                    completed_limit: int = 50, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or _now()
    q = db.query(WholesaleSellerCallback).filter(
        WholesaleSellerCallback.organization_id == org_id)
    if not include_test:
        q = q.filter(WholesaleSellerCallback.is_test.is_(False))
    if mine and user is not None:
        q = q.filter(WholesaleSellerCallback.assigned_to_id == user.id)
    open_rows = (q.filter(WholesaleSellerCallback.status == "due")
                 .order_by(WholesaleSellerCallback.due_at.asc()).limit(1000).all())
    done_rows = (q.filter(WholesaleSellerCallback.status == "completed")
                 .order_by(WholesaleSellerCallback.completed_at.desc())
                 .limit(max(1, min(int(completed_limit or 50), 200))).all())
    buckets: Dict[str, List[Dict[str, Any]]] = {b: [] for b in BUCKETS}
    for cb in open_rows + done_rows:
        item = callback_json(db, cb, now)
        if item["bucket"]:
            buckets[item["bucket"]].append(item)
    for item in _exception_items(db, org_id, now, include_test):
        if mine and user is not None and item["assigned_to_id"] != user.id:
            continue
        buckets[item["bucket"]].append(item)
    for b in ("due_now", "upcoming", "overdue"):
        buckets[b].sort(key=lambda i: (i["due_at"] is None, i["due_at"] or "", i["created_at"] or "", str(i["id"])))
    completed_total = q.filter(WholesaleSellerCallback.status == "completed").count()
    return {"now": _iso(now), "buckets": buckets,
            "counts": {b: (completed_total if b == "completed" else len(buckets[b])) for b in BUCKETS},
            "window_minutes": int(DUE_NOW_AFTER.total_seconds() // 60)}


def parse_when(raw) -> datetime:
    if isinstance(raw, datetime):
        dt = raw
    else:
        try:
            dt = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="due_at must be an ISO date-time.")
    if dt.tzinfo is not None:
        from datetime import timezone
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def create_callback(db, org_id: str, user, *, lead_id=None, deal_id=None, due_at=None,
                    notes=None, assigned_to_id=None, source="manual",
                    source_ref=None) -> WholesaleSellerCallback:
    from app.services import wholesale_service as svc
    deal = svc.get_deal(db, org_id, deal_id) if deal_id else None
    lead_id = lead_id or getattr(deal, "seller_lead_id", None)
    lead = get_lead(db, org_id, lead_id) if lead_id else None
    if deal is None and lead is None:
        raise HTTPException(status_code=422, detail="A callback needs a seller or a deal.")
    if due_at is None:
        raise HTTPException(status_code=422, detail="When should the call happen? due_at is required.")
    cb = WholesaleSellerCallback(
        organization_id=org_id, lead_id=getattr(lead, "id", None),
        deal_id=getattr(deal, "id", None), property_id=getattr(deal, "property_id", None),
        due_at=parse_when(due_at), status="due", notes=(notes or None),
        assigned_to_id=assignable_user(db, org_id, assigned_to_id) or getattr(user, "id", None),
        created_by_id=getattr(user, "id", None), source=source, source_ref=source_ref,
        is_test=bool(getattr(lead, "is_test", False) or getattr(deal, "is_test", False)),
        created_at=_now())
    db.add(cb)
    db.flush()
    return cb


def adopt_exception(db, org_id: str, user, exception_id: str, due_at=None, notes=None
                    ) -> WholesaleSellerCallback:
    """Turn a seller_callback_requested exception into a scheduled callback.
    The exception row is left exactly as it is."""
    from app.models.wholesale_models import WholesaleWorkException
    ex = (db.query(WholesaleWorkException)
          .filter(WholesaleWorkException.id == exception_id,
                  WholesaleWorkException.organization_id == org_id,
                  WholesaleWorkException.kind == "seller_callback_requested").first())
    if ex is None:
        raise HTTPException(status_code=404, detail="Callback request not found")
    existing = (db.query(WholesaleSellerCallback)
                .filter(WholesaleSellerCallback.organization_id == org_id,
                        WholesaleSellerCallback.source == "exception",
                        WholesaleSellerCallback.source_ref == ex.id).first())
    if existing is not None:
        return existing
    lead_id = ex.subject_id if ex.subject_type == "lead" else None
    return create_callback(db, org_id, user, lead_id=lead_id,
                           due_at=due_at or ex.due_at or ex.created_at or _now(),
                           notes=notes or ex.detail, assigned_to_id=ex.assigned_to_id,
                           source="exception", source_ref=ex.id)


def get_callback(db, org_id: str, callback_id: str) -> WholesaleSellerCallback:
    cb = (db.query(WholesaleSellerCallback)
          .filter(WholesaleSellerCallback.id == callback_id,
                  WholesaleSellerCallback.organization_id == org_id).first())
    if cb is None:
        raise HTTPException(status_code=404, detail="Callback not found")
    return cb


def finish_callback(db, org_id: str, user, callback_id: str, status: str,
                    note: Optional[str]) -> WholesaleSellerCallback:
    if status not in CALLBACK_STATUSES or status == "due":
        raise HTTPException(status_code=422, detail="Unknown outcome")
    if callback_id.startswith("exc:"):
        cb = adopt_exception(db, org_id, user, callback_id[4:])
    else:
        cb = get_callback(db, org_id, callback_id)
    if cb.status != "due":
        raise HTTPException(status_code=409, detail="This callback is already %s." % cb.status)
    cb.status = status
    cb.outcome_note = (note or None)
    if status == "completed":
        cb.completed_at = _now()
        cb.completed_by_id = getattr(user, "id", None)
    cb.updated_at = _now()
    db.flush()
    return cb


# ── Deal notes ──────────────────────────────────────────────────────────────

def note_json(db, n: WholesaleDealNote) -> Dict[str, Any]:
    return {"id": n.id, "deal_id": n.deal_id, "property_id": n.property_id, "lead_id": n.lead_id,
            "author_user_id": n.author_user_id, "author_name": user_name(db, n.author_user_id),
            "body": n.body, "created_at": _iso(n.created_at), "edited_at": _iso(n.edited_at)}


def list_notes(db, org_id: str, deal_id: str) -> List[Dict[str, Any]]:
    rows = (db.query(WholesaleDealNote)
            .filter(WholesaleDealNote.organization_id == org_id,
                    WholesaleDealNote.deal_id == deal_id,
                    WholesaleDealNote.deleted_at.is_(None))
            .order_by(WholesaleDealNote.created_at.desc()).limit(500).all())
    return [note_json(db, n) for n in rows]


def get_note(db, org_id: str, note_id: str) -> WholesaleDealNote:
    n = (db.query(WholesaleDealNote)
         .filter(WholesaleDealNote.id == note_id, WholesaleDealNote.organization_id == org_id,
                 WholesaleDealNote.deleted_at.is_(None)).first())
    if n is None:
        raise HTTPException(status_code=404, detail="Note not found")
    return n


def _clean_body(body) -> str:
    text = (body or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="A note cannot be empty.")
    return text[:10000]


# ── Pause AI / Take over / Resume AI ────────────────────────────────────────

def control_row(db, org_id: str, lead_id: str) -> Optional[WholesaleConversationControl]:
    return (db.query(WholesaleConversationControl)
            .filter(WholesaleConversationControl.organization_id == org_id,
                    WholesaleConversationControl.lead_id == lead_id).first())


def control_json(db, org_id: str, lead_id: str) -> Dict[str, Any]:
    row = control_row(db, org_id, lead_id)
    events = (db.query(WholesaleConversationControlEvent)
              .filter(WholesaleConversationControlEvent.organization_id == org_id,
                      WholesaleConversationControlEvent.lead_id == lead_id)
              .order_by(WholesaleConversationControlEvent.created_at.desc()).limit(25).all())
    mode = getattr(row, "mode", None) or "ai"
    paused = bool(getattr(row, "paused_ai", False))
    return {"lead_id": lead_id, "mode": mode, "paused_ai": paused,
            "ai_may_send": not (paused or mode == "human"),
            "taken_over_by_id": getattr(row, "taken_over_by_id", None),
            "taken_over_by_name": user_name(db, getattr(row, "taken_over_by_id", None)),
            "changed_at": _iso(getattr(row, "changed_at", None)),
            "history": [{"action": e.action, "by": user_name(db, e.actor_user_id),
                         "reason": e.reason, "at": _iso(e.created_at)} for e in events]}


def apply_control(db, org_id: str, lead, user, action: str, reason: Optional[str] = None
                  ) -> WholesaleConversationControl:
    if action not in ("pause", "takeover", "resume"):
        raise HTTPException(status_code=400, detail="Unknown action")
    row = control_row(db, org_id, lead.id)
    if row is None:
        row = WholesaleConversationControl(organization_id=org_id, lead_id=lead.id)
        db.add(row)
    if action == "pause":
        row.paused_ai = True
    elif action == "takeover":
        # A second person taking over from the first is allowed and audited;
        # the conversation always has exactly one named human owner.
        row.paused_ai = True
        row.mode = "human"
        row.taken_over_by_id = getattr(user, "id", None)
    else:
        row.paused_ai = False
        row.mode = "ai"
        row.taken_over_by_id = None
    row.reason = (reason or None)
    row.updated_by_id = getattr(user, "id", None)
    row.changed_at = _now()
    db.add(WholesaleConversationControlEvent(
        organization_id=org_id, lead_id=lead.id, action=action,
        actor_user_id=getattr(user, "id", None), reason=(reason or None), created_at=_now()))
    db.flush()
    return row


def ai_send_refusal(db, lead, path: Optional[str]) -> Optional[str]:
    """None, or why an automated send to this lead is refused right now.

    Called by the send gates. A MANUAL send by a person is always left to the
    other gates; anything else is refused while the conversation is paused or
    held by a human. Errors fail CLOSED for non-manual sends."""
    if lead is None or not getattr(lead, "id", None):
        return None
    if (path or "") in HUMAN_SEND_SOURCES:
        return None
    try:
        row = (db.query(WholesaleConversationControl)
               .filter(WholesaleConversationControl.organization_id == lead.organization_id,
                       WholesaleConversationControl.lead_id == lead.id).first())
    except Exception:                                       # noqa: BLE001
        log.exception("wholesale_ops: conversation control lookup failed for %s", lead.id)
        return "AI_PAUSED"
    if row is None:
        return None
    if row.mode == "human":
        return "HUMAN_TAKEOVER"
    if row.paused_ai:
        return "AI_PAUSED"
    return None


# ── Pilot controls ──────────────────────────────────────────────────────────

def pilot_row(db, org_id: str, create: bool = False) -> Optional[WholesalePilotControl]:
    row = (db.query(WholesalePilotControl)
           .filter(WholesalePilotControl.organization_id == org_id).first())
    if row is None and create:
        row = WholesalePilotControl(organization_id=org_id)
        db.add(row)
        db.flush()
    return row


def distribution_status(db, org_id: str) -> Dict[str, Any]:
    """Is anything distributed to buyers or funders automatically? Read from
    configuration - never asserted."""
    from app.services import wholesale_service as svc
    s = svc.resolve_settings(db, org_id, commit=False)
    return {
        "buyers": {"auto_distribution": False,
                   "auto_match_on_contract": bool(s.auto_match_on_contract),
                   "flow": ["Deal", "Match", "Review", "Select", "Send"],
                   "note": ("Buyer sends are manual: a person selects buyers and presses Send. "
                            "Auto-match only builds a match list for review" +
                            (" (currently ON)." if s.auto_match_on_contract else " and is OFF."))},
        "funding": {"auto_distribution": False,
                    "flow": ["Deal", "Match", "Review", "Select", "Share"],
                    "note": "Funding partners only see a deal when a person creates a submission."},
    }


def pilot_payload(db, org_id: str) -> Dict[str, Any]:
    from app.services.evosense import strategy as ST
    from app.services.evosense import common as C
    row = pilot_row(db, org_id)
    strat = None
    if row is not None and row.strategy_id:
        from app.models.evosense_models import EvoSenseStrategy
        strat = (db.query(EvoSenseStrategy)
                 .filter(EvoSenseStrategy.id == row.strategy_id,
                         EvoSenseStrategy.organization_id == org_id).first())
    ctl = None
    try:
        from app.models.evosense_models import EvoSenseControl
        ctl = db.query(EvoSenseControl).filter(EvoSenseControl.organization_id == org_id).first()
    except Exception:                                       # noqa: BLE001
        ctl = None
    return {
        "configured": row is not None,
        "status": getattr(row, "status", None) or "draft",
        "max_records": getattr(row, "max_records", None) or 250,
        "source": getattr(row, "source", None),
        "strategy_id": getattr(row, "strategy_id", None),
        "strategy": ({"id": strat.id, "name": strat.name, "status": strat.status,
                      "pilot_mode": bool(strat.pilot_mode),
                      "pilot_record_cap": ST.pilot_cap(strat) if strat.pilot_mode else None,
                      "pilot_allow_paid": bool(strat.pilot_allow_paid),
                      "pilot_spend_cap_cents": ST.pilot_spend_cap(strat) if strat.pilot_mode else None,
                      "auto_outreach": bool(ST.outreach_policy(strat).get("auto_outreach"))}
                     if strat is not None else None),
        "skip_trace_budget_cents": getattr(row, "skip_trace_budget_cents", None) or 0,
        "outreach_daily_limit": getattr(row, "outreach_daily_limit", None) or 0,
        "notes": getattr(row, "notes", None),
        "updated_at": _iso(getattr(row, "updated_at", None)),
        "updated_by": user_name(db, getattr(row, "updated_by_id", None)),
        "limits": {"hard_cap": ST.PILOT_HARD_CAP, "recommended_min": ST.PILOT_RECOMMENDED_MIN,
                   "recommended_max": ST.PILOT_HARD_CAP},
        "kill_switches": ({k: bool(getattr(ctl, k)) for k in
                           ("paused_all", "paused_discovery", "paused_paid_data", "paused_sms",
                            "paused_email", "paused_voice", "paused_ai_replies")}
                          if ctl is not None else None),
        # WHAT ENFORCES EACH CONTROL. Stated, not implied.
        "enforcement": {
            "max_records": ("Mirrored onto the linked strategy's pilot cap, which the EvoSense "
                            "hunt enforces per run." if strat is not None else
                            "Recorded. Link a strategy so the hunt enforces it."),
            "skip_trace_budget_cents": ("Mirrored onto the linked strategy's pilot spend cap. Paid data "
                                        "stays OFF unless a person allows it on the strategy."
                                        if strat is not None else "Recorded. Link a strategy to enforce."),
            "outreach_daily_limit": ("Recorded only. Pilot strategies never start outreach on their own "
                                     "(auto outreach is forced off), so no automated send exists to limit."),
            "pause_stop": ("Pausing or stopping pauses the linked strategy and sets the EvoSense "
                           "discovery switch." if strat is not None else
                           "Pausing or stopping sets the EvoSense discovery switch."),
        },
        "distribution": distribution_status(db, org_id),
        "money_note": "Budgets are in cents. %s" % C.money(getattr(row, "skip_trace_budget_cents", 0) or 0),
    }


def update_pilot(db, org_id: str, user, data: Dict[str, Any]) -> WholesalePilotControl:
    from app.services.evosense import strategy as ST
    from app.services.evosense import common as C
    row = pilot_row(db, org_id, create=True)
    problems = []
    if "max_records" in data and data["max_records"] is not None:
        try:
            n = int(data["max_records"])
        except (TypeError, ValueError):
            n = -1
        if n < 1 or n > ST.PILOT_HARD_CAP:
            problems.append("A pilot batch is 1-%s records (recommended %s-%s); it is a cap, not a target."
                            % (ST.PILOT_HARD_CAP, ST.PILOT_RECOMMENDED_MIN, ST.PILOT_HARD_CAP))
        else:
            row.max_records = n
    for f in ("skip_trace_budget_cents", "outreach_daily_limit"):
        if f in data and data[f] is not None:
            try:
                v = int(data[f])
            except (TypeError, ValueError):
                v = -1
            if v < 0:
                problems.append("%s must be zero or more." % f.replace("_", " "))
            else:
                setattr(row, f, v)
    if "status" in data and data["status"] is not None:
        if data["status"] not in PILOT_STATUSES:
            problems.append("status must be one of %s." % ", ".join(PILOT_STATUSES))
        else:
            row.status = data["status"]
    if "source" in data:
        row.source = (data.get("source") or None)
    if "notes" in data:
        row.notes = (data.get("notes") or None)
    strat = None
    if "strategy_id" in data:
        if data["strategy_id"]:
            strat = ST.get(db, org_id, data["strategy_id"])     # 404 across tenants
            row.strategy_id = strat.id
        else:
            row.strategy_id = None
    if problems:
        raise HTTPException(status_code=422, detail=" ".join(problems))
    if strat is None and row.strategy_id:
        strat = ST.get(db, org_id, row.strategy_id)
    if strat is not None:
        # The linked strategy becomes a PILOT: manual-only, no outreach, the
        # cap and spend cap mirrored. Paid data is NOT switched on here.
        ST.apply(strat, {"pilot_mode": True, "pilot_max_properties": row.max_records,
                         "pilot_max_spend_cents": row.skip_trace_budget_cents})
        if row.status in ("paused", "stopped") and strat.status == "active":
            strat.status = "paused"
    if row.status in ("paused", "stopped"):
        C.controls(db, org_id).paused_discovery = True
    row.updated_by_id = getattr(user, "id", None)
    row.updated_at = _now()
    db.flush()
    return row


# ── Skip trace: real numbers from the ledgers ───────────────────────────────

def skip_trace_summary(db, org_id: str) -> Dict[str, Any]:
    from sqlalchemy import func
    from app.models.evosense_models import EvoSenseCostEntry, EvoSenseEnrichmentDecision
    from app.models.wholesale_models import WholesaleEnrichmentRequest
    from app.services.evosense import common as C

    led = (db.query(EvoSenseCostEntry.provider_key, EvoSenseCostEntry.status,
                    EvoSenseCostEntry.success, func.count(EvoSenseCostEntry.id),
                    func.coalesce(func.sum(EvoSenseCostEntry.total_cents), 0))
           .filter(EvoSenseCostEntry.organization_id == org_id,
                   EvoSenseCostEntry.capability == C.CONTACT_ENRICHMENT,
                   EvoSenseCostEntry.is_test.is_(False))
           .group_by(EvoSenseCostEntry.provider_key, EvoSenseCostEntry.status,
                     EvoSenseCostEntry.success).all())
    providers: Dict[str, Dict[str, Any]] = {}
    for prov, st, success, n, cents in led:
        p = providers.setdefault(prov, {"attempts": 0, "charged_cents": 0, "hits": 0,
                                        "no_result": 0, "errors": 0, "refunded": 0})
        p["attempts"] += n
        if st in ("charged", "failed_charged"):
            p["charged_cents"] += int(cents or 0)
        if st == "failed_refunded":
            p["refunded"] += n
            p["errors"] += n
        elif st == "failed_charged":
            p["errors"] += n
        elif success is True:
            p["hits"] += n
        elif success is False:
            p["no_result"] += n
    for p in providers.values():
        p["cost_per_hit_cents"] = (round(p["charged_cents"] / p["hits"], 2) if p["hits"] else None)

    wreq = (db.query(WholesaleEnrichmentRequest.status, func.count(WholesaleEnrichmentRequest.id),
                     func.coalesce(func.sum(WholesaleEnrichmentRequest.cost_cents), 0))
            .filter(WholesaleEnrichmentRequest.organization_id == org_id)
            .group_by(WholesaleEnrichmentRequest.status).all())
    requests = {st: {"count": n, "cost_cents": int(c or 0)} for st, n, c in wreq}
    # Retries: more than one request for the same property.
    retried = (db.query(WholesaleEnrichmentRequest.property_id)
               .filter(WholesaleEnrichmentRequest.organization_id == org_id,
                       WholesaleEnrichmentRequest.property_id.isnot(None))
               .group_by(WholesaleEnrichmentRequest.property_id)
               .having(func.count(WholesaleEnrichmentRequest.id) > 1).count())
    dec = (db.query(EvoSenseEnrichmentDecision.decision, EvoSenseEnrichmentDecision.outcome,
                    func.count(EvoSenseEnrichmentDecision.id))
           .filter(EvoSenseEnrichmentDecision.organization_id == org_id,
                   EvoSenseEnrichmentDecision.capability == C.CONTACT_ENRICHMENT)
           .group_by(EvoSenseEnrichmentDecision.decision, EvoSenseEnrichmentDecision.outcome).all())
    decisions: Dict[str, int] = {}
    outcomes: Dict[str, int] = {}
    for d, o, n in dec:
        decisions[d] = decisions.get(d, 0) + n
        if o:
            outcomes[o] = outcomes.get(o, 0) + n

    # What the configured vendors cost per hit, from their adapters.
    prices = []
    try:
        from app.services.evosense import vendors as V
        for name in dir(V):
            cls = getattr(V, name)
            if isinstance(cls, type) and C.CONTACT_ENRICHMENT in (getattr(cls, "capabilities", ()) or ()) \
                    and getattr(cls, "key", None):
                prices.append({"provider": cls.key, "label": getattr(cls, "label", cls.key),
                               "cost_cents_per_hit": (getattr(cls, "costs", {}) or {}).get(C.CONTACT_ENRICHMENT),
                               "charge_on_miss": getattr(cls, "charge_on_miss", None),
                               "price_confirmed": getattr(cls, "price_confirmed", None)})
    except Exception:                                       # noqa: BLE001
        log.exception("wholesale_ops: vendor price listing failed")
    target = 1   # Derrick's ~$0.01/record target, in cents
    cheapest = min((p["cost_cents_per_hit"] for p in prices if p["cost_cents_per_hit"] is not None),
                   default=None)
    total = sum(p["charged_cents"] for p in providers.values())
    return {
        "ledger": {"providers": providers, "total_charged_cents": total,
                   "attempts": sum(p["attempts"] for p in providers.values()),
                   "hits": sum(p["hits"] for p in providers.values()),
                   "no_result": sum(p["no_result"] for p in providers.values()),
                   "errors": sum(p["errors"] for p in providers.values())},
        "wholesale_requests": requests,
        "retried_properties": retried,
        "decisions": decisions,
        "decision_outcomes": outcomes,
        "duplicate_billing_protection": {
            "skipped_existing_data": int(decisions.get("USE_EXISTING_DATA", 0)),
            "retry_later": int(decisions.get("RETRY_LATER", 0)),
            "how": ("A property / owner already looked up is not bought again (USE_EXISTING_DATA); "
                    "a failed call is refunded, not charged (failed_refunded)."),
        },
        "vendor_prices": prices,
        "target_cents_per_record": target,
        "target_viable": (cheapest is not None and cheapest <= target),
        "target_note": ("No configured skip-trace provider is priced at or below $0.01 per record; "
                        "the cheapest configured is %s per hit." % C.money(cheapest)
                        if cheapest is not None and cheapest > target else
                        "A configured provider is priced at or below $0.01 per record."
                        if cheapest is not None else "No skip-trace provider price is configured."),
    }


# ── Voice and voicemail: what is actually stored ────────────────────────────

def _inbound_voicemail_count(db, org_id, lead_id):
    try:
        from app.models.telephony_models import Voicemail
        return (db.query(Voicemail)
                .filter(Voicemail.organization_id == org_id, Voicemail.lead_id == lead_id).count())
    except Exception:  # noqa: BLE001 - table absent on an old database: nothing recorded
        return 0


def calls_payload(db, org_id: str, lead) -> Dict[str, Any]:
    from app.models.models import VoiceCall
    from app.services import compliance_service
    rows = (db.query(VoiceCall)
            .filter(VoiceCall.organization_id == org_id, VoiceCall.lead_id == lead.id)
            .order_by(VoiceCall.created_at.desc()).limit(50).all())
    calls = [{"id": c.id, "direction": c.direction or "outbound", "status": c.status,
              "answered_by": getattr(c, "answered_by", None), "outcome": c.outcome,
              "duration_seconds": c.duration_seconds, "voicemail_left": bool(c.voicemail_left),
              "voicemail_transcript": c.voicemail_transcript,
              "has_recording": bool(c.recording_url or c.recording_sid),
              "has_transcript": bool(c.transcript), "created_at": _iso(c.created_at)}
             for c in rows]
    phone = compliance_service.usable_us_phone(lead.phone) if lead.phone else None
    from app.services import communication_eligibility as CE
    verdict = CE.voice(db, org_id, lead.phone, lead=lead)
    return {
        "calls": calls,
        "voicemail": {
            "outbound_voicemails_left": sum(1 for c in calls if c["voicemail_left"]),
            # Inbound voicemail is recorded by the telephony layer
            # (app/models/telephony_models.Voicemail) when the organization has
            # an inbound voice number routed to /voice/inbound.
            "inbound_voicemail_capture": "stored",
            "inbound_voicemails": _inbound_voicemail_count(db, org_id, lead.id),
            "note": ("Inbound voicemail is recorded when this organization's number sends calls to the "
                     "platform; playback is in the conversation thread."),
        },
        "dialer": {
            # The in-app human dialer is the Twilio click-to-call bridge
            # (POST /calls/human); `tel` stays as a fallback for the operator's
            # own phone and is offered only when eligibility passes.
            "kind": "bridge",
            "tel": ("tel:+%s" % phone) if (phone and verdict.get("eligible")) else None,
            "eligible": bool(verdict.get("eligible")),
            "state": verdict.get("state"),
            "reasons": [c["label"] for c in verdict.get("checks", []) if not c.get("ok")],
            "note": "Click-to-call bridge via the organization's number: your phone rings first, then the seller.",
        },
    }
