"""EvoSys Sales automatic follow-up (owner request 2026-11-16).

Every lead on the EvoSys Pro sales team that has an email address gets a short
series of emails. The series stops by itself when the lead replies (logged on
the lead), books a call, opts out, or moves past "Contacted" in the pipeline.

Nothing sends unless BOTH are true for the team:
    * Mike approved the steps (approved_at), and
    * the team's switch is on (sending_on).
Editing the steps clears the approval. Sends happen only on weekdays between
8:00 AM and 6:00 PM Central, at most `daily_limit` a day. Text steps are held
(never sent) until EvoSys Sales has a texting number of its own; the screen
says so.
"""
from __future__ import annotations

import html as _html
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.evosys_sales_models import (
    EvoSysOutreachMessage, EvoSysOutreachSettings, EvoSysOutreachState,
)
from app.models.sales_models import (
    BrandSalesOrg, Opportunity, OpportunityEvent, STAGE_CONTACTED, STAGE_PROSPECT,
)

log = logging.getLogger(__name__)
CENTRAL = ZoneInfo("America/Chicago")
SEND_START_HOUR, SEND_END_HOUR = 8, 18
WORKING_STAGES = (STAGE_PROSPECT, STAGE_CONTACTED)

DEFAULT_BOOKING_URL = "https://evosyspro.live/request-demo/"
DEFAULT_FROM = "info@evosyspro.live"

# Drafts for Mike to edit and approve. {offer} is his offer text.
DEFAULT_STEPS = [
    {"day": 0, "channel": "email", "subject": "More commercial cleaning contracts for {company}",
     "body": ("Hi {first_name},\n\n"
              "I saw {company}'s ad for commercial cleaning. EvoSys Pro finds offices, "
              "medical buildings and other businesses near you that need cleaning, "
              "reaches out to them, and books walkthroughs straight onto your calendar.\n\n"
              "{offer}\n\n"
              "Want to see how it would work for {company}? Pick a time here: {booking_link}\n\n"
              "{sender_name}\nEvoSys Pro")},
    {"day": 3, "channel": "email", "subject": "Re: More commercial cleaning contracts for {company}",
     "body": ("Hi {first_name},\n\n"
              "Following up on my note. We do the prospecting and follow-up so you can focus "
              "on cleaning. You only meet the businesses that want a walkthrough.\n\n"
              "15 minutes is enough to show you: {booking_link}\n\n{sender_name}")},
    {"day": 7, "channel": "email", "subject": "Walkthroughs for {company}",
     "body": ("Hi {first_name},\n\n"
              "Quick one: would a few more booked walkthroughs a month help {company}? "
              "{offer}\n\nGrab a time: {booking_link}\n\n{sender_name}")},
    {"day": 14, "channel": "email", "subject": "Should I close your file?",
     "body": ("Hi {first_name},\n\n"
              "I haven't heard back, so I'll assume now isn't the right time. If you'd like "
              "more commercial accounts later, you can book a call anytime: {booking_link}\n\n"
              "{sender_name}")},
]
DEFAULT_OFFER = "[Your offer goes here: the $250 Growth Pilot or the free 30-day trial]"


# ── brand ─────────────────────────────────────────────────────────────────────

def brand_id(db: Session) -> Optional[str]:
    """The EvoSys Pro sales team (not the demonstration team)."""
    env = (os.environ.get("EVOSYS_SALES_BRAND_ID") or "").strip()
    if env:
        return env
    from app.models.models import Platform
    rows = (db.query(BrandSalesOrg).join(Platform, Platform.id == BrandSalesOrg.platform_id)
            .filter(Platform.slug == "evosyspro").all())
    rows = [r for r in rows if "demonstration" not in (r.name or "").lower()
            and r.is_active is not False]
    rows.sort(key=lambda r: (r.created_at or datetime.min))
    return rows[0].id if rows else None


# ── settings ──────────────────────────────────────────────────────────────────

def get_settings(db: Session, bso_id: str) -> EvoSysOutreachSettings:
    row = db.get(EvoSysOutreachSettings, bso_id)
    if row is None:
        row = EvoSysOutreachSettings(
            brand_sales_org_id=bso_id, steps=[dict(s) for s in DEFAULT_STEPS], offer=DEFAULT_OFFER,
            booking_url=DEFAULT_BOOKING_URL, sender_name="Mike Simmons", from_email=DEFAULT_FROM,
            reply_to_email=DEFAULT_FROM, mailing_address=None, daily_limit=60, sending_on=False)
        db.add(row)
        db.flush()
    return row


