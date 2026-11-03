"""SMS Recovery Queue API (#482) - platform-shared, tenant-scoped.

Every query is filtered to the caller's active workspace; an item id from
another workspace is "not found". Reading needs a workspace seat (a
spectator reads too, with names masked); every action needs a manager and
is written to the audit log AND the item's own history. Nothing here sends a
text or an email, and there is no bulk action.
"""
import csv
import io
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.deps import get_db, require_tenant_or_observer, require_tenant_user
from app.models.models import EmailMessage, Lead, Message, Reply, User
from app.models.sms_recovery_models import (HELD_STATES, SmsRecoveryEvent, SmsRecoveryItem,
                                            SmsRecoveryPolicy)
from app.services import lead_scope
from app.services import sms_recovery as R
from app.services import sms_recovery_policy as P

router = APIRouter(prefix="/sms-recovery", tags=["sms-recovery"])

ACTIONS = ("request_review", "mark_capable", "mark_not_capable", "release", "route_email")


def _org(db: Session, user: User) -> str:
    oid = lead_scope.active_workspace_org_id(user, db) or user.organization_id
    if not oid:
        raise HTTPException(status_code=404, detail="No workspace selected.")
    return oid


def _manager(db: Session, user: User) -> bool:
    try:
        return bool(lead_scope.is_manager_here(user, db))
    except Exception:                                    # noqa: BLE001
        return False


def _mask_phone(p: Optional[str]) -> Optional[str]:
    d = "".join(ch for ch in (p or "") if ch.isdigit())
    return ("•••-%s" % d[-4:]) if len(d) >= 4 else None


def _mask_name(first: Optional[str], last: Optional[str]) -> str:
    f = (first or "").strip().title()
    l = (last or "").strip()
    return ("%s %s." % (f, l[:1].upper())).strip() if (f or l) else "Unnamed"


def _sid_tail(sid: Optional[str]) -> Optional[str]:
    return ("…" + sid[-6:]) if sid else None


def _iso(t) -> Optional[str]:
    return t.isoformat() + "Z" if t else None


def _range(date_from: Optional[str], date_to: Optional[str]):
    try:
        start = datetime.fromisoformat(date_from) if date_from else datetime.utcnow() - timedelta(days=30)
        end = datetime.fromisoformat(date_to) + timedelta(days=1) if date_to else datetime.utcnow() + timedelta(minutes=1)
    except ValueError:
        raise HTTPException(status_code=422, detail="Dates must be YYYY-MM-DD.")
    return start, end


def _context(db: Session, org_id: str, lead_ids):
    """campaign / location for each family (program contacts), when known."""
    out = {}
    if not lead_ids:
        return out
    try:
        from app.models.location_models import Location
        from app.models.program_models import ProgramSourceRecord
        recs = (db.query(ProgramSourceRecord.lead_id, ProgramSourceRecord.campaign_family,
                         ProgramSourceRecord.location_id)
                .filter(ProgramSourceRecord.organization_id == org_id,
                        ProgramSourceRecord.lead_id.in_(lead_ids)).all())
        locs = {i: n for i, n in db.query(Location.id, Location.name).filter(
            Location.id.in_({r[2] for r in recs if r[2]} or {"-"})).all()}
        for lid, fam, loc in recs:
            out.setdefault(lid, {"campaign": fam, "location_id": loc, "location": locs.get(loc)})
    except Exception:                                    # noqa: BLE001
        pass
    return out


