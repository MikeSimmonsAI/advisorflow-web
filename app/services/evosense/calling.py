"""Human first contact by phone - the VA / acquisition rep's call queue and
one-click call outcomes, on the existing records.

    call_queue      owners with a usable phone, best first (the opportunity
                    view's order), each with why it matters, the sale date,
                    every restriction and the approved opening
    record_outcome  one click: what happened on the call. It feeds the SAME
                    machinery a reply does - suppression and opt-out, wrong
                    party, follow-ups, hand-offs, seller intent - and the rep's
                    note is stored as what a PERSON recorded (human_entered),
                    never as the seller's own words
    sweep           put each callable owner in the existing VA queue
                    (WholesaleWorkException kind seller_first_call), once

CALLING HOURS (Texas Business & Commerce Code 301.051, the called person's
local time): after 9 a.m. and before 9 p.m. Monday-Saturday; after noon and
before 9 p.m. Sunday. Do-not-call, suppression and opt-outs always apply.
Nothing here dials a phone or sends a message.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import or_

from app.services.evosense import common as C

TZ = "America/Chicago"
OUTCOMES = {
    "no_answer": "No answer",
    "voicemail": "Left a voicemail",
    "wrong_number": "Wrong number",
    "do_not_call": "Asked not to be called",
    "not_interested": "Not interested",
    "callback": "Call back later",
    "interested": "Interested - talk more",
    "appointment": "Wants an appointment",
    "deceased": "Owner deceased",
    "not_owner": "Not the owner",
}
VA_KIND = "seller_first_call"


def window(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Is it inside Texas calling hours right now (Central time)?"""
    from zoneinfo import ZoneInfo
    now = now or datetime.utcnow()
    local = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(TZ))
    start = 12 if local.weekday() == 6 else 9
    ok = start <= local.hour < 21
    return {"ok": ok, "local_time": local.strftime("%a %I:%M %p CT"),
            "rule": "Texas: 9 AM-9 PM Mon-Sat, noon-9 PM Sunday (the called person's local time)",
            "opens": None if ok else ("today at %s" % ("noon" if start == 12 else "9 AM") if local.hour < start
                                      else "tomorrow at %s" % ("noon" if (local.weekday() + 1) % 7 == 6 else "9 AM"))}


def _restrictions(db, org_id: str, cp) -> List[str]:
    from app.services.evosense import eligibility as EL
    out = []
    if cp.status != "active":
        out.append("Number is %s" % cp.status.replace("_", " "))
    try:
        from app.services import compliance_service as CS
        if CS.is_phone_suppressed(db, org_id, cp.value):
            out.append("On the do-not-contact / suppression list")
    except Exception:  # noqa: BLE001
        pass
    try:
        if EL.platform_dnc(db, org_id, cp.value):
            out.append("A lead with this number is marked do-not-call")
    except Exception:  # noqa: BLE001
        pass
    if cp.validation == "invalid":
        out.append("Failed validation")
    return out