def settings_problems(s: EvoSysOutreachSettings) -> list:
    """What must be fixed before the steps can be approved. Plain words."""
    out = []
    steps = s.steps or []
    if not steps:
        out.append("Add at least one step.")
    if not (s.mailing_address or "").strip():
        out.append("Add a mailing address. The law requires one in every sales email.")
    if not (s.offer or "").strip() or (s.offer or "").strip().startswith("[Your offer"):
        out.append("Write the offer.")
    if not (s.booking_url or "").strip().lower().startswith("http"):
        out.append("Add the booking link.")
    if not re.match(r"[^@\s]+@evosyspro\.live$", (s.from_email or "").strip().lower()):
        out.append("The From address must be an @evosyspro.live address.")
    if not re.match(r"[^@+\s]+@evosyspro\.live$", (s.reply_to_email or "").strip().lower()):
        out.append("Replies must go to an @evosyspro.live address, so they can be read and matched.")
    for i, st in enumerate(steps, 1):
        if st.get("channel") == "email" and not (st.get("subject") or "").strip():
            out.append("Step %d needs a subject." % i)
        if not (st.get("body") or "").strip():
            out.append("Step %d needs a message." % i)
    return out


def save_settings(db: Session, s: EvoSysOutreachSettings, data: dict, user_id: str) -> None:
    content_keys = ("steps", "offer", "booking_url", "sender_name", "from_email",
                    "reply_to_email", "mailing_address")
    changed = False
    for k in content_keys + ("daily_limit",):
        if k in data and data[k] is not None:
            v = data[k]
            if k == "steps":
                v = [_clean_step(x) for x in (v or [])][:10]
            if k == "daily_limit":
                v = max(1, min(int(v), 300))
            if getattr(s, k) != v:
                setattr(s, k, v)
                changed = changed or k in content_keys
    if changed:
        # New wording needs a new approval, and sending stops until then.
        s.approved_at = None
        s.approved_by = None
        s.sending_on = False
    s.updated_at = datetime.utcnow()
    s.updated_by = user_id


def _clean_step(x: dict) -> dict:
    ch = "text" if (x or {}).get("channel") == "text" else "email"
    day = max(0, min(int((x or {}).get("day") or 0), 90))
    return {"day": day, "channel": ch, "subject": str((x or {}).get("subject") or "")[:200],
            "body": str((x or {}).get("body") or "")[:5000]}


# ── rendering ─────────────────────────────────────────────────────────────────

def _first_name(opp: Opportunity) -> str:
    n = (opp.contact_name or "").strip().split()
    return n[0].title() if n else "there"


def fill(text: str, opp: Opportunity, s: EvoSysOutreachSettings, campaign=None) -> str:
    src = campaign if campaign is not None else s
    vals = {"first_name": _first_name(opp), "company": (opp.company_name or "your company").strip(),
            "offer": (getattr(src, "offer", None) or "").strip(),
            "booking_link": (getattr(src, "booking_url", None) or "").strip(),
            "sender_name": (s.sender_name or "").strip()}
    return re.sub(r"\{(first_name|company|offer|booking_link|sender_name)\}",
                  lambda m: vals[m.group(1)], text or "")


def opt_out_url(opp_id: str) -> str:
    from app.services.programs import unsubscribe as _u
    base = (os.environ.get("PROGRAM_ASSET_BASE_URL") or "").strip().rstrip("/")
    if not base:
        from app.services.twilio_callbacks import public_api_base
        base = public_api_base()
    return "%s/evosys-sales-public/stop/%s" % (base, _u.make_token("evs:" + opp_id))


def read_opt_out(token: str) -> Optional[str]:
    from app.services.programs import unsubscribe as _u
    v = _u.read_token(token or "")
    return v[4:] if v and v.startswith("evs:") else None


def render_email(opp: Opportunity, s: EvoSysOutreachSettings, step: dict, campaign=None) -> dict:
    subject = fill(step.get("subject"), opp, s, campaign)
    text = fill(step.get("body"), opp, s, campaign)
    paras = "".join("<p>%s</p>" % _html.escape(p).replace("\n", "<br>")
                    for p in text.split("\n\n") if p.strip())
    paras = re.sub(r"(https?://[^\s<]+)", r'<a href="\1">\1</a>', paras)
    footer = ('<p style="font-size:12px;color:#6b7f8c;margin-top:24px">%s<br>'
              'Not interested? <a href="%s">Stop these emails</a>.</p>'
              % (_html.escape((s.mailing_address or "").strip()), opt_out_url(opp.id)))
    return {"subject": subject, "text": text, "html": paras + footer}