def _queue(db: Session, org_id: str, *, start, end, manager: bool):
    fails = (db.query(Message).join(Lead, Lead.id == Message.lead_id)
             .filter(Lead.organization_id == org_id, Message.sent_at >= start, Message.sent_at < end,
                     func.lower(Message.delivery_status).in_(R.FAILED_STATUSES))
             .order_by(Message.sent_at).all())
    items = (db.query(SmsRecoveryItem).filter(SmsRecoveryItem.organization_id == org_id)
             .filter((SmsRecoveryItem.open_lead_id.isnot(None)) | (SmsRecoveryItem.opened_at >= start))
             .order_by(SmsRecoveryItem.opened_at).all())
    by_lead = defaultdict(list)
    for m in fails:
        by_lead[m.lead_id].append(m)
    latest_item = {}
    for it in items:
        latest_item[it.lead_id] = it if (it.open_lead_id or it.lead_id not in latest_item
                                         or latest_item[it.lead_id].open_lead_id is None) else latest_item[it.lead_id]
    ids = list(set(by_lead) | set(latest_item))
    leads = {l.id: l for l in db.query(Lead).filter(Lead.organization_id == org_id, Lead.id.in_(ids or ["-"])).all()}
    counts = defaultdict(Counter)
    for lid, st, n in (db.query(Message.lead_id, func.lower(Message.delivery_status), func.count(Message.id))
                       .filter(Message.lead_id.in_(ids or ["-"])).group_by(Message.lead_id,
                                                                           func.lower(Message.delivery_status)).all()):
        counts[lid][st or "pending"] += n
    owners = {u.id: (u.full_name or u.email) for u in db.query(User).filter(
        User.id.in_({l.assigned_to_id for l in leads.values() if l.assigned_to_id} or {"-"})).all()}
    ctx = _context(db, org_id, ids)
    _v, rules = P.effective(db, org_id)
    rows = []
    for lid in ids:
        lead = leads.get(lid)
        if lead is None:
            continue
        ms = by_lead.get(lid, [])
        it = latest_item.get(lid)
        last = ms[-1] if ms else None
        code = (last.error_code if last else None) or (it.error_code if it else None)
        rule = P.rule_for(rules, code)
        c = counts[lid]
        if it is not None and it.open_lead_id:
            kind = "held"
        elif it is not None:
            kind = "resolved"
        else:
            kind = {P.INVESTIGATE: "investigate", P.CONTINUE_SCHEDULE: "watch"}.get(rule["action"], "watch")
        fb = R.email_fallback(db, lead)
        rows.append({
            "lead_id": lid, "item_id": it.id if it else None, "kind": kind,
            "name": ("%s %s" % (lead.first_name or "", lead.last_name or "")).strip().title() if manager else None,
            "name_masked": _mask_name(lead.first_name, lead.last_name), "phone": _mask_phone(lead.phone),
            "campaign": (ctx.get(lid) or {}).get("campaign"), "location": (ctx.get(lid) or {}).get("location"),
            "location_id": (ctx.get(lid) or {}).get("location_id"),
            "owner": owners.get(lead.assigned_to_id), "owner_id": lead.assigned_to_id,
            "latest_error": {"code": code, "label": rule.get("label"), "action": rule["action"],
                             "note": rule.get("note"),
                             "at": _iso(last.delivery_status_at or last.sent_at) if last else _iso(it.failed_at if it else None),
                             "provider_status": (last.delivery_status if last else (it.provider_status if it else None)),
                             "sid": _sid_tail(last.twilio_sid if last else (it.provider_sid if it else None))},
            "codes": sorted({m.error_code for m in ms if m.error_code}),
            "attempts": sum(c.values()), "failed": c["undelivered"] + c["failed"], "delivered": c["delivered"],
            "phone_type": lead.phone_line_type or "not checked",
            "fallback": fb,
            "hold": None if it is None else {
                "state": it.state, "review_status": it.review_status, "disposition": it.disposition,
                "opened_at": _iso(it.opened_at), "closed_at": _iso(it.closed_at), "policy": it.policy_version,
                "action": it.policy_action, "prevented": it.prevented or 0, "failures": it.failures or 0,
                "evidence": it.evidence if manager else None, "email_fallback": it.email_fallback},
            "opted_out": R.opted_out(db, lead),
        })
    return rows, fails, items