def call_queue(db, org_id: str, *, limit: int = 100, include_test: bool = False) -> Dict[str, Any]:
    from app.models.evosense_models import EvoSenseContactPoint, EvoSenseFollowUp, EvoSenseProperty
    from app.services.evosense import cohort as CO
    from app.services.evosense import first_contact as FC
    from app.services.evosense import opportunity as OP
    q = (db.query(EvoSenseProperty)
         .filter(EvoSenseProperty.organization_id == org_id, EvoSenseProperty.archived_at.is_(None),
                 EvoSenseProperty.promoted_deal_id.is_(None),
                 or_(EvoSenseProperty.workflow_state.is_(None),
                     ~EvoSenseProperty.workflow_state.in_(("DO_NOT_CONTACT", "NOT_INTERESTED")))))
    if not include_test:
        q = q.filter(EvoSenseProperty.is_test.is_(False))
    props = q.order_by(EvoSenseProperty.priority_score.desc().nullslast()).limit(2000).all()
    pre = CO._prefetch(db, org_id, props)
    phones: Dict[str, List[Any]] = {}
    for oid, cps in pre["contacts"].items():
        phones[oid] = [c for c in cps if c.kind == "phone"]
    win = window()
    rows = []
    for p in props:
        owners = CO._owners_of(pre, p)
        if not owners:
            continue
        cps = phones.get(owners[0].id) or []
        if not cps:
            continue
        o = OP.compact(p) or {}
        nums = []
        for cp in sorted(cps, key=lambda c: (c.verified_at is None, -(c.provider_confidence or 0))):
            nums.append({"contact_point_id": cp.id, "number": cp.value, "line_type": cp.line_type,
                         "validation": cp.validation, "verified": cp.verified_at is not None,
                         "provider_confidence": cp.provider_confidence,
                         "source": cp.source, "restrictions": _restrictions(db, org_id, cp)})
        callable_nums = [n for n in nums if not n["restrictions"]]
        if not callable_nums:
            continue
        rows.append({"property_id": p.id, "address": p.street_address, "city": p.city, "county": p.county,
                     "owner": owners[0].display_name, "owner_type": owners[0].owner_type,
                     "owner_status": CO._owner_status_pre(pre, p).get("status"),
                     "numbers": nums, "priority": p.priority_score, "urgency": o.get("urgency_label"),
                     "sale_deadline": p.sale_deadline.isoformat() if p.sale_deadline else None,
                     "why": [f["label"] for f in (o.get("top_factors") or [])][:3],
                     "next_action": o.get("next_action"), "workflow": o.get("workflow_state"),
                     "is_test": bool(p.is_test)})
        if len(rows) >= limit:
            break
    ids = [r["property_id"] for r in rows] or ["-"]
    due = {}
    for pid, d in (db.query(EvoSenseFollowUp.property_id, EvoSenseFollowUp.due_at)
                   .filter(EvoSenseFollowUp.organization_id == org_id, EvoSenseFollowUp.property_id.in_(ids),
                           EvoSenseFollowUp.status == "due").all()):
        if pid not in due or d < due[pid]:
            due[pid] = d
    for r in rows:
        r["follow_up_due"] = due[r["property_id"]].isoformat() + "Z" if r["property_id"] in due else None
    return {"rows": rows, "window": win, "script": FC.CALL_TRACK, "rules": FC.CALL_RULES,
            "outcomes": OUTCOMES,
            "notice": "Nothing here dials or sends. Calling hours, do-not-call and opt-outs apply to every call."}


