"""WHAT WOULD HAPPEN IF THE CADENCE WERE SWITCHED ON - counted, not guessed.

READ-ONLY. NOTHING HERE SENDS, ENQUEUES, MODIFIES OR SCHEDULES ANYTHING.

WHY THIS EXISTS
---------------
The engine never sent a message. It raised TypeError on the first touch of
every run, AFTER the counter had been advanced and committed, so leads walked
their whole sequence, arrived at `status = "sent"`, and produced nothing. The
repair is written and it is switched off, because turning it on begins real
SMS to whatever has accumulated in the meantime.

Nobody should make that decision from a feeling. This answers it with numbers:

    how many enrollments are active
    how many advanced WITHOUT any authoritative send behind them
    how many would be due the moment the switch flips
    how many of those compliance would refuse
    how many of those permitted-contact-hours would refuse
    how many would actually send
    ... broken down by organization and by cadence

THE PHANTOM COUNT IS THE INTERESTING ONE
----------------------------------------
`CadenceState.current_touch_number` says how many touches a lead has had.
`cadence_touch_logs` says which ones actually happened, and it did not exist
until this week - so for every enrollment that predates it, the honest reading
of the counter is "unknown, and probably zero". This module reports that gap
rather than papering over it: `advanced_without_evidence` is the number of
touches the product has been claiming and cannot support.

It does NOT repair them. Resetting a counter restarts a sequence for a real
family; that is a decision with a person on the other end of it.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import (BookingLink, CadenceState, CadenceTouchLog,
                               Lead, Organization, Reply)
from app.services import cadence_service, contact_hours

# What a due touch would run into. Same order the engine applies them, so the
# numbers here and the outcomes there cannot drift apart.
WOULD_STOP = "would_stop"                  # reply, DNC, booking, takeover
WOULD_BLOCK_COMPLIANCE = "would_block_compliance"
WOULD_BLOCK_HOURS = "would_block_hours"
WOULD_SKIP = "would_skip"                  # capacity hold, no advisor, no entitlement
WOULD_SEND = "would_send"

VERDICTS = (WOULD_STOP, WOULD_BLOCK_COMPLIANCE, WOULD_BLOCK_HOURS,
            WOULD_SKIP, WOULD_SEND)


def _tally() -> Dict[str, int]:
    return {v: 0 for v in VERDICTS}


def _verdict_for(db: Session, state: CadenceState, lead: Lead,
                 now: datetime, org_cache: Dict[str, Any]) -> Dict[str, Any]:
    """What the engine WOULD do with this one. No side effects.

    Deliberately mirrors `run_due_cadences` in order and in reason text. If the
    two ever disagree, this file is the one that is wrong.
    """
    from app.services import entitlements, send_source as _src
    from app.services.compliance_service import check_compliance_preflight
    from app.services.lead_capacity import is_held
    from app.models.models import Message

    if (lead.status or "") in ("dnc", "hot", "replied", "booked"):
        return {"verdict": WOULD_STOP, "reason": "lead status is %s" % lead.status}

    if db.query(Reply).filter(Reply.lead_id == lead.id).first():
        return {"verdict": WOULD_STOP, "reason": "the family replied"}

    if (db.query(BookingLink)
            .filter(BookingLink.lead_id == lead.id,
                    BookingLink.status.in_(("booked", "confirmed"))).first()):
        return {"verdict": WOULD_STOP, "reason": "an appointment is already booked"}

    if state.cadence_started_at is not None:
        if (db.query(Message)
                .filter(Message.lead_id == lead.id,
                        Message.send_source.in_(list(_src.HUMAN_INITIATED)),
                        Message.sent_at > state.cadence_started_at).first()):
            return {"verdict": WOULD_STOP,
                    "reason": "a person has taken this conversation over"}

    if is_held(lead):
        return {"verdict": WOULD_SKIP, "reason": "lead is held over plan capacity"}

    if lead.assigned_to is None:
        return {"verdict": WOULD_SKIP, "reason": "no advisor assigned"}

    org = org_cache.get(lead.organization_id, "miss")
    if org == "miss":
        org = (db.query(Organization)
               .filter(Organization.id == lead.organization_id).first()
               if lead.organization_id else None)
        org_cache[lead.organization_id] = org
    if not entitlements.org_has_feature(org, "cadences"):
        return {"verdict": WOULD_SKIP,
                "reason": "the organization is not entitled to cadences"}

    try:
        check_compliance_preflight(db, lead, channel="sms")
    except ValueError as blocked:
        return {"verdict": WOULD_BLOCK_COMPLIANCE, "reason": str(blocked)}

    hours = contact_hours.check(lead, now)
    if not hours["permitted"]:
        return {"verdict": WOULD_BLOCK_HOURS, "reason": hours["reason"],
                "hours_code": hours["code"]}

    return {"verdict": WOULD_SEND, "reason": "nothing would stop it"}


def scan(db: Session, organization_id: Optional[str] = None,
         now: Optional[datetime] = None,
         include_leads: bool = False) -> Dict[str, Any]:
    """The whole picture. Read-only.

    `organization_id` narrows it; omitted, it is the deployment.
    """
    now = now or datetime.utcnow()

    states = db.query(CadenceState).filter(CadenceState.status == "active")
    if organization_id:
        states = states.join(Lead).filter(Lead.organization_id == organization_id)
    states = states.all()

    # Evidence, in one query rather than one per enrollment.
    evidence: Dict[str, int] = {}
    ids = [s.id for s in states]
    for i in range(0, len(ids), 500):
        rows = (db.query(CadenceTouchLog.cadence_state_id,
                         CadenceTouchLog.touch_number)
                .filter(CadenceTouchLog.cadence_state_id.in_(ids[i:i + 500]),
                        CadenceTouchLog.outcome == cadence_service.OUTCOME_SENT)
                .all())
        seen: Dict[str, set] = defaultdict(set)
        for sid, touch in rows:
            seen[sid].add(touch)
        for sid, touches in seen.items():
            evidence[sid] = len(touches)

    org_names: Dict[str, str] = {}
    org_cache: Dict[str, Any] = {}
    by_org: Dict[str, Dict[str, Any]] = {}
    by_cadence: Dict[int, Dict[str, Any]] = {}
    hours_reasons: Dict[str, int] = defaultdict(int)

    active = 0
    due = 0
    claimed_touches = 0
    evidenced_touches = 0
    advanced_without_evidence = 0
    verdicts = _tally()
    leads_out: List[Dict[str, Any]] = []

    for state in states:
        lead = state.lead
        if lead is None:
            continue
        active += 1
        org_id = lead.organization_id or "unassigned"
        if org_id not in by_org:
            org = org_cache.get(org_id, "miss")
            if org == "miss":
                org = (db.query(Organization)
                       .filter(Organization.id == org_id).first()
                       if lead.organization_id else None)
                org_cache[org_id] = org
            org_names[org_id] = getattr(org, "name", None) or org_id
            by_org[org_id] = {"organization_id": org_id,
                              "organization": org_names[org_id],
                              "active": 0, "due": 0,
                              "advanced_without_evidence": 0,
                              "verdicts": _tally()}
        by_org[org_id]["active"] += 1

        claimed = int(state.current_touch_number or 0)
        proven = int(evidence.get(state.id, 0))
        claimed_touches += claimed
        evidenced_touches += proven
        gap = max(claimed - proven, 0)
        if gap:
            advanced_without_evidence += gap
            by_org[org_id]["advanced_without_evidence"] += gap

        if state.next_touch_due_at is None or state.next_touch_due_at > now:
            continue

        due += 1
        by_org[org_id]["due"] += 1
        step = claimed + 1
        if step not in by_cadence:
            by_cadence[step] = {"touch_number": step, "due": 0,
                                "verdicts": _tally()}
        by_cadence[step]["due"] += 1

        outcome = _verdict_for(db, state, lead, now, org_cache)
        v = outcome["verdict"]
        verdicts[v] += 1
        by_org[org_id]["verdicts"][v] += 1
        by_cadence[step]["verdicts"][v] += 1
        if v == WOULD_BLOCK_HOURS:
            hours_reasons[outcome.get("hours_code", "unknown")] += 1
        if include_leads:
            leads_out.append({"lead_id": lead.id, "cadence_state_id": state.id,
                              "organization_id": org_id,
                              "touch_number": step,
                              "claimed_touches": claimed,
                              "evidenced_touches": proven,
                              "due_at": state.next_touch_due_at,
                              **outcome})

    out = {
        "generated_at": now,
        "read_only": True,
        "sending_enabled": cadence_service._sending_enabled(),
        "active_enrollments": active,
        "due_now": due,
        "touches": {
            "claimed_by_counters": claimed_touches,
            "with_a_recorded_send": evidenced_touches,
            "advanced_without_evidence": advanced_without_evidence,
        },
        "if_enabled_now": verdicts,
        "quiet_hours_reasons": dict(hours_reasons),
        "by_organization": sorted(by_org.values(),
                                  key=lambda r: (-r["due"], r["organization"])),
        "by_touch_number": [by_cadence[k] for k in sorted(by_cadence)],
        # THE SENTENCE THAT MATTERS, written out so a reader does not have to
        # assemble it from the numbers themselves.
        "headline": ("If cadence SMS were enabled at this moment, %d of %d "
                     "active enrollments would be due, and %d message(s) "
                     "would actually be sent."
                     % (due, active, verdicts[WOULD_SEND])),
    }
    if include_leads:
        out["leads"] = leads_out
    return out


def activation_plan(db: Session, organization_id: Optional[str] = None,
                    now: Optional[datetime] = None) -> Dict[str, Any]:
    """A safe way to turn it on, with this deployment's own numbers in it.

    Describes. Does not execute. `executed` is False and there is no argument
    that makes it True - the same shape `pipeline_consistency.cleanup_plan`
    uses, for the same reason.

    THE BACKLOG IS NOT THE FIRST THING YOU SEND. Every enrollment due at the
    moment of the switch is due because the engine has been failing, not
    because a human decided today was the day to contact those families. Some
    of them were enrolled months ago. Sending that queue as one batch is the
    single worst outcome available here, and it is also the default one if the
    switch is simply flipped.
    """
    report = scan(db, organization_id=organization_id, now=now)
    would_send = report["if_enabled_now"][WOULD_SEND]
    return {
        "executed": False,
        "read_only": True,
        "scan": report,
        "recommended_sequence": [
            {"step": 1,
             "action": "Read this scan for the whole deployment, then per org.",
             "why": "The blast radius is `would_send`, which is %d right now."
                    % would_send},
            {"step": 2,
             "action": "Decide the backlog question FIRST, before any switch.",
             "why": ("Every due enrollment is due because the engine failed, "
                     "not because anyone chose today. The options are: abandon "
                     "the backlog (mark the enrollments completed and start "
                     "clean), re-date it (push next_touch_due_at forward so it "
                     "drains at a chosen rate), or send it. Only the third one "
                     "happens by itself if the switch is simply flipped.")},
            {"step": 3,
             "action": "Enable for ONE organization first, via its `cadences` "
                       "entitlement, with CADENCE_SMS_SENDING on.",
             "why": ("Both gates must agree, so the deployment switch alone "
                     "reaches nobody. One customer is a real test with a "
                     "bounded number of families in it.")},
            {"step": 4,
             "action": "Watch cadence_touch_logs for that org for a full day.",
             "why": ("Every attempt writes a row with the provider's own error "
                     "text. A first run that produces no `sent` rows is the "
                     "same silence as before, and now it is visible.")},
            {"step": 5,
             "action": "Only then widen, one organization at a time.",
             "why": "Nothing about this is urgent enough to do all at once."},
        ],
        "what_will_not_happen_by_itself": [
            "No historical enrollment is re-dated, reset or abandoned by this "
            "code. The counters keep saying what they say until somebody "
            "decides.",
            "No touch is replayed. cadence_touch_logs records from now on; it "
            "does not reconstruct what was never sent.",
            "A lead whose time zone cannot be determined is refused, not "
            "texted on the server's clock. That is `quiet_hours_reasons."
            "zone_unknown` in the scan above.",
        ],
    }
