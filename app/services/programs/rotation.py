"""CAMPAIGN ROTATION AND THE 30-DAY REST (Issue #507, Restland).

Owner decisions (Nov 7 2026):

  * After the final unanswered Day-14 touch a family gets a FULL 30-DAY BREAK
    before it may be considered for another round, measured from the ACTUAL
    last outreach (never shortened because a round started late or a retry
    ran late). Where the last planned touch could not be delivered, the round
    still ends, with an explicit basis (`undeliverable`, measured from the last
    attempt) - nobody is stuck forever, nobody is recycled at once.
  * Eligibility after the break = permission to APPEAR in a reviewed pool.
    Never automatic enrollment, never infinite repetition.
  * A genuine reply, a booking, an opt-out or a cancelled appointment ends the
    round for good reasons of its own - those families go to people (or stay
    suppressed), they are never recycled as non-responders. Automatic replies
    ("I'm driving") and delivery receipts are not replies.
  * Rest and active rounds are enforced per DESTINATION (phone / email), not
    one record: a duplicate record or a shared number can never put the same
    phone or inbox into overlapping outreach.

This module only reads outreach history and writes campaign_cycle_members. It
sends nothing and enrolls nobody.
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional, Set

from sqlalchemy.orm import Session

from app.models.reset_models import CampaignCycleMember

log = logging.getLogger(__name__)

REST_DAYS = 30
OPEN_STATUSES = ("staged", "active")


def _norm_email(e: Optional[str]) -> Optional[str]:
    e = (e or "").strip().lower()
    return e or None


def destinations(lead) -> Dict[str, Optional[str]]:
    from app.services.programs.workspace_reset import e164
    return {"phone": e164(getattr(lead, "phone", None)), "email": _norm_email(getattr(lead, "email", None))}


# ── evidence for one family ─────────────────────────────────────────────────

def _genuine_reply(db: Session, org_id: str, lead_id: str, since: Optional[datetime]) -> bool:
    """A person wrote back. Automatic replies are not engagement."""
    from app.models.program_models import ProgramResponse
    q = db.query(ProgramResponse).filter(ProgramResponse.organization_id == org_id, ProgramResponse.lead_id == lead_id)
    if since is not None:
        q = q.filter(ProgramResponse.received_at >= since)
    for r in q.all():
        cls = (r.response_class or "").lower()
        if cls in ("auto_reply", "opt_out"):
            continue                        # automatic replies; opt-outs are handled as opt-outs
        return True
    return False


def reply_counts_for_cadence(db: Session, lead) -> bool:
    """Does this lead's reply history stop its text sequence? For the Restland
    Family File Review campaigns only automatic replies are ignored; for every
    other campaign any reply stops it (unchanged)."""
    from app.models.models import Reply
    from app.models.program_models import ProgramResponse
    from app.services.programs import identity
    from app.services.programs import restland_ffr as F
    rec = identity.source_record_for_lead(db, lead)
    if rec is None or rec.campaign_family not in (F.CAMPAIGN_KEY, F.RETURN_KEY):
        return True
    auto = {rid for (rid,) in db.query(ProgramResponse.reply_id).filter(
        ProgramResponse.lead_id == lead.id, ProgramResponse.response_class == "auto_reply",
        ProgramResponse.reply_id.isnot(None)).all()}
    return any(r.id not in auto for r in db.query(Reply).filter(Reply.lead_id == lead.id).all())


def genuine_repliers(db: Session, organization_id: str, lead_ids) -> Set[str]:
    """Leads with at least one reply that is not automatic / an opt-out."""
    from app.models.program_models import ProgramResponse
    ids = list(lead_ids)
    out: Set[str] = set()
    for i in range(0, len(ids), 500):
        out.update(x for (x,) in db.query(ProgramResponse.lead_id).filter(
            ProgramResponse.organization_id == organization_id, ProgramResponse.lead_id.in_(ids[i:i + 500]),
            ProgramResponse.response_class.notin_(("auto_reply", "opt_out"))).all())
    return out


def _outreach_times(db: Session, lead_id: str, since: Optional[datetime]):
    """(last time anything reached a provider, last DELIVERED-or-unknown time,
    any attempt that failed for good)."""
    from app.models.models import EmailMessage, Message
    last_any, last_ok = None, None
    q = db.query(Message).filter(Message.lead_id == lead_id)
    if since is not None:
        q = q.filter(Message.sent_at >= since)
    for m in q.all():
        if (getattr(m, "direction", None) or "outbound") != "outbound" or m.sent_at is None:
            continue
        last_any = max(last_any or m.sent_at, m.sent_at)
        if (m.delivery_status or "").lower() not in ("undelivered", "failed"):
            last_ok = max(last_ok or m.sent_at, m.sent_at)
    q = db.query(EmailMessage).filter(EmailMessage.lead_id == lead_id)
    if since is not None:
        q = q.filter(EmailMessage.sent_at >= since)
    for e in q.all():
        if e.sent_at is None:
            continue
        last_any = max(last_any or e.sent_at, e.sent_at)
        if (e.status or "").lower() not in ("failed", "bounced"):
            last_ok = max(last_ok or e.sent_at, e.sent_at)
    return last_any, last_ok


def _texts_done(db: Session, lead) -> bool:
    """No more texts will go in this round."""
    from app.models.models import CadenceState
    from app.services.programs.send_approval import textable
    if not textable(lead):
        return True
    st = db.query(CadenceState).filter(CadenceState.lead_id == lead.id).first()
    return st is None or (st.status or "").lower() not in ("active", "paused")


def round_base(member) -> int:
    """Email touch numbers for this round: round k uses 100*(k-1) + n."""
    return 100 * (max(1, int(getattr(member, "round_number", None) or 1)) - 1)


def open_member(db: Session, organization_id: str, lead_id: str) -> Optional[CampaignCycleMember]:
    return (db.query(CampaignCycleMember)
            .filter(CampaignCycleMember.organization_id == organization_id, CampaignCycleMember.lead_id == lead_id,
                    CampaignCycleMember.status.in_(OPEN_STATUSES))
            .order_by(CampaignCycleMember.created_at.desc()).first())


def cycle_key_for(db: Session, lead) -> Optional[str]:
    """The round a campaign touch to this lead belongs to (Restland only)."""
    memo = db.info.setdefault("_orgs_with_rounds", {})
    try:
        # A savepoint: a failed read here must never roll back the caller's
        # work (this runs inside the texting pass).
        with db.begin_nested():
            if lead.organization_id not in memo:     # one cheap check per org per session (SCI has none)
                memo[lead.organization_id] = db.query(CampaignCycleMember.id).filter(
                    CampaignCycleMember.organization_id == lead.organization_id).first() is not None
            if not memo[lead.organization_id]:
                return None
            m = open_member(db, lead.organization_id, lead.id)
    except Exception:                                    # noqa: BLE001 - table absent on an old DB
        return None
    return m.cycle_key if m is not None else None


def email_bases(db: Session, organization_id: str, lead_ids) -> Dict[str, int]:
    """{lead_id: touch-number base} for leads in a round after their first."""
    out: Dict[str, int] = {}
    ids = list(lead_ids)
    for i in range(0, len(ids), 500):
        for m in (db.query(CampaignCycleMember)
                  .filter(CampaignCycleMember.organization_id == organization_id,
                          CampaignCycleMember.lead_id.in_(ids[i:i + 500]),
                          CampaignCycleMember.status.in_(OPEN_STATUSES),
                          CampaignCycleMember.round_number > 1).all()):
            out[m.lead_id] = round_base(m)
    return out


def _emails_done(db: Session, org_id: str, lead, campaign_key: str, base: int = 0) -> bool:
    """No more emails will go in this round: every scheduled email is final,
    or the family has no usable email (missing emails never become texts)."""
    from app.models.program_models import ProgramEmailTouch
    from app.services.programs import campaigns
    if not (lead.email or "").strip() or lead.allow_email is False:
        return True
    steps = campaigns.email_steps(campaign_key)
    rows = {t.touch_number: t for t in db.query(ProgramEmailTouch)
            .filter(ProgramEmailTouch.organization_id == org_id, ProgramEmailTouch.lead_id == lead.id).all()}
    for n, _d in steps:
        t = rows.get(base + n)
        if t is None or t.status in ("claimed", "retry", "blocked"):
            return False
    return True


def assess(db: Session, member: CampaignCycleMember, now: Optional[datetime] = None) -> Dict:
    """What has happened to this family in this round. Pure read."""
    from app.models.models import BookingLink, Lead
    from app.services.programs import identity
    now = now or datetime.utcnow()
    lead = db.query(Lead).filter(Lead.id == member.lead_id).first()
    if lead is None:
        return {"status": "withdrawn", "reason": "record no longer exists"}
    recs = identity.records_for_lead(db, lead)
    started = min([r.journey_started_at for r in recs if getattr(r, "journey_started_at", None)] or [None]) \
        if recs else None
    if started is not None and member.round_number and member.round_number > 1 and member.created_at \
            and started < member.created_at:
        started = None                  # the record still shows the earlier round's start
    if started is not None and getattr(started, "tzinfo", None) is not None:
        started = started.replace(tzinfo=None)
    approved = any(getattr(r, "approved_to_send_at", None) for r in recs)
    if (lead.status or "").lower() == "dnc":
        return {"status": "stopped", "reason": "opted_out", "started_at": started}
    since = started or member.created_at
    bks = (db.query(BookingLink).filter(BookingLink.lead_id == lead.id, BookingLink.booked_time.isnot(None))
           .all())
    if any((b.status or "") in ("booked", "confirmed", "completed") and (b.created_at is None or since is None
                                                                       or b.created_at >= since) for b in bks):
        return {"status": "stopped", "reason": "booked", "started_at": started}
    if any((b.status or "") == "cancelled" and (b.created_at is None or since is None or b.created_at >= since)
           for b in bks):
        return {"status": "stopped", "reason": "cancelled_appointment", "started_at": started}
    if started is None:
        return {"status": "staged" if not approved else "approved", "started_at": None}
    if _genuine_reply(db, lead.organization_id, lead.id, started):
        return {"status": "stopped", "reason": "replied", "started_at": started}
    if not (_texts_done(db, lead) and _emails_done(db, lead.organization_id, lead, member.campaign_key,
                                                    round_base(member))):
        return {"status": "active", "started_at": started}
    last_any, last_ok = _outreach_times(db, lead.id, started)
    if last_ok is not None:
        return {"status": "completed", "reason": "no_reply", "started_at": started, "last_outreach_at": last_ok,
                "next_eligible_at": last_ok + timedelta(days=REST_DAYS)}
    basis = last_any or started
    return {"status": "completed", "reason": "undeliverable", "started_at": started, "last_outreach_at": basis,
            "next_eligible_at": basis + timedelta(days=REST_DAYS)}


def refresh(db: Session, organization_id: str, now: Optional[datetime] = None, write: bool = True) -> Dict:
    """Bring every open round record up to date. Never moves a family
    backwards (a completed round stays completed). Returns counts."""
    now = now or datetime.utcnow()
    counts: Dict[str, int] = {}
    for m in (db.query(CampaignCycleMember).filter(CampaignCycleMember.organization_id == organization_id,
                                                   CampaignCycleMember.status.in_(OPEN_STATUSES)).all()):
        a = assess(db, m, now)
        st = a["status"]
        counts[st] = counts.get(st, 0) + 1
        if not write:
            continue
        if a.get("started_at") and not m.started_at:
            m.started_at = a["started_at"]
        if st == "active":
            m.status = "active"
        elif st in ("completed", "stopped", "withdrawn"):
            m.status = st
            m.completed_at = now
            m.completion_reason = a.get("reason")
            m.last_outreach_at = a.get("last_outreach_at")
            m.next_eligible_at = a.get("next_eligible_at")
    if write:
        db.commit()
    return counts


# ── the destination-level guard ─────────────────────────────────────────────

def conflicts(db: Session, organization_id: str, lead, *, now: Optional[datetime] = None,
              allow_cycle: Optional[str] = None) -> List[str]:
    """Why this family's phone/email may NOT be put into a round now, in plain
    words. `allow_cycle` = the round the family is already staged in (its own
    staged row is not a conflict)."""
    from sqlalchemy import or_
    now = now or datetime.utcnow()
    d = destinations(lead)
    keys = []
    if d["phone"]:
        keys.append(CampaignCycleMember.phone_e164 == d["phone"])
    if d["email"]:
        keys.append(CampaignCycleMember.email_norm == d["email"])
    keys.append(CampaignCycleMember.lead_id == lead.id)
    out = []
    for m in (db.query(CampaignCycleMember).filter(CampaignCycleMember.organization_id == organization_id,
                                                   or_(*keys)).all()):
        if allow_cycle and m.cycle_key == allow_cycle and m.lead_id == lead.id and m.status in OPEN_STATUSES:
            continue
        same = "this family" if m.lead_id == lead.id else "another record with the same phone or email"
        if m.status in OPEN_STATUSES:
            out.append("%s is already in an open round (%s)" % (same, m.cycle_key))
        elif m.status == "completed" and m.next_eligible_at and m.next_eligible_at > now:
            out.append("%s is resting until %s" % (same, m.next_eligible_at.strftime("%b %-d, %Y")))
        elif m.status == "stopped" and m.completion_reason in ("replied", "booked", "cancelled_appointment"):
            out.append("%s %s - a person follows up, not a new round" % (
                same, {"replied": "replied", "booked": "booked an appointment",
                       "cancelled_appointment": "cancelled an appointment"}[m.completion_reason]))
        elif m.status == "stopped" and m.completion_reason == "opted_out":
            out.append("%s opted out" % same)
    return out


def returning(db: Session, organization_id: str, lead_id: str) -> bool:
    """Has this family finished an earlier round of any campaign?"""
    return db.query(CampaignCycleMember.id).filter(
        CampaignCycleMember.organization_id == organization_id, CampaignCycleMember.lead_id == lead_id,
        CampaignCycleMember.status == "completed").first() is not None


def approval_refusal(db: Session, organization_id: str, lead, campaign_key: str) -> Optional[str]:
    """Called by send approval for the rotating campaigns. None = may proceed."""
    from app.services.programs import restland_ffr as F
    if campaign_key not in (F.CAMPAIGN_KEY, F.RETURN_KEY):
        return None
    staged = (db.query(CampaignCycleMember).filter(CampaignCycleMember.organization_id == organization_id,
                                                   CampaignCycleMember.lead_id == lead.id,
                                                   CampaignCycleMember.campaign_key == campaign_key,
                                                   CampaignCycleMember.status.in_(OPEN_STATUSES)).first())
    if staged is None:
        return "Not staged for this campaign round - stage it after review first"
    c = conflicts(db, organization_id, lead, allow_cycle=staged.cycle_key)
    if c:
        return c[0][:1].upper() + c[0][1:]
    if campaign_key == F.CAMPAIGN_KEY and returning(db, organization_id, lead.id):
        return "Returning family - use the Return campaign"
    if campaign_key == F.RETURN_KEY and (staged.round_number or 1) < 2:
        return "Not a returning family - use the first-round campaign"
    return None


def dispatch_refusal(db: Session, lead) -> Optional[str]:
    """Checked again immediately before a rotating-campaign touch goes:
    the phone/email must not have entered another open round or a rest since
    approval. None = may go."""
    m = open_member(db, lead.organization_id, lead.id)
    if m is None:
        return None
    c = conflicts(db, lead.organization_id, lead, allow_cycle=m.cycle_key)
    return c[0] if c else None


def stage_return(db: Session, organization_id: str, leads: Iterable, cycle_key: str, by=None,
                 now: Optional[datetime] = None) -> Dict:
    """REVIEWED re-entry: a person picked these families from "Rest over".
    Each must have finished an earlier round with no reply and a rest that
    has fully ended, with no other conflict on its phone/email. They are
    staged for the Return campaign - NOT approved, NOT started. Their program
    record moves to the Return campaign; the earlier round's start and
    approval are kept in the round's notes."""
    from app.models.program_models import ProgramSourceRecord
    from app.services.programs import restland_ffr as F
    now = now or datetime.utcnow()
    staged, refused = [], []
    for lead in leads:
        prev = (db.query(CampaignCycleMember)
                .filter(CampaignCycleMember.organization_id == organization_id,
                        CampaignCycleMember.lead_id == lead.id)
                .order_by(CampaignCycleMember.created_at.desc()).first())
        why = None
        if prev is None or prev.status != "completed" or prev.completion_reason not in ("no_reply", "undeliverable"):
            why = "no finished, unanswered earlier round"
        elif prev.next_eligible_at is None or prev.next_eligible_at > now:
            why = "still resting"
        else:
            c = conflicts(db, organization_id, lead, now=now)
            why = c[0] if c else None
        if (lead.status or "").lower() == "dnc":
            why = "do not contact"
        if why:
            refused.append({"lead_id": lead.id, "reason": why})
            continue
        rounds = db.query(CampaignCycleMember).filter(CampaignCycleMember.organization_id == organization_id,
                                                      CampaignCycleMember.lead_id == lead.id).count()
        d = destinations(lead)
        recs = db.query(ProgramSourceRecord).filter(ProgramSourceRecord.organization_id == organization_id,
                                                    ProgramSourceRecord.lead_id == lead.id).all()
        notes = {"previous": [{"record": r.id, "campaign": r.campaign_family,
                               "journey_started_at": r.journey_started_at.isoformat() if r.journey_started_at else None,
                               "approved_at": r.approved_to_send_at.isoformat() if r.approved_to_send_at else None}
                              for r in recs], "staged_by": getattr(by, "id", None)}
        m = CampaignCycleMember(organization_id=organization_id, campaign_key=F.RETURN_KEY, cycle_key=cycle_key,
                                lead_id=lead.id, phone_e164=d["phone"], email_norm=d["email"], status="staged",
                                round_number=rounds + 1, notes_json=json.dumps(notes))
        db.add(m)
        for r in recs:
            r.campaign_family = F.RETURN_KEY
            r.journey_started_at, r.journey_started_by = None, None
            r.approved_to_send_at, r.approved_to_send_by = None, None
            r.scheduled_start_at = None
        staged.append(lead.id)
    db.commit()
    return {"staged": staged, "refused": refused, "cycle": cycle_key}


