"""#452 Phase 4 - what happens AFTER EvoSys delivers a booked appointment.

* The cleaning client reports outcomes (engine.client_outcome) - here we add
  the won-contract details, a customer-safe history and period reporting.
* "Request EvoSys Follow-Up" becomes a task for EvoSys staff. It never sends
  an email, text or call by itself, and an identical open request is never
  duplicated.
* Reported value is the client's word. Confirmed value is what EvoSys has
  confirmed. Neither is collected revenue, and nothing here says it is.
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.acquisition_models import AcquisitionEvent, AcquisitionFollowUp, AcquisitionOpportunity
from app.models.models import Organization, User
from app.services.acquisition import common as C
from app.services.acquisition import engine as E

FOLLOWUP_REASONS = [
    ("prospect_response", "Need the prospect to respond"),
    ("estimate_followup", "Follow up on my estimate"),
    ("reschedule", "Reschedule requested"),
    ("did_not_attend", "The business didn't attend"),
    ("missing_info", "Need missing information"),
    ("second_appointment", "Request a second appointment"),
    ("other", "Other question"),
]
REASON_LABEL = dict(FOLLOWUP_REASONS)
FOLLOWUP_STATUSES = ("open", "in_progress", "done", "closed")
OPEN = ("open", "in_progress")
CONTRACT_TYPES = ("recurring", "one_time", "trial")


# ── follow-up requests ─────────────────────────────────────────────────────
def followup_json(f: AcquisitionFollowUp, names: Optional[Dict[str, str]] = None, business: Optional[str] = None,
                  org_name: Optional[str] = None) -> Dict[str, Any]:
    names = names or {}
    return {"id": f.id, "organization_id": f.organization_id, "client": org_name, "opportunity_id": f.opportunity_id,
            "business_name": business, "reason": f.reason, "reason_label": REASON_LABEL.get(f.reason, f.reason),
            "note": f.note, "status": f.status, "requested_by": names.get(f.requested_by_id),
            "assigned_to": names.get(f.assigned_to_id), "resolution_note": f.resolution_note,
            "resolved_at": C.iso(f.resolved_at), "created_at": C.iso(f.created_at), "updated_at": C.iso(f.updated_at)}


def request_followup(db: Session, org_id: str, actor: User, data: Dict[str, Any]) -> Tuple[AcquisitionFollowUp, bool]:
    """Client asks EvoSys to follow up. Returns (task, created). An identical
    open request (same appointment + reason) is returned, not duplicated."""
    reason = (data.get("reason") or "").strip()
    if reason not in REASON_LABEL:
        raise HTTPException(422, "Choose a reason")
    opp_id = data.get("opportunity_id") or None
    if opp_id:
        E.load_opp(db, org_id, opp_id, client=True)        # 404 unless delivered to THIS client
    note = (data.get("note") or "").strip()[:2000] or None
    if reason == "other" and not note:
        raise HTTPException(422, "Tell us what you need")
    same = (db.query(AcquisitionFollowUp)
            .filter(AcquisitionFollowUp.organization_id == org_id, AcquisitionFollowUp.reason == reason,
                    AcquisitionFollowUp.opportunity_id == opp_id if opp_id else AcquisitionFollowUp.opportunity_id.is_(None),
                    AcquisitionFollowUp.status.in_(OPEN)).first())
    if same is not None:
        if note and note not in (same.note or ""):
            same.note = ((same.note + "\n\n") if same.note else "") + note
            db.commit()
        return same, False
    f = AcquisitionFollowUp(organization_id=org_id, opportunity_id=opp_id, reason=reason, note=note,
                            status="open", requested_by_id=actor.id)
    db.add(f)
    db.add(AcquisitionEvent(organization_id=org_id, opportunity_id=opp_id, kind="followup_requested",
                            actor_id=actor.id, note=REASON_LABEL[reason]))
    db.commit()
    return f, True


def _names(db: Session, ids) -> Dict[str, str]:
    ids = [i for i in set(ids) if i]
    if not ids:
        return {}
    return {u.id: (u.full_name or u.email) for u in db.query(User).filter(User.id.in_(ids)).all()}


def _business(db: Session, opp_ids) -> Dict[str, str]:
    ids = [i for i in set(opp_ids) if i]
    if not ids:
        return {}
    from app.models.models import Lead
    out = {}
    for o, l in (db.query(AcquisitionOpportunity, Lead).join(Lead, Lead.id == AcquisitionOpportunity.lead_id)
                 .filter(AcquisitionOpportunity.id.in_(ids)).all()):
        out[o.id] = E.opp_json(o, l, None, full=False)["business_name"]
    return out


def list_followups(db: Session, org_ids: Optional[List[str]] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
    q = db.query(AcquisitionFollowUp)
    if org_ids is not None:
        if not org_ids:
            return []
        q = q.filter(AcquisitionFollowUp.organization_id.in_(org_ids))
    if status == "open":
        q = q.filter(AcquisitionFollowUp.status.in_(OPEN))
    elif status:
        q = q.filter(AcquisitionFollowUp.status == status)
    rows = q.order_by(AcquisitionFollowUp.created_at.desc()).limit(300).all()
    names = _names(db, [r.requested_by_id for r in rows] + [r.assigned_to_id for r in rows])
    biz = _business(db, [r.opportunity_id for r in rows])
    orgs = {o.id: o.name for o in db.query(Organization).filter(
        Organization.id.in_({r.organization_id for r in rows} or {""})).all()}
    return [followup_json(r, names, biz.get(r.opportunity_id), orgs.get(r.organization_id)) for r in rows]


def update_followup(db: Session, org_id: str, fid: str, actor: User, data: Dict[str, Any]) -> AcquisitionFollowUp:
    f = (db.query(AcquisitionFollowUp)
         .filter(AcquisitionFollowUp.id == fid, AcquisitionFollowUp.organization_id == org_id).first())
    if f is None:
        raise HTTPException(404, "Follow-up not found")
    st = data.get("status")
    if st is not None:
        if st not in FOLLOWUP_STATUSES:
            raise HTTPException(422, "Unknown status")
        if st in ("done", "closed") and not (data.get("resolution_note") or f.resolution_note):
            raise HTTPException(422, "Say what was done")
        f.status = st
        if st in ("done", "closed"):
            f.resolved_at, f.resolved_by_id = datetime.utcnow(), actor.id
    if data.get("resolution_note"):
        f.resolution_note = str(data["resolution_note"]).strip()[:2000]
    if "assign_to_me" in data and data["assign_to_me"]:
        f.assigned_to_id = actor.id
    db.add(AcquisitionEvent(organization_id=org_id, opportunity_id=f.opportunity_id, kind="followup_" + f.status,
                            actor_id=actor.id, note=(f.resolution_note or "")[:500] or None))
    db.commit()
    return f


# ── won contracts ──────────────────────────────────────────────────────────
def apply_contract(opp: AcquisitionOpportunity, data: Dict[str, Any]) -> None:
    """Won-contract details as the client reports them."""
    ct = (data.get("contract_type") or "").strip() or None
    if ct is not None and ct not in CONTRACT_TYPES:
        raise HTTPException(422, "Unknown contract type")
    opp.contract_type = ct or opp.contract_type
    if data.get("service_frequency"):
        opp.service_frequency = str(data["service_frequency"]).strip()[:40]
    if data.get("contract_value") not in (None, ""):
        opp.contract_value = C.money(data.get("contract_value"))
    if data.get("contract_start"):
        try:
            opp.contract_start = datetime.fromisoformat(str(data["contract_start"])[:10])
        except ValueError:
            raise HTTPException(422, "Start date must be YYYY-MM-DD")


def confirm_won(db: Session, org_id: str, opp_id: str, actor: User) -> AcquisitionOpportunity:
    opp = E.load_opp(db, org_id, opp_id)
    if opp.stage != C.WON:
        raise HTTPException(409, "Only a won contract can be confirmed")
    if opp.won_confirmed_at is None:
        opp.won_confirmed_at, opp.won_confirmed_by_id = datetime.utcnow(), actor.id
        db.add(AcquisitionEvent(organization_id=org_id, opportunity_id=opp.id, kind="won_confirmed",
                                actor_id=actor.id, note="Contract confirmed by EvoSys"))
        db.commit()
    return opp


def contract_json(opp: AcquisitionOpportunity) -> Dict[str, Any]:
    return {"contract_type": opp.contract_type, "service_frequency": opp.service_frequency,
            "contract_value": opp.contract_value, "monthly_value": opp.monthly_value,
            "contract_start": C.iso(opp.contract_start), "confirmed": opp.won_confirmed_at is not None,
            "confirmed_at": C.iso(opp.won_confirmed_at)}


# ── customer-safe history ──────────────────────────────────────────────────
CUSTOMER_EVENT_KINDS = ("stage", "rescheduled", "followup_requested", "followup_in_progress", "followup_done",
                        "followup_closed", "won_confirmed")


def customer_history(db: Session, opp: AcquisitionOpportunity) -> List[Dict[str, Any]]:
    """What happened to THIS appointment from the moment it was booked. Earlier
    research, outreach and conversation stay EvoSys-internal."""
    if opp.booked_at is None:
        return []
    rows = (db.query(AcquisitionEvent)
            .filter(AcquisitionEvent.organization_id == opp.organization_id,
                    AcquisitionEvent.opportunity_id == opp.id, AcquisitionEvent.kind.in_(CUSTOMER_EVENT_KINDS))
            .order_by(AcquisitionEvent.created_at.asc()).all())
    out = []
    for e in rows:
        if e.kind == "stage" and e.to_stage not in C.CLIENT_STAGES:
            continue
        label = (C.STAGE_LABEL.get(e.to_stage, e.to_stage) if e.kind == "stage"
                 else {"rescheduled": "Appointment rescheduled", "followup_requested": "Follow-up requested", "followup_in_progress": "EvoSys is following up",
                       "followup_done": "Follow-up completed", "followup_closed": "Follow-up closed",
                       "won_confirmed": "Contract confirmed by EvoSys"}[e.kind])
        out.append({"kind": e.kind, "label": label, "at": C.iso(e.created_at),
                    "note": e.note if e.kind != "stage" or e.to_stage != C.BOOKED else None})
    return out


# ── reporting ──────────────────────────────────────────────────────────────
PERIODS = {"7d": "Last 7 days", "month": "This month", "last_month": "Last month", "90d": "Last 90 days",
           "all": "All time", "custom": "Custom range"}


def period_range(period: str, start: Optional[str] = None, end: Optional[str] = None,
                 now: Optional[datetime] = None) -> Tuple[Optional[datetime], Optional[datetime]]:
    now = now or datetime.utcnow()
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if period == "7d":
        return now - timedelta(days=7), now
    if period == "90d":
        return now - timedelta(days=90), now
    if period == "month":
        return first, now
    if period == "last_month":
        prev = (first - timedelta(days=1)).replace(day=1)
        return prev, first
    if period == "custom":
        try:
            a = datetime.fromisoformat(str(start)[:10])
            b = datetime.fromisoformat(str(end)[:10]) + timedelta(days=1)
        except (TypeError, ValueError):
            raise HTTPException(422, "Give a start and end date (YYYY-MM-DD)")
        if b <= a:
            raise HTTPException(422, "The end date must be after the start date")
        return a, b
    if period == "all":
        return None, None
    raise HTTPException(422, "Unknown period")


def _in(t, a, b) -> bool:
    return t is not None and (a is None or t >= a) and (b is None or t < b)


def client_report(db: Session, org_id: str, a: Optional[datetime], b: Optional[datetime]) -> Dict[str, Any]:
    """Each number counts a DIFFERENT event in the period: appointments booked,
    visits held, estimates sent, contracts won. A reschedule moves the same
    appointment; it is never a second delivered appointment."""
    rows = [o for o in db.query(AcquisitionOpportunity).filter(AcquisitionOpportunity.organization_id == org_id).all()
            if E.delivered(o)]
    booked = [o for o in rows if _in(o.booked_at, a, b)]
    held = [o for o in rows if _in(o.held_at, a, b)]
    quoted = [o for o in rows if _in(o.quoted_at, a, b)]
    won = [o for o in rows if o.stage == C.WON and _in(o.closed_at, a, b)]
    lost = [o for o in rows if o.stage == C.LOST and _in(o.closed_at, a, b)]
    no_show_ids = set()
    if rows:
        for (oid,) in (db.query(AcquisitionEvent.opportunity_id)
                       .filter(AcquisitionEvent.organization_id == org_id, AcquisitionEvent.kind == "stage",
                               AcquisitionEvent.to_stage == C.NO_SHOW,
                               *( [AcquisitionEvent.created_at >= a] if a else []),
                               *( [AcquisitionEvent.created_at < b] if b else [])).distinct().all()):
            no_show_ids.add(oid)
    ids = {o.id for o in rows}
    by_seg: Dict[str, int] = {}
    for o in booked:
        k = o.segment or "other"
        by_seg[k] = by_seg.get(k, 0) + 1
    days = [(o.booked_at - o.created_at).days for o in booked if o.created_at and o.booked_at]
    upcoming = [o for o in rows if o.stage == C.BOOKED]
    reported = round(sum(o.monthly_value or 0 for o in won), 2)
    confirmed = round(sum(o.monthly_value or 0 for o in won if o.won_confirmed_at), 2)
    return {
        "appointments_booked": len(booked), "visits_held": len(held), "no_shows": len(no_show_ids & ids),
        "estimates_sent": len(quoted), "contracts_won": len(won), "contracts_lost": len(lost),
        "upcoming_now": len(upcoming),
        "close_rate": (round(len(won) / len(held), 3) if held else None),
        "reported_monthly_value": reported, "confirmed_monthly_value": confirmed,
        "forecast_monthly_value": round(sum(o.monthly_value or 0 for o in rows
                                            if o.stage == C.QUOTED and _in(o.quoted_at, a, b)), 2),
        "avg_days_to_appointment": (round(sum(days) / len(days), 1) if days else None),
        "by_segment": [{"segment": k, "label": C.SEGMENT_LABEL.get(k, "Other"), "count": v}
                       for k, v in sorted(by_seg.items(), key=lambda kv: -kv[1])],
        "notes": "Each number counts a different step. Reported value is what the client told us; "
                 "confirmed value is what EvoSys confirmed. Neither is collected revenue.",
    }


def all_clients_report(db: Session, a, b) -> Dict[str, Any]:
    from app.services.acquisition.hub import is_test_org
    rows = []
    for o in E.enabled_orgs(db):
        r = client_report(db, o.id, a, b)
        rows.append(dict(r, organization_id=o.id, name=o.name, is_test=is_test_org(o.name)))
    real = [r for r in rows if not r["is_test"]]
    keys = ("appointments_booked", "visits_held", "no_shows", "estimates_sent", "contracts_won", "contracts_lost",
            "upcoming_now", "reported_monthly_value", "confirmed_monthly_value", "forecast_monthly_value")
    tot = {k: round(sum(r[k] for r in real), 2) if "value" in k else sum(r[k] for r in real) for k in keys}
    tot["close_rate"] = round(tot["contracts_won"] / tot["visits_held"], 3) if tot["visits_held"] else None
    return {"totals": tot, "clients": rows,
            "note": "Totals include real clients only; TEST workspaces are listed but never counted."}