# ── enrolment and stopping ────────────────────────────────────────────────────

def _has_email(opp) -> bool:
    return bool(re.match(r"[^@\s]+@[^@\s]+\.[^@\s]+$", (opp.email or "").strip()))


def enroll_new(db: Session, bso_id: str, now: datetime) -> int:
    """Every working lead with an email, a clear group and no follow-up yet
    joins the campaign that fits it - only if that campaign is approved and on.
    A lead whose campaign is off simply waits; nothing is queued for it."""
    from app.services.evosys_sales import campaigns as CA, grouping as G
    from app.models.evosys_sales_models import EvoSysLead
    s = get_settings(db, bso_id)
    have = db.query(EvoSysOutreachState.opportunity_id).filter(
        EvoSysOutreachState.brand_sales_org_id == bso_id)
    rows = (db.query(Opportunity).filter(Opportunity.brand_sales_org_id == bso_id,
                                         Opportunity.stage.in_(WORKING_STAGES),
                                         Opportunity.status == "open",
                                         ~Opportunity.id.in_(have)).all())
    n = 0
    for o in rows:
        if not _has_email(o):
            continue
        lead = db.get(EvoSysLead, o.id) or G.ensure(db, s, o)
        if lead.review_status not in G.SENDABLE:
            continue
        c = CA.campaign_for(db, s, lead)
        if c is None or not CA.can_send(c, s):
            continue
        db.add(EvoSysOutreachState(opportunity_id=o.id, brand_sales_org_id=bso_id, status="active",
                                   next_step=0, next_due_at=now, started_at=now, campaign_id=c.id))
        n += 1
    db.flush()
    return n


def stop(db: Session, opp_id: str, reason: str, now: Optional[datetime] = None) -> Optional[EvoSysOutreachState]:
    now = now or datetime.utcnow()
    st = db.get(EvoSysOutreachState, opp_id)
    if st is None:
        opp = db.get(Opportunity, opp_id)
        if opp is None:
            return None
        st = EvoSysOutreachState(opportunity_id=opp_id, brand_sales_org_id=opp.brand_sales_org_id,
                                 started_at=now)
        db.add(st)
    if st.status == "active":
        st.status = "stopped"
        st.stop_reason = reason
        st.stopped_at = now
        st.next_due_at = None
    if reason == "opted_out":
        st.opted_out_at = st.opted_out_at or now
    if reason == "replied":
        st.replied_at = st.replied_at or now
    db.flush()
    return st


def restart(db: Session, opp_id: str, now: Optional[datetime] = None) -> Optional[EvoSysOutreachState]:
    now = now or datetime.utcnow()
    st = db.get(EvoSysOutreachState, opp_id)
    if st is None or st.opted_out_at is not None:
        return None
    st.status, st.stop_reason, st.stopped_at, st.next_due_at = "active", None, None, now
    db.flush()
    return st


def log_reply(db: Session, opp: Opportunity, user_id: str, note: str, channel: str = "email") -> EvoSysOutreachMessage:
    now = datetime.utcnow()
    m = EvoSysOutreachMessage(brand_sales_org_id=opp.brand_sales_org_id, opportunity_id=opp.id,
                              direction="in", channel=channel if channel in ("email", "text", "call") else "email",
                              body=(note or "").strip()[:5000] or "(replied)", status="logged", match_method="manual",
                              actor_user_id=user_id, created_at=now)
    db.add(m)
    stop(db, opp.id, "replied", now)
    db.add(OpportunityEvent(opportunity_id=opp.id, event_type="outreach_reply",
                            summary="Reply logged - automatic follow-up stopped",
                            detail=(note or "")[:2000], actor_user_id=user_id, occurred_at=now))
    db.flush()
    return m


# ── the pass ──────────────────────────────────────────────────────────────────

def in_send_window(now_utc: datetime) -> bool:
    local = now_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(CENTRAL)
    return local.weekday() < 5 and SEND_START_HOUR <= local.hour < SEND_END_HOUR