def stage(db: Session, organization_id: str, campaign_key: str, cycle_key: str, leads: Iterable,
          run_id: Optional[str] = None) -> List[CampaignCycleMember]:
    """Record reviewed families as STAGED for a round. Sends nothing."""
    out = []
    for lead in leads:
        if conflicts(db, organization_id, lead, allow_cycle=cycle_key):
            continue
        m = (db.query(CampaignCycleMember).filter_by(organization_id=organization_id, cycle_key=cycle_key,
                                                      lead_id=lead.id).first())
        if m is None:
            d = destinations(lead)
            prior = db.query(CampaignCycleMember).filter(CampaignCycleMember.organization_id == organization_id,
                                                         CampaignCycleMember.lead_id == lead.id).count()
            m = CampaignCycleMember(organization_id=organization_id, campaign_key=campaign_key, cycle_key=cycle_key,
                                    lead_id=lead.id, phone_e164=d["phone"], email_norm=d["email"],
                                    status="staged", run_id=run_id, round_number=prior + 1)
            db.add(m)
            db.flush()
        out.append(m)
    return out


def groups(db: Session, organization_id: str, now: Optional[datetime] = None) -> Dict:
    """Ready-now vs resting-until for the rotation screen (counts + soonest dates)."""
    now = now or datetime.utcnow()
    rows = db.query(CampaignCycleMember).filter(CampaignCycleMember.organization_id == organization_id).all()
    out = {"staged": 0, "active": 0, "resting": 0, "rest_over": 0, "replied": 0, "booked": 0, "opted_out": 0,
           "other_stopped": 0, "next_rest_ends": None, "cycles": sorted({r.cycle_key for r in rows})}
    for r in rows:
        if r.status in OPEN_STATUSES:
            out[r.status] += 1
        elif r.status == "completed":
            if r.next_eligible_at and r.next_eligible_at > now:
                out["resting"] += 1
                if out["next_rest_ends"] is None or r.next_eligible_at < out["next_rest_ends"]:
                    out["next_rest_ends"] = r.next_eligible_at
            else:
                out["rest_over"] += 1
        elif r.completion_reason in ("replied", "booked", "opted_out"):
            out[r.completion_reason] += 1
        else:
            out["other_stopped"] += 1
    if out["next_rest_ends"] is not None:
        out["next_rest_ends"] = out["next_rest_ends"].isoformat() + "Z"
    return out


def member_view(m: Optional[CampaignCycleMember]) -> Optional[Dict]:
    if m is None:
        return None
    iso = lambda d: d.isoformat() + "Z" if d else None   # noqa: E731
    return {"cycle": m.cycle_key, "campaign": m.campaign_key, "round": m.round_number or 1,
            "status": m.status, "started_at": iso(m.started_at),
            "completed_at": iso(m.completed_at), "completion_reason": m.completion_reason,
            "last_outreach_at": iso(m.last_outreach_at), "next_eligible_at": iso(m.next_eligible_at)}