def record_outcome(db, org_id: str, prop, user, *, outcome: str, note: Optional[str] = None,
                   callback_at: Optional[str] = None, contact_point_id: Optional[str] = None) -> Dict[str, Any]:
    """One click after a call. Updates the existing engagement, contact, facts,
    follow-ups, hand-offs and scores - nothing parallel."""
    from app.models.evosense_models import EvoSenseContactPoint, EvoSenseFact, EvoSenseMessage
    from app.services.evosense import contacts as CT
    from app.services.evosense import conversation as CV
    from app.services.evosense import evaluate as EV
    from app.services.evosense import followups as FU
    from app.services.evosense import handoff as HO
    from app.services.evosense import opportunity as OP
    from app.services.evosense import outreach as OU
    from app.services.evosense import scoring as SC
    if outcome not in OUTCOMES:
        raise ValueError("Unknown call outcome.")
    owner = CT.primary_owner(db, prop)
    q = db.query(EvoSenseContactPoint).filter(EvoSenseContactPoint.organization_id == org_id,
                                              EvoSenseContactPoint.kind == "phone")
    cp = (q.filter(EvoSenseContactPoint.id == contact_point_id).first() if contact_point_id else
          q.filter(EvoSenseContactPoint.owner_id == getattr(owner, "id", "-"),
                   EvoSenseContactPoint.status == "active")
          .order_by(EvoSenseContactPoint.verified_at.desc().nullslast(),
                    EvoSenseContactPoint.provider_confidence.desc().nullslast()).first())
    if cp is None or (owner is not None and cp.owner_id != owner.id):
        raise ValueError("No phone on file for this property's owner.")
    strategy = EV.strategy_for(db, prop)
    eng = OU._engagement(db, prop, strategy, cp)
    eng.channel = "call"
    eng.touches = (eng.touches or 0) + 1
    eng.last_touch_at = C.now()
    who = getattr(user, "email", None) or "a person"
    text = "Call - %s%s" % (OUTCOMES[outcome], (": " + note.strip()) if note and note.strip() else "")
    db.add(EvoSenseMessage(organization_id=org_id, engagement_id=eng.id, property_id=prop.id, direction="outbound",
                           channel="call", body=text[:2000], delivery="call_logged"))

    def fact(ftype, value):
        db.add(EvoSenseFact(organization_id=org_id, property_id=prop.id, engagement_id=eng.id, fact_type=ftype,
                            value=value, truth_state=C.T_HUMAN,
                            quote=("Recorded by %s after a call: %s" % (who, note or OUTCOMES[outcome]))[:250],
                            extracted_by="operator", confidence=70))
    actions: List[str] = []
    mapped = None
    if outcome in ("no_answer", "voicemail"):
        eng.status = "active"
        when = C.now() + timedelta(days=1)
        FU.create(db, org_id, prop, user, due_at=(callback_at or when.isoformat()),
                  note="Try again (%s)" % OUTCOMES[outcome].lower())
        actions.append("Follow-up scheduled")
    elif outcome == "wrong_number":
        cp.status, cp.status_reason = "wrong_party", "Call: wrong number (%s)" % C.now().strftime("%b %d")
        eng.status, eng.blocked_reason, mapped = "stopped", "wrong_party", C.O_WRONG_PERSON
        actions.append("Number marked wrong party - never used for this owner again")
    elif outcome == "do_not_call":
        CV._opt_out(db, org_id, cp, None, "Asked not to be called (call outcome)")
        eng.status, eng.blocked_reason, mapped = "stopped", "opted_out", C.O_DNC
        actions.append("Number suppressed and opted out")
    elif outcome == "not_interested":
        eng.status, mapped = "stopped", C.O_HARD_NO
        actions.append("Stopped: not interested")
    elif outcome == "not_owner":
        cp.status, cp.status_reason = "wrong_party", "Call: not the owner"
        eng.status, mapped = "stopped", C.O_NOT_OWNER
        actions.append("Number marked wrong party (not the owner)")
    elif outcome == "callback":
        if not callback_at:
            raise ValueError("Choose when to call back.")
        fu = FU.create(db, org_id, prop, user, due_at=callback_at, note=note or "Owner asked for a call back")
        eng.status, eng.nurture_until, mapped = "nurture", fu.due_at, C.O_CALL_LATER
        eng.nurture_reason = "Call back %s" % fu.due_at.strftime("%b %d %I:%M %p")
        actions.append("Call back scheduled for %s" % fu.due_at.strftime("%b %d"))
    elif outcome in ("interested", "appointment"):
        eng.status, mapped = "responded", (C.O_APPOINTMENT if outcome == "appointment" else C.O_INTERESTED)
        fact("appointment_request" if outcome == "appointment" else "willing_to_sell", "yes")
        cp.positive_response = True
        HO.open_handoff(db, prop, ["APPOINTMENT" if outcome == "appointment" else "CALL_INTERESTED"],
                        engagement=eng, priority=90 if outcome == "appointment" else 80,
                        next_action=("Book the appointment - promote into Wholesale, then Appointments"
                                     if outcome == "appointment" else
                                     "Qualify (timeline, condition, occupancy), then promote into Wholesale"))
        actions.append("Hand-off opened: book the appointment" if outcome == "appointment" else
                       "Hand-off opened: qualify (timeline, condition, occupancy) and set an appointment")
    elif outcome == "deceased":
        fact("estate_context", "owner deceased (reported on a call)")
        HO.open_handoff(db, prop, ["ESTATE"], engagement=eng, priority=80,
                        next_action="Confirm the estate and who can sell")
        mapped = C.O_ESTATE
        actions.append("Hand-off: confirm the estate and who can sell")
    if mapped:
        eng.last_outcome = mapped
    cp.last_response_at = C.now() if outcome not in ("no_answer", "voicemail") else cp.last_response_at
    db.flush()
    if mapped:
        facts = db.query(EvoSenseFact).filter(EvoSenseFact.organization_id == org_id,
                                              EvoSenseFact.property_id == prop.id).all()
        si = SC.seller_intent(facts, mapped, 1)
        SC.record(db, prop, "seller_intent", si, subject_type="engagement", subject_id=eng.id,
                  strategy_id=getattr(strategy, "id", None))
        prop.seller_intent = si["value"]
    CT.score_contact_point(db, prop, cp)
    _resolve_va(db, org_id, prop, user, outcome)
    C.log_event(db, org_id, "call.logged", property_id=prop.id, user=user, actor_type=C.ACTOR_USER,
                is_test=prop.is_test, summary="%s by %s%s" % (text[:160], who,
                                                              ("; " + "; ".join(actions)) if actions else ""),
                details={"outcome": outcome, "contact_point": cp.id})
    EV.rescore(db, prop, strategy)
    OP.refresh(db, org_id, [prop])
    return {"outcome": outcome, "label": OUTCOMES[outcome], "actions": actions, "status": prop.status,
            "next_action": (OP.compact(prop) or {}).get("next_action")}