def sent_today(db: Session, bso_id: str, now_utc: datetime, campaign_id: Optional[str] = None,
               tz: str = "America/Chicago") -> int:
    local = now_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(tz))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    q = (db.query(func.count(EvoSysOutreachMessage.id))
         .filter(EvoSysOutreachMessage.brand_sales_org_id == bso_id,
                 EvoSysOutreachMessage.direction == "out",
                 EvoSysOutreachMessage.channel == "email",
                 EvoSysOutreachMessage.status == "sent",
                 EvoSysOutreachMessage.created_at >= start))
    if campaign_id:
        q = q.filter(EvoSysOutreachMessage.campaign_id == campaign_id)
    return q.scalar() or 0


def _booked(db: Session, opp_id: str) -> bool:
    from app.models.scheduling_models import SalesAppointment
    return db.query(SalesAppointment.id).filter(SalesAppointment.opportunity_id == opp_id).first() is not None


class _Sender:
    """What send_email_via_provider reads off an org: from, name, reply-to."""
    resolved = True

    def __init__(self, s: EvoSysOutreachSettings, reply_to: Optional[str] = None):
        self.from_email = (s.from_email or DEFAULT_FROM).strip()
        self.from_name = ((s.sender_name or "").strip() + " | EvoSys Pro").strip(" |")
        self.reply_to_email = (reply_to or s.reply_to_email or "").strip() or None
        self.cc_email = None
        self.resend_api_key = None


def _send(to: str, msg: dict, s: EvoSysOutreachSettings, opp_id: str) -> dict:
    from app.services.email_service import send_email_via_provider
    return send_email_via_provider(
        to, msg["subject"], msg["html"], org=_Sender(s, msg.get("reply_to")), message_type="sales_outreach",
        sensitivity="none",
        headers={"List-Unsubscribe": "<%s>" % opt_out_url(opp_id),
                 "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"})