def _metrics(db: Session, org_id: str, rows, fails, items, start, end) -> dict:
    sent = (db.query(func.lower(Message.delivery_status), func.count(Message.id), func.count(Message.twilio_sid))
            .join(Lead, Lead.id == Message.lead_id)
            .filter(Lead.organization_id == org_id, Message.sent_at >= start, Message.sent_at < end)
            .group_by(func.lower(Message.delivery_status)).all())
    by = {s or "pending": n for s, n, _ in sent}
    held_ids = [it for it in items if it.open_lead_id]
    fallback_used = 0
    for it in items:
        if db.query(EmailMessage.id).filter(EmailMessage.lead_id == it.lead_id, EmailMessage.sent_at >= it.opened_at,
                                            EmailMessage.status.notin_(("failed", "bounced"))).first():
            fallback_used += 1
    now = datetime.utcnow()
    return {
        "since": _iso(start),
        "families_affected": len({m.lead_id for m in fails}),
        "failed_by_code": dict(Counter(m.error_code or "none" for m in fails)),
        "attempted": sum(by.values()),
        "accepted_by_provider": sum(n for _s, _n, n in sent),
        "delivered": by.get("delivered", 0),
        "failed": by.get("undelivered", 0) + by.get("failed", 0),
        "pending": sum(n for s, n in by.items() if s not in ("delivered", "undelivered", "failed")),
        "holds_open": len(held_ids),
        "holds_resolved": len([it for it in items if not it.open_lead_id]),
        "repeat_attempts_prevented": sum(it.prevented or 0 for it in items),
        "email_fallback_used": fallback_used,
        "outcomes": dict(Counter(it.disposition for it in items if it.disposition)),
        "bottlenecks": {
            "waiting_review_over_a_day": len([it for it in held_ids if it.review_status in ("open", "review_requested")
                                              and it.opened_at < now - timedelta(days=1)]),
            "held_with_no_email_fallback": len([r for r in rows if r["kind"] == "held" and not r["fallback"]["eligible"]]),
            "verified_ready_to_release": len([it for it in held_ids if it.review_status == "verified_capable"]),
        },
    }


