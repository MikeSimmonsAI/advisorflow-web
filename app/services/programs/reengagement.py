"""RE-ENGAGEMENT ELIGIBILITY (owner, Nov 17 2026) - its own policy, read-only.

A family that finished a journey is NOT a fresh lead. It may be contacted
again only through the separate Re-engagement campaign, and only when ALL of
these hold:
  * it is a real family (not test / simulation) that was approved and reached;
  * it never replied, never booked, is not Do Not Contact / STOP, and no
    person owns its conversation;
  * its text journey is finished (or it never had one) and no email is still
    scheduled or waiting on a retry;
  * its last outreach (text or email) is at least REST_DAYS old;
  * it has not been re-engaged in the last REENGAGE_EVERY_DAYS.
This module only COUNTS - it never enrolls, approves or switches anything on.
The Re-engagement campaign stays off until its wording is reviewed and a
person switches it on.
"""
from datetime import datetime, timedelta
from typing import Dict, Optional

from sqlalchemy.orm import Session

REST_DAYS = 90
REENGAGE_EVERY_DAYS = 365
KEY = "re_engagement"


def _chunks(ids, n=500):
    ids = list(ids)
    for i in range(0, len(ids), n):
        yield ids[i:i + n]


def audit(db: Session, prog, now: Optional[datetime] = None) -> Dict:
    from app.models.models import BookingLink, CadenceState, EmailMessage, Lead, Message
    from app.models.program_models import (CampaignFamily, ProgramConversation, ProgramEmailTouch,
                                           ProgramResponse, ProgramSourceRecord)
    from app.services.programs import campaigns
    now = now or datetime.utcnow()
    org = prog.organization_id
    recs = (db.query(ProgramSourceRecord.lead_id, ProgramSourceRecord.campaign_family,
                     ProgramSourceRecord.approved_to_send_at, ProgramSourceRecord.on_hold)
            .filter(ProgramSourceRecord.organization_id == org, ProgramSourceRecord.lead_id.isnot(None)).all())
    by_lead: Dict[str, Dict] = {}
    for lid, cf, appr, hold in recs:
        f = by_lead.setdefault(lid, {"approved": False, "hold": False, "campaigns": set()})
        f["approved"] = f["approved"] or bool(appr)
        f["hold"] = f["hold"] or bool(hold)
        if cf:
            f["campaigns"].add(cf)
    ids = [lid for lid, f in by_lead.items() if f["approved"]]
    leads, cad, last, replied, booked, human, pending, reengaged = {}, {}, {}, set(), set(), set(), set(), set()
    for ch in _chunks(ids):
        for l in db.query(Lead).filter(Lead.organization_id == org, Lead.id.in_(ch)).all():
            leads[l.id] = l
        for lid, st in db.query(CadenceState.lead_id, CadenceState.status).filter(CadenceState.lead_id.in_(ch)):
            cad[lid] = st
        for lid, at in db.query(Message.lead_id, Message.sent_at).filter(Message.lead_id.in_(ch)):
            if at and (lid not in last or at > last[lid]):
                last[lid] = at
        for lid, at in db.query(EmailMessage.lead_id, EmailMessage.sent_at).filter(
                EmailMessage.lead_id.in_(ch), EmailMessage.status == "sent"):
            if at and (lid not in last or at > last[lid]):
                last[lid] = at
        replied.update(x for (x,) in db.query(ProgramResponse.lead_id).filter(
            ProgramResponse.organization_id == org, ProgramResponse.lead_id.in_(ch)))
        booked.update(x for (x,) in db.query(BookingLink.lead_id).filter(
            BookingLink.lead_id.in_(ch), BookingLink.status.in_(("booked", "confirmed", "completed"))))
        human.update(x for (x,) in db.query(ProgramConversation.lead_id).filter(
            ProgramConversation.organization_id == org, ProgramConversation.lead_id.in_(ch),
            ProgramConversation.state.in_(("human", "callback_requested"))))
        for lid, cf, st in db.query(ProgramEmailTouch.lead_id, ProgramEmailTouch.campaign_family,
                                    ProgramEmailTouch.status).filter(
                ProgramEmailTouch.organization_id == org, ProgramEmailTouch.lead_id.in_(ch)):
            if st in ("retry", "blocked", "claimed"):
                pending.add(lid)
            if cf == KEY and st == "sent":
                reengaged.add(lid)
    out = {"families_finished": 0, "eligible_now": 0, "resting": 0, "first_eligible_on": None,
           "last_contact_newest": None, "excluded": {}, "by_campaign": {}}

    def ex(why):
        out["excluded"][why] = out["excluded"].get(why, 0) + 1
    first = None
    newest = None
    for lid in ids:
        l = leads.get(lid)
        if l is None or getattr(l, "is_test", False):
            continue
        if lid not in last:
            ex("Never reached")
            continue
        if (l.status or "").lower() == "dnc" or (cad.get(lid) or "").startswith("stopped_dnc"):
            ex("Do not contact / STOP")
            continue
        if lid in booked:
            ex("Booked")
            continue
        if lid in replied:
            ex("Replied")
            continue
        if lid in human:
            ex("A person has the conversation")
            continue
        if by_lead[lid]["hold"]:
            ex("On hold")
            continue
        if cad.get(lid) == "active" or lid in pending:
            ex("Journey still running")
            continue
        if lid in reengaged:
            ex("Already re-engaged this year")
            continue
        out["families_finished"] += 1
        ready_on = last[lid] + timedelta(days=REST_DAYS)
        newest = last[lid] if newest is None or last[lid] > newest else newest
        cf = sorted(by_lead[lid]["campaigns"])[0] if by_lead[lid]["campaigns"] else "(none)"
        b = out["by_campaign"].setdefault(cf, {"finished": 0, "eligible_now": 0})
        b["finished"] += 1
        if ready_on <= now:
            out["eligible_now"] += 1
            b["eligible_now"] += 1
        else:
            out["resting"] += 1
            first = ready_on if first is None or ready_on < first else first
    out["first_eligible_on"] = first.strftime("%Y-%m-%d") if first else None
    out["last_contact_newest"] = newest.strftime("%Y-%m-%d") if newest else None
    fam = (db.query(CampaignFamily).filter(CampaignFamily.organization_id == org, CampaignFamily.key == KEY).first())
    blocker = campaigns.activation_blocker(fam, db) if fam is not None else "No Re-engagement campaign"
    default_copy = False
    try:
        from app.services.programs.email_touches import _family_copy_is_default
        default_copy = bool(fam is not None and _family_copy_is_default(fam))
    except Exception:                                    # noqa: BLE001
        pass
    out["campaign"] = {"exists": fam is not None, "on": bool(fam and fam.is_active), "blocker": blocker,
                       "copy_is_setup_default": default_copy,
                       "ready": fam is not None and blocker is None and not default_copy}
    out["policy"] = {"rest_days": REST_DAYS, "at_most_every_days": REENGAGE_EVERY_DAYS,
                     "never": ["replied", "booked", "Do not contact / STOP", "a person owns the conversation",
                               "on hold", "journey still running"]}
    return out