def run_pass(db: Session, now: Optional[datetime] = None, sender=None) -> dict:
    """One pass: enrol new leads into campaigns that are approved and on, then
    send what is due - per campaign, inside that campaign's days and hours and
    daily limit - and advance each lead. Safe to run repeatedly.

    A due lead whose campaign is off or no longer approved is NOT sent and NOT
    moved: it waits ("paused with emails waiting") and goes out when the
    campaign is approved and on again, without being queued twice."""
    from app.services.evosys_sales import campaigns as CA, grouping as G, replies as _replies
    from app.services import sales_suppression as SDNC
    from app.models.evosys_sales_models import EvoSysLead
    now = now or datetime.utcnow()
    sender = sender or _send
    bso_id = brand_id(db)
    out = {"brand": bso_id, "enrolled": 0, "sent": 0, "failed": 0, "held": 0, "stopped": 0,
           "waiting": 0, "skipped": None}
    if not bso_id:
        out["skipped"] = "no EvoSys sales team"
        return out
    s = get_settings(db, bso_id)
    camps = {c.id: c for c in CA.all_for(db, s)}
    live = {cid: c for cid, c in camps.items() if CA.can_send(c, s)}
    db.commit()
    if not live:
        out["skipped"] = "off"
        return out
    out["enrolled"] = enroll_new(db, bso_id, now)
    db.commit()
    open_now = {cid for cid, c in live.items() if CA.in_window(c, now)}
    if not open_now:
        out["skipped"] = "outside sending hours"
        return out
    room = {cid: max(0, int(live[cid].daily_limit or 0) - sent_today(db, bso_id, now, cid, live[cid].timezone))
            for cid in open_now}
    due = (db.query(EvoSysOutreachState)
           .filter(EvoSysOutreachState.brand_sales_org_id == bso_id,
                   EvoSysOutreachState.status == "active",
                   EvoSysOutreachState.next_due_at <= now)
           .order_by(EvoSysOutreachState.next_due_at)
           .with_for_update(skip_locked=True).limit(500).all())
    for st in due:
        if st.campaign_id not in open_now:
            out["waiting"] += 1          # campaign off, unapproved or outside its hours: wait
            continue
        c = live[st.campaign_id]
        steps = c.steps or []
        opp = db.get(Opportunity, st.opportunity_id)
        if opp is None or opp.stage not in WORKING_STAGES or opp.status != "open":
            stop(db, st.opportunity_id, "stage", now); out["stopped"] += 1; continue
        if _booked(db, opp.id):
            stop(db, opp.id, "booked", now); out["stopped"] += 1; continue
        if not _has_email(opp):
            stop(db, opp.id, "no_email", now); out["stopped"] += 1; continue
        if st.next_step >= len(steps):
            st.status, st.next_due_at = "finished", None; continue
        step = steps[st.next_step]
        if step.get("channel") == "text":
            # No EvoSys texting number: recorded once, never sent.
            db.add(EvoSysOutreachMessage(brand_sales_org_id=bso_id, opportunity_id=opp.id, direction="out",
                                         channel="text", step_no=st.next_step + 1, to_address=opp.phone,
                                         body=fill(step.get("body"), opp, s, c), status="held", campaign_id=c.id,
                                         error="EvoSys Sales has no texting number yet.", created_at=now))
            out["held"] += 1
            _advance(st, steps, now)
            continue
        if room[c.id] <= 0:
            out["waiting"] += 1
            continue
        # Re-read this lead right before sending: a reply, opt-out, booking,
        # hold or Do Not Contact recorded since the due list was read (by the
        # mailbox poller, the opt-out link, a person) must stop this email.
        db.refresh(st)
        db.refresh(opp)
        db.refresh(c)
        if st.status != "active" or st.replied_at or st.opted_out_at:
            out["stopped"] += 1
            continue
        if not CA.can_send(c, s):
            out["waiting"] += 1
            continue
        if opp.stage not in WORKING_STAGES or _booked(db, opp.id):
            stop(db, opp.id, "booked" if _booked(db, opp.id) else "stage", now); out["stopped"] += 1; continue
        if SDNC.for_opportunity(db, opp) or SDNC.find(db, bso_id, opp.phone, opp.email):
            stop(db, opp.id, "opted_out", now); out["stopped"] += 1; continue
        lead = db.get(EvoSysLead, opp.id)
        if lead is not None and lead.review_status == "excluded":
            stop(db, opp.id, "excluded", now); out["stopped"] += 1; continue
        if lead is not None and lead.review_status not in G.SENDABLE:
            out["waiting"] += 1          # held / needs review: not sent, not moved
            continue
        msg = render_email(opp, s, step, c)
        msg["reply_to"] = _replies.tagged_address(s.reply_to_email or DEFAULT_FROM, _replies.ensure_code(db, st))
        try:
            r = sender(opp.email.strip(), msg, s, opp.id)
        except Exception as e:                                    # noqa: BLE001
            r = {"success": False, "error": str(e)}
        ok = bool(r.get("success"))
        db.add(EvoSysOutreachMessage(brand_sales_org_id=bso_id, opportunity_id=opp.id, direction="out",
                                     channel="email", step_no=st.next_step + 1, to_address=opp.email.strip(),
                                     subject=msg["subject"], body=msg["text"], campaign_id=c.id,
                                     status="sent" if ok else "failed",
                                     error=None if ok else str(r.get("error") or "")[:1000],
                                     provider_message_id=r.get("provider_message_id"), created_at=now))
        if ok:
            out["sent"] += 1
            room[c.id] -= 1
            if opp.stage == STAGE_PROSPECT:
                opp.stage = STAGE_CONTACTED
                opp.stage_changed_at = now
            opp.contacted_at = opp.contacted_at or now
            db.add(OpportunityEvent(opportunity_id=opp.id, event_type="outreach_email",
                                    summary="Follow-up email %d sent (%s)" % (st.next_step + 1, c.name),
                                    detail=msg["subject"], occurred_at=now))
            _advance(st, steps, now)
        else:
            out["failed"] += 1
            # Try again in an hour; a bad address stops after two failures.
            db.flush()
            fails = (db.query(func.count(EvoSysOutreachMessage.id))
                     .filter(EvoSysOutreachMessage.opportunity_id == opp.id,
                             EvoSysOutreachMessage.status == "failed").scalar() or 0)
            if fails >= 2:
                stop(db, opp.id, "email_failed", now)
            else:
                st.next_due_at = now + timedelta(hours=1)
        db.commit()
    db.commit()
    return out


def _advance(st: EvoSysOutreachState, steps: list, now: datetime) -> None:
    cur = steps[st.next_step]
    st.next_step += 1
    if st.next_step >= len(steps):
        st.status, st.next_due_at = "finished", None
        return
    gap = max(1, int(steps[st.next_step].get("day") or 0) - int(cur.get("day") or 0))
    st.next_due_at = now + timedelta(days=gap)