def _resolve_va(db, org_id: str, prop, user, outcome: str) -> None:
    from app.models.wholesale_models import WholesaleWorkException
    for ex in (db.query(WholesaleWorkException)
               .filter(WholesaleWorkException.organization_id == org_id, WholesaleWorkException.kind == VA_KIND,
                       WholesaleWorkException.subject_type == "evosense_property",
                       WholesaleWorkException.subject_id == prop.id,
                       WholesaleWorkException.status.in_(("open", "assigned", "needs_more_info"))).all()):
        if outcome in ("no_answer", "voicemail"):
            ex.outcome_note = "Call attempted: %s" % OUTCOMES[outcome]
            continue
        ex.status, ex.outcome = "complete", "complete"
        ex.outcome_note = "Call outcome: %s" % OUTCOMES[outcome]
        ex.resolved_at, ex.resolved_by_id = datetime.utcnow(), getattr(user, "id", None)


def sweep(db, org_id: str, *, limit: int = 100) -> int:
    """Every callable owner is one open item in the existing VA queue."""
    from app.services import wholesale_exceptions as WX
    n = 0
    now = datetime.utcnow()
    for r in call_queue(db, org_id, limit=limit)["rows"]:
        if r["workflow"] and r["workflow"].get("key") in ("RESPONDED", "HUMAN_HANDOFF", "APPOINTMENT"):
            continue
        due = r.get("follow_up_due")
        if due and datetime.fromisoformat(due.rstrip("Z")) > now:
            continue                 # a promised call-back / retry is not due yet - no task before it is
        promised = bool(due)
        detail = "; ".join(filter(None, [
            ("Sale %s" % r["sale_deadline"]) if r["sale_deadline"] else None,
            ", ".join(r["why"]) or None,
            "Open the property for the number, the script and the outcome buttons."]))
        WX.raise_exception(db, org_id, kind=VA_KIND, subject_type="evosense_property",
                           subject_id=r["property_id"],
                           title=("Call back the owner (follow-up due): %s" if promised else
                                  "Call the owner: %s") % r["address"],
                           detail=detail[:900], priority=10 if r["sale_deadline"] else 40,
                           source="evosense.calling", is_test=r["is_test"])
        n += 1
    return n
