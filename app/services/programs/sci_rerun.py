"""SCI: START THE CADENCE OVER FOR FAMILIES WHO DIDN'T REPLY (owner, Nov 17 2026).

Owner's choices (Nov 17, 9:50 PM CT):
  * who: families the first journey NEVER REACHED go first (no text ever
    delivered - carrier failures - or texts held on SMS Recovery), then
    everyone else who got the messages and did not reply;
  * pace: through the standing daily batch, 40 families a day in total
    (brand-new families first, then this queue), 10 minutes apart, 9 AM -
    6 PM Central, every day, starting FRIDAY NOV 20, 9:00 AM Central;
  * wording: the Re-engagement campaign's (switched on for this, audited).

Never: Do Not Contact / STOP, anyone who replied (any reply), booked, a
conversation a person has, records on hold, test records, a family with no
usable phone or email. Every family is checked again at the moment it starts
(something may have changed since it was queued).

Starting a family (nothing is sent here - the normal senders do that, inside
their hours, with every send-time check):
  * its source record(s) move to the Re-engagement campaign with a new start
    time (the drip slot);
  * texts: the text journey restarts at text 1 at that slot (cell phones
    only; an SMS Recovery hold still holds its texts until reviewed);
  * emails: its earlier campaign emails are kept as history (touch 900 + n),
    so email 1 goes again in the new wording;
  * every changed value is kept in the member row (before_json).
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.sci_rerun_models import NEVER_REACHED, NO_REPLY, QUEUED, SKIPPED, STARTED, ProgramRerunMember

log = logging.getLogger(__name__)

RERUN_KEY = "sci-rerun-2026-11-20"
CAMPAIGN = "re_engagement"
ARCHIVE = 900
# Friday Nov 20 2026, 9:00 AM Central (CST = UTC-6)
NOT_BEFORE = datetime(2026, 11, 20, 15, 0)


def _chunks(ids, n=500):
    ids = list(ids)
    for i in range(0, len(ids), n):
        yield ids[i:i + n]


def _facts(db: Session, org_id: str, ids: List[str]) -> Dict:
    from app.models.models import BookingLink, CadenceState, EmailMessage, Lead, Message
    from app.models.program_models import ProgramConversation, ProgramResponse, ProgramSourceRecord
    from app.models.sms_recovery_models import SmsRecoveryItem
    f = {"leads": {}, "states": {}, "recs": {}, "texts": {}, "delivered": set(), "emailed": set(),
         "replied": set(), "booked": set(), "human": set(), "held": set()}
    for ch in _chunks(ids):
        f["leads"].update({l.id: l for l in db.query(Lead).filter(Lead.organization_id == org_id,
                                                                  Lead.id.in_(ch)).all()})
        f["states"].update({s.lead_id: s for s in db.query(CadenceState).filter(CadenceState.lead_id.in_(ch)).all()})
        for r in db.query(ProgramSourceRecord).filter(ProgramSourceRecord.organization_id == org_id,
                                                      ProgramSourceRecord.lead_id.in_(ch)).all():
            f["recs"].setdefault(r.lead_id, []).append(r)
        for lid, ds in db.query(Message.lead_id, Message.delivery_status).filter(Message.lead_id.in_(ch)):
            f["texts"][lid] = f["texts"].get(lid, 0) + 1
            if (ds or "").lower() == "delivered":
                f["delivered"].add(lid)
        f["emailed"].update(x for (x,) in db.query(EmailMessage.lead_id).filter(
            EmailMessage.lead_id.in_(ch), EmailMessage.status == "sent"))
        f["replied"].update(x for (x,) in db.query(ProgramResponse.lead_id).filter(
            ProgramResponse.organization_id == org_id, ProgramResponse.lead_id.in_(ch)))
        f["booked"].update(x for (x,) in db.query(BookingLink.lead_id).filter(
            BookingLink.lead_id.in_(ch), BookingLink.status.in_(("booked", "confirmed", "completed"))))
        f["human"].update(x for (x,) in db.query(ProgramConversation.lead_id).filter(
            ProgramConversation.organization_id == org_id, ProgramConversation.lead_id.in_(ch),
            ProgramConversation.state.in_(("human", "callback_requested"))))
        f["held"].update(x for (x,) in db.query(SmsRecoveryItem.lead_id).filter(
            SmsRecoveryItem.organization_id == org_id, SmsRecoveryItem.lead_id.in_(ch),
            SmsRecoveryItem.closed_at.is_(None)))
    return f


def _mode(prog, lead) -> Optional[str]:
    from app.services.programs.daily_batch import EMAIL_ONLY, TEXT_AND_EMAIL, _email_route_ok
    if (lead.phone or "").strip() and getattr(lead, "phone_line_type", None) == "mobile" \
            and getattr(lead, "contact_channel", None) != "email_only":
        return TEXT_AND_EMAIL
    return EMAIL_ONLY if _email_route_ok(prog, lead) is None else None


def why_not(prog, f: Dict, lid: str) -> Optional[str]:
    """None when this family may start over, else the reason it may not."""
    lead = f["leads"].get(lid)
    st = f["states"].get(lid)
    recs = f["recs"].get(lid, [])
    if lead is None or getattr(lead, "is_test", False):
        return "Test record"
    if not any(r.approved_to_send_at for r in recs):
        return "Never in a journey"
    if (lead.status or "").lower() == "dnc" or getattr(lead, "manual_flag", None) == "remove_all" or \
            (st is not None and (st.status or "").startswith("stopped_dnc")):
        return "Do not contact / STOP"
    if lid in f["booked"] or (lead.status or "") == "booked" or (st is not None and st.status == "stopped_booked"):
        return "Booked"
    if lid in f["replied"] or (lead.status or "") in ("replied", "hot") or \
            (st is not None and (st.status or "").startswith("stopped")) or (st is not None and st.status == "paused"):
        return "Replied"
    if lid in f["human"]:
        return "A person has the conversation"
    if any(r.on_hold for r in recs):
        return "On hold"
    if not (f["texts"].get(lid) or lid in f["emailed"]):
        return "Never contacted (no usable channel)"
    if any(r.campaign_family == CAMPAIGN for r in recs):
        return "Already in Re-engagement"
    if _mode(prog, lead) is None:
        return "No usable phone or email"
    return None


def group_of(f: Dict, lid: str) -> str:
    if lid in f["held"] or (lid not in f["delivered"] and lid not in f["emailed"]) or \
            (f["texts"].get(lid) and lid not in f["delivered"]):
        return NEVER_REACHED
    return NO_REPLY


def ensure_staged(db: Session, prog, *, now: Optional[datetime] = None) -> Dict:
    """Queue every family that may start over, once (idempotent per key), and
    switch the Re-engagement campaign on (audited). Sends nothing."""
    from app.models.program_models import CampaignFamily, ProgramSourceRecord
    from app.services import sms_programs
    from app.services.programs import campaigns
    org = prog.organization_id
    if not sms_programs.is_sci_org(db, org):
        return {"skipped": "not SCI"}
    if db.query(ProgramRerunMember.id).filter(ProgramRerunMember.rerun_key == RERUN_KEY,
                                              ProgramRerunMember.organization_id == org).first() is not None:
        return {"already": True}
    fam = (db.query(CampaignFamily).filter(CampaignFamily.organization_id == org,
                                           CampaignFamily.key == CAMPAIGN).first())
    if fam is None or campaigns.activation_blocker(fam, db) is not None:
        log.warning("sci rerun not staged: Re-engagement campaign missing or not ready")
        return {"refused": "Re-engagement campaign missing or its wording is not complete"}
    ids = [x for (x,) in db.query(ProgramSourceRecord.lead_id).filter(
        ProgramSourceRecord.organization_id == org, ProgramSourceRecord.lead_id.isnot(None),
        ProgramSourceRecord.approved_to_send_at.isnot(None)).distinct()]
    f = _facts(db, org, ids)
    queued, skipped = [], {}
    for lid in ids:
        why = why_not(prog, f, lid)
        if why:
            skipped[why] = skipped.get(why, 0) + 1
            continue
        g = group_of(f, lid)
        first = min((r.approved_to_send_at for r in f["recs"][lid] if r.approved_to_send_at), default=None)
        queued.append((0 if g == NEVER_REACHED else 1, first or datetime.min, lid, g))
    queued.sort()
    for i, (pri, _first, lid, g) in enumerate(queued):
        db.add(ProgramRerunMember(organization_id=org, rerun_key=RERUN_KEY, lead_id=lid, group=g,
                                  priority=pri * 100000 + i, status=QUEUED, not_before=NOT_BEFORE))
    was_on = bool(fam.is_active)
    fam.is_active = True
    db.commit()
    summary = {"rerun_key": RERUN_KEY, "queued": len(queued),
               "never_reached": sum(1 for q in queued if q[3] == NEVER_REACHED),
               "no_reply": sum(1 for q in queued if q[3] == NO_REPLY), "left_out": skipped,
               "starts": "Fri Nov 20 2026, 9:00 AM Central, 40 a day through the daily batch",
               "campaign": CAMPAIGN, "campaign_was_on": was_on}
    try:
        from app.routers.audit_log_router import log_action
        actor = getattr(prog, "daily_batch_by", None) or getattr(prog, "created_by", None)
        if actor:
            log_action(db, org, actor, action="program.rerun_staged", target_type="outreach_program",
                       target_id=prog.id, details=dict(summary, by="owner directive Nov 17, 9:50 PM CT"))
            db.commit()
    except Exception:                                    # noqa: BLE001
        db.rollback()
    return summary


def due(db: Session, prog, now: datetime) -> int:
    return (db.query(ProgramRerunMember).filter(ProgramRerunMember.organization_id == prog.organization_id,
                                                ProgramRerunMember.status == QUEUED,
                                                ProgramRerunMember.not_before <= now).count())


def start_next(db: Session, prog, user, n: int, first_at: datetime, spacing_minutes: int,
               now: Optional[datetime] = None) -> List[Dict]:
    """Start up to n queued families (best first) on the drip from first_at.
    Each is checked again now; one that may no longer start is skipped with
    its reason (it never takes a slot)."""
    from app.models.models import CadenceState
    from app.models.program_models import ProgramEmailTouch
    from app.services.programs.daily_batch import TEXT_AND_EMAIL
    now = now or datetime.utcnow()
    if n <= 0:
        return []
    out: List[Dict] = []
    q = (db.query(ProgramRerunMember).filter(ProgramRerunMember.organization_id == prog.organization_id,
                                             ProgramRerunMember.status == QUEUED,
                                             ProgramRerunMember.not_before <= now)
         .order_by(ProgramRerunMember.priority))
    for m in q.limit(n * 3 + 20).all():
        if len(out) >= n:
            break
        f = _facts(db, prog.organization_id, [m.lead_id])
        why = why_not(prog, f, m.lead_id)
        if why:
            m.status, m.reason = SKIPPED, why
            db.commit()
            continue
        lead = f["leads"][m.lead_id]
        mode = _mode(prog, lead)
        at = first_at + timedelta(minutes=spacing_minutes * len(out))
        st = f["states"].get(m.lead_id)
        recs = f["recs"][m.lead_id]
        before = {"records": [{"id": r.id, "campaign_family": r.campaign_family,
                               "scheduled_start_at": r.scheduled_start_at, "send_mode": r.send_mode}
                              for r in recs],
                  "cadence": ({"id": st.id, "status": st.status, "current_touch_number": st.current_touch_number,
                               "cadence_started_at": st.cadence_started_at, "next_touch_due_at": st.next_touch_due_at,
                               "completed_at": st.completed_at} if st is not None else None),
                  "emails": []}
        taken = {t.touch_number for t in db.query(ProgramEmailTouch).filter(
            ProgramEmailTouch.lead_id == m.lead_id).all()}
        for t in db.query(ProgramEmailTouch).filter(ProgramEmailTouch.lead_id == m.lead_id,
                                                    ProgramEmailTouch.touch_number.between(1, 99)).all():
            new_n = ARCHIVE + t.touch_number
            while new_n in taken:
                new_n += 10
            taken.add(new_n)
            before["emails"].append({"id": t.id, "touch_number": t.touch_number, "now": new_n})
            t.touch_number = new_n
        db.flush()
        for r in recs:
            r.campaign_family, r.scheduled_start_at, r.send_mode = CAMPAIGN, at, mode
        if mode == TEXT_AND_EMAIL:
            if st is None:
                st = CadenceState(lead_id=lead.id, status="active", current_touch_number=0)
                db.add(st)
            st.status, st.current_touch_number, st.completed_at = "active", 0, None
            st.cadence_started_at, st.next_touch_due_at = at, at
        m.status, m.mode, m.started_at = STARTED, mode, now
        m.before_json = json.dumps(before, default=str)
        db.commit()
        out.append({"lead_id": m.lead_id, "mode": mode, "group": m.group, "at": at.isoformat() + "Z"})
    return out


def counts(db: Session, prog) -> Dict:
    from sqlalchemy import func
    rows = (db.query(ProgramRerunMember.group, ProgramRerunMember.status, func.count(ProgramRerunMember.id))
            .filter(ProgramRerunMember.organization_id == prog.organization_id,
                    ProgramRerunMember.rerun_key == RERUN_KEY)
            .group_by(ProgramRerunMember.group, ProgramRerunMember.status).all())
    out = {"key": RERUN_KEY, "starts_on": "2026-11-20", "queued": 0, "started": 0, "skipped": 0,
           "never_reached_queued": 0, "no_reply_queued": 0}
    for g, s, c in rows:
        out[s] = out.get(s, 0) + c
        if s == QUEUED:
            out["%s_queued" % g] = out.get("%s_queued" % g, 0) + c
    return out