@router.get("/queue")
def queue(code: Optional[str] = None, kind: Optional[str] = Query(None, description="held/resolved/watch/investigate"),
          campaign: Optional[str] = None, location_id: Optional[str] = None, owner_id: Optional[str] = None,
          fallback: Optional[str] = Query(None, description="eligible/none"), outcome: Optional[str] = None,
          date_from: Optional[str] = None, date_to: Optional[str] = None,
          db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    org_id = _org(db, user)
    manager = _manager(db, user)
    start, end = _range(date_from, date_to)
    rows, fails, items = _queue(db, org_id, start=start, end=end, manager=manager)
    metrics = _metrics(db, org_id, rows, fails, items, start, end)
    options = {"codes": sorted({c for r in rows for c in r["codes"]} | set(P.TRACKED_CODES)),
               "campaigns": sorted({r["campaign"] for r in rows if r["campaign"]}),
               "locations": sorted({(r["location_id"], r["location"]) for r in rows if r["location_id"]},
                                   key=lambda x: x[1] or ""),
               "owners": sorted({(r["owner_id"], r["owner"]) for r in rows if r["owner_id"]}, key=lambda x: x[1] or ""),
               "outcomes": sorted({r["hold"]["disposition"] for r in rows if r["hold"] and r["hold"]["disposition"]})}
    if code:
        rows = [r for r in rows if code in r["codes"] or r["latest_error"]["code"] == code]
    if kind:
        rows = [r for r in rows if r["kind"] == kind]
    if campaign:
        rows = [r for r in rows if r["campaign"] == campaign]
    if location_id:
        rows = [r for r in rows if r["location_id"] == location_id]
    if owner_id:
        rows = [r for r in rows if r["owner_id"] == owner_id]
    if fallback in ("eligible", "none"):
        rows = [r for r in rows if r["fallback"]["eligible"] == (fallback == "eligible")]
    if outcome:
        rows = [r for r in rows if r["hold"] and r["hold"]["disposition"] == outcome]
    order = {"held": 0, "investigate": 1, "watch": 2, "resolved": 3}
    rows.sort(key=lambda r: r["latest_error"]["at"] or "", reverse=True)       # newest first ...
    rows.sort(key=lambda r: order.get(r["kind"], 9))                            # ... within each group
    version, rules = P.effective(db, org_id)
    return {"rows": rows, "metrics": metrics, "options": {**options,
            "locations": [{"id": a, "name": b} for a, b in options["locations"]],
            "owners": [{"id": a, "name": b} for a, b in options["owners"]]},
            "policy": {"version": version, "rules": rules}, "can_act": manager,
            "carrier_known": False}


@router.get("/families/{lead_id}/history")
def history(lead_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    org_id = _org(db, user)
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.organization_id == org_id).first()
    if lead is None:
        raise HTTPException(status_code=404, detail="Family not found.")
    manager = _manager(db, user)
    msgs = db.query(Message).filter(Message.lead_id == lead.id).order_by(Message.sent_at).all()
    events = (db.query(SmsRecoveryEvent).filter(SmsRecoveryEvent.organization_id == org_id,
                                                SmsRecoveryEvent.lead_id == lead.id)
              .order_by(SmsRecoveryEvent.created_at).all())
    items = (db.query(SmsRecoveryItem).filter(SmsRecoveryItem.organization_id == org_id,
                                              SmsRecoveryItem.lead_id == lead.id)
             .order_by(SmsRecoveryItem.opened_at).all())
    replies = db.query(func.count(Reply.id)).filter(Reply.lead_id == lead.id, Reply.source == "sms").scalar() or 0
    return {
        "lead_id": lead.id, "name_masked": _mask_name(lead.first_name, lead.last_name),
        "phone": _mask_phone(lead.phone), "phone_type": lead.phone_line_type or "not checked",
        "texts_from_family": replies,
        "messages": [{"at": _iso(m.sent_at), "status": m.delivery_status, "status_at": _iso(m.delivery_status_at),
                      "error_code": m.error_code, "error_message": m.error_message if manager else None,
                      "sid": _sid_tail(m.twilio_sid), "source": m.send_source} for m in msgs],
        "holds": [{"id": i.id, "state": i.state, "review_status": i.review_status, "disposition": i.disposition,
                   "error_code": i.error_code, "opened_at": _iso(i.opened_at), "closed_at": _iso(i.closed_at),
                   "policy": i.policy_version, "prevented": i.prevented, "evidence": i.evidence if manager else None}
                  for i in items],
        "events": [{"at": _iso(e.created_at), "kind": e.kind, "error_code": e.error_code,
                    "detail": (json.loads(e.detail) if (e.detail and manager) else None)} for e in events],
        "fallback": R.email_fallback(db, lead),
    }


class ActionIn(BaseModel):
    action: str
    note: Optional[str] = None


@router.post("/items/{item_id}/action")
def act(item_id: str, body: ActionIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    org_id = _org(db, user)
    if not _manager(db, user):
        raise HTTPException(status_code=403, detail="Admin access required.")
    if body.action not in ACTIONS:
        raise HTTPException(status_code=422, detail="Unknown action.")
    item = (db.query(SmsRecoveryItem).filter(SmsRecoveryItem.id == item_id,
                                             SmsRecoveryItem.organization_id == org_id)
            .with_for_update().first())
    if item is None:
        raise HTTPException(status_code=404, detail="Hold not found.")
    before = {"state": item.state, "review_status": item.review_status, "open": bool(item.open_lead_id)}
    note = (body.note or "").strip()
    try:
        result = None
        if body.action == "request_review":
            R.request_review(db, item, user.id, note)
        elif body.action == "mark_capable":
            R.mark_capable(db, item, user.id, note)
        elif body.action == "mark_not_capable":
            R.mark_not_capable(db, item, user.id, note)
        elif body.action == "release":
            result = R.release(db, item, user.id, note)
        elif body.action == "route_email":
            result = R.route_email(db, item, user.id)
    except R.ActionRefused as refused:
        db.commit()                                     # keep the "no eligible channel" event
        raise HTTPException(status_code=409, detail=str(refused))
    from app.routers.audit_log_router import log_action
    log_action(db, org_id, user.id, action="sms_recovery.%s" % body.action, target_type="lead",
               target_id=item.lead_id, before=before,
               after={"state": item.state, "review_status": item.review_status, "open": bool(item.open_lead_id)},
               note=note[:500] or None, commit=False)
    db.commit()
    return {"ok": True, "item_id": item.id, "state": item.state, "review_status": item.review_status,
            "open": bool(item.open_lead_id), "result": result}


@router.get("/evidence.csv")
def evidence(code: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None,
             db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """Carrier-support evidence (full message SIDs, codes, times) for a
    Twilio ticket. Managers only; the recipient stays masked; audited."""
    org_id = _org(db, user)
    if not _manager(db, user):
        raise HTTPException(status_code=403, detail="Admin access required.")
    start, end = _range(date_from, date_to)
    q = (db.query(Message, Lead).join(Lead, Lead.id == Message.lead_id)
         .filter(Lead.organization_id == org_id, Message.sent_at >= start, Message.sent_at < end,
                 func.lower(Message.delivery_status).in_(R.FAILED_STATUSES)))
    if code:
        q = q.filter(Message.error_code == code)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["message_sid", "error_code", "status", "sent_at_utc", "status_at_utc", "recipient",
                "line_type", "send_source", "error_message"])
    n = 0
    for m, l in q.order_by(Message.sent_at).all():
        w.writerow([m.twilio_sid or "", m.error_code or "", m.delivery_status or "", _iso(m.sent_at) or "",
                    _iso(m.delivery_status_at) or "", _mask_phone(l.phone) or "", l.phone_line_type or "",
                    m.send_source or "", (m.error_message or "")[:200]])
        n += 1
    from app.routers.audit_log_router import log_action
    log_action(db, org_id, user.id, action="sms_recovery.export_evidence", target_type="organization",
               target_id=org_id, details={"rows": n, "code": code, "from": _iso(start), "to": _iso(end)})
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="sms-delivery-evidence.csv"'})


@router.get("/policy")
def get_policy(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    org_id = _org(db, user)
    version, rules = P.effective(db, org_id)
    rows = (db.query(SmsRecoveryPolicy).filter(SmsRecoveryPolicy.organization_id == org_id)
            .order_by(SmsRecoveryPolicy.created_at.desc()).limit(20).all())
    return {"version": version, "rules": rules, "default_version": P.DEFAULT_VERSION,
            "history": [{"version": r.version, "active": r.active, "note": r.note, "at": _iso(r.created_at)}
                        for r in rows]}


class PolicyIn(BaseModel):
    rules: dict
    note: str


@router.put("/policy")
def put_policy(body: PolicyIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """A NEW policy version for this workspace (the old one is kept, never
    edited). Takes effect for failures from now on; existing holds keep the
    version that opened them and are never released by a policy change."""
    org_id = _org(db, user)
    if not _manager(db, user):
        raise HTTPException(status_code=403, detail="Admin access required.")
    if not (body.note or "").strip():
        raise HTTPException(status_code=422, detail="Say why the policy is changing (e.g. the Twilio answer).")
    try:
        rules = P.validate(body.rules)
    except ValueError as bad:
        raise HTTPException(status_code=422, detail=str(bad))
    n = db.query(func.count(SmsRecoveryPolicy.id)).filter(SmsRecoveryPolicy.organization_id == org_id).scalar() or 0
    version = "%s-v%d" % (datetime.utcnow().strftime("%Y-%m-%d"), n + 1)
    db.query(SmsRecoveryPolicy).filter(SmsRecoveryPolicy.organization_id == org_id,
                                       SmsRecoveryPolicy.active.is_(True)).update({"active": False})
    db.add(SmsRecoveryPolicy(organization_id=org_id, version=version, rules=json.dumps(rules),
                             note=body.note.strip()[:2000], active=True, created_by_user_id=user.id))
    from app.routers.audit_log_router import log_action
    log_action(db, org_id, user.id, action="sms_recovery.policy", target_type="organization", target_id=org_id,
               details={"version": version, "rules": rules}, note=body.note.strip()[:500], commit=False)
    db.commit()
    return {"version": version, "rules": P.effective(db, org_id)[1]}
