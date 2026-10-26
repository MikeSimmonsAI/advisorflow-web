"""THE ONE post-appointment survey path (#405).

WHAT WENT WRONG (production, Aug-Oct 2026). Two jobs sent surveys:
  * review_request_cron (every 30 min) and
  * post_appointment_service (every 2 min, from the email poller).
Both compared `BookingLink.booked_time` - stored as the advisor's NAIVE LOCAL
wall time (app/services/booking_time.py) - with the clock in UTC. In Central
time that is 5 hours (CDT) early, so a 2:00 PM visit looked over at 9:00 AM:
Walter Johnson's survey arrived 10:27 AM for a 2:00 PM appointment, and every
Restland family got TWO surveys, both before the visit, one at 5 AM. The
Restland texts went straight to Twilio, so they are in no message history.
Neither job asked whether the visit actually happened.

THE RULE NOW. One family, one completed appointment, one survey:

  ELIGIBLE   the booking is booked/confirmed; its END (start + duration, in
             the advisor's zone, converted to UTC) is at least BUFFER ago and
             no more than LOOKBACK ago; a person marked the visit COMPLETED
             (LeadOutcome.attendance == "completed"); it was never surveyed
             (no review_request_sent_at, no BookingFollowup); nobody answered
             a survey for it.
  CLAIM      one conditional UPDATE wins the booking (review_request_sent_at
             IS NULL and no followup row), committed before any send, so two
             workers or two runs can never both send. A crash after the claim
             leaves the row 'claimed' - never resent automatically.
  CHANNEL    a TEXT only if the exact message the gateway would send still
             carries the link (a sender not approved for links has it
             stripped - then no text claims to carry a survey); otherwise an
             EMAIL through the approved email gateway; otherwise HELD with the
             reason. Never both.
  GATEWAYS   sms_service.send_sms (STOP/DNC/suppression/consent/program and
             campaign gates, delivery receipts, message history) and
             outbound_email_gate.send_lead_email (compliance, switches,
             unsubscribe, email history). No direct provider call here.
  RECORD     state claimed|sent|held|failed with a plain reason, the message
             id whose own provider status is the delivery truth, attempts.
  RECOVERY   retry() re-attempts a held/failed survey only after checking
             nothing was delivered and nobody answered - a person's decision.

SWITCH. SURVEY_SEND_ENABLED (default OFF): with it off a pass claims and sends
nothing. Turning it on is the owner's release decision.
"""
from __future__ import annotations

import logging
import os
import secrets
import string
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

BUFFER = timedelta(minutes=60)          # after the visit ENDS
LOOKBACK = timedelta(days=7)            # never chase old visits
DEFAULT_MINUTES = 60
COMPLETED = "completed"
NEVER = ("no_show", "cancelled")
LIVE_STATUSES = ("booked", "confirmed")

TOKEN_ALPHABET = string.ascii_letters + string.digits
TOKEN_LENGTH = 12                        # 62^12 ~ 3.2e21 (~71 bits)

STATE_CLAIMED, STATE_SENT, STATE_HELD, STATE_FAILED = "claimed", "sent", "held", "failed"

SMS_TEMPLATE = "Hi {first}, thank you for visiting {place}. How did we do? {url}"
EMAIL_SUBJECT = "How was your visit with {place}?"


def sending_enabled() -> bool:
    return (os.environ.get("SURVEY_SEND_ENABLED") or "").strip().lower() in ("1", "true", "yes", "on")


# ── links ───────────────────────────────────────────────────────────────────

def new_token(db: Session) -> str:
    """A short opaque survey token: random, nothing about the family in it.
    Checked against every issued token (UUIDs included); the column's unique
    index is the final guard."""
    from app.models.models import BookingFollowup
    for _ in range(8):
        tok = "".join(secrets.choice(TOKEN_ALPHABET) for _ in range(TOKEN_LENGTH))
        if db.query(BookingFollowup.id).filter(BookingFollowup.survey_token == tok).first() is None:
            return tok
    raise RuntimeError("could not mint a unique survey token")


# ── time ────────────────────────────────────────────────────────────────────

def appointment_end_utc(db: Session, booking) -> Optional[datetime]:
    """When the visit ENDS, as naive UTC. booked_time is the advisor's local
    wall time; an aware value is converted as-is."""
    if booking.booked_time is None:
        return None
    from app.services import booking_time as bkt
    start = bkt.to_utc(booking.booked_time, bkt.zone_of_user(db, booking.user_id))
    return start + timedelta(minutes=int(booking.appt_duration or DEFAULT_MINUTES))


def attendance(db: Session, booking) -> Optional[str]:
    """The latest attendance a person recorded for this visit, or None."""
    from app.models.models import LeadOutcome
    row = (db.query(LeadOutcome.attendance)
           .filter(LeadOutcome.booking_link_id == booking.id, LeadOutcome.attendance.isnot(None))
           .order_by(LeadOutcome.updated_at.desc().nullslast(), LeadOutcome.created_at.desc().nullslast())
           .first())
    return row[0] if row else None


def ineligible_reason(db: Session, booking, now: Optional[datetime] = None) -> Optional[str]:
    """None = this visit may be surveyed now; otherwise why not, in plain words."""
    from app.models.models import BookingFollowup, Lead
    now = now or datetime.utcnow()
    if (booking.status or "") not in LIVE_STATUSES:
        return "the appointment is %s" % (booking.status or "not booked")
    end = appointment_end_utc(db, booking)
    if end is None:
        return "the appointment has no time"
    if end + BUFFER > now:
        return "the visit is not over yet"
    if end < now - LOOKBACK:
        return "the visit was more than %d days ago" % LOOKBACK.days
    att = attendance(db, booking)
    if att in NEVER:
        return "the appointment was marked %s" % att.replace("_", "-")
    if att != COMPLETED:
        return "nobody has marked the visit completed"
    if booking.review_request_sent_at is not None or db.query(BookingFollowup.id).filter(
            BookingFollowup.booking_link_id == booking.id).first() is not None:
        return "a survey was already handled for this visit"
    lead = db.query(Lead).filter(Lead.id == booking.lead_id).first()
    if lead is None:
        return "the family's record is missing"
    if (lead.status or "").lower() == "dnc":
        return "the family is do-not-contact"
    return None


def candidates(db: Session, now: Optional[datetime] = None) -> List:
    """Bookings that MAY be due. A coarse SQL window (booked_time is local, so
    +/- booking_time.WINDOW), then the exact per-row rule above."""
    from app.models.models import BookingLink, LeadOutcome
    from app.services import booking_time as bkt
    now = now or datetime.utcnow()
    completed = (db.query(LeadOutcome.id)
                 .filter(LeadOutcome.booking_link_id == BookingLink.id,
                         LeadOutcome.attendance == COMPLETED).exists())
    rows = (db.query(BookingLink)
            .filter(BookingLink.status.in_(LIVE_STATUSES),
                    BookingLink.booked_time.isnot(None),
                    BookingLink.review_request_sent_at.is_(None),
                    BookingLink.booked_time <= now + bkt.WINDOW,
                    BookingLink.booked_time >= now - LOOKBACK - bkt.WINDOW - timedelta(days=1),
                    completed)
            .order_by(BookingLink.booked_time.desc()).limit(500).all())
    return [b for b in rows if ineligible_reason(db, b, now) is None]


# ── claim ───────────────────────────────────────────────────────────────────

def claim(db: Session, booking, now: Optional[datetime] = None):
    """Win this booking or return None. One conditional UPDATE: exactly one
    runner can see rowcount 1. The followup row rides in the same commit."""
    from app.models.models import BookingFollowup
    now = now or datetime.utcnow()
    won = db.execute(text(
        "UPDATE booking_links SET review_request_sent_at = :now "
        "WHERE id = :id AND review_request_sent_at IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM booking_followups f WHERE f.booking_link_id = :id)"),
        {"id": booking.id, "now": now})
    if won.rowcount != 1:
        db.rollback()
        return None
    fu = BookingFollowup(booking_link_id=booking.id, lead_id=booking.lead_id, advisor_id=booking.user_id,
                         channel="none", survey_token=new_token(db), thank_you_sent=False,
                         survey_link_sent=False, state=STATE_CLAIMED, state_reason="claimed; sending",
                         attempts=0)
    db.add(fu)
    db.commit()
    return fu


# ── content ─────────────────────────────────────────────────────────────────

def place_for(db: Session, lead) -> Tuple[Optional[str], Optional[str]]:
    """(the business's customer-facing name, held reason). Program contacts:
    their own funeral home / cemetery, never the corporation."""
    from app.services import survey_identity as si
    loc = si.location_identity(db, lead)
    if loc is not None:
        return (loc["name"], None) if loc["resolved"] and loc["name"] else (None, si.HELD_NO_LOCATION)
    from app.services.public_identity import identity_for_org
    from app.models.models import Organization
    ident = identity_for_org(db, lead.organization_id)
    name = ident.customer_facing_name
    if not name:
        org = db.query(Organization).filter(Organization.id == lead.organization_id).first()
        name = org.name if org else None
    return (name, None) if name else (None, "held: this business has no customer-facing name")


def _email_html(first: str, place: str, url: str) -> str:
    import html as _h
    return ("<p>Hi %s,</p><p>Thank you for visiting %s. Would you tell us how it went? "
            "It takes less than a minute.</p><p><a href=\"%s\">Share your feedback</a></p>"
            % (_h.escape(first), _h.escape(place), _h.escape(url, quote=True)))


# ── deliver ─────────────────────────────────────────────────────────────────

def _record(db: Session, fu, *, state: str, reason: Optional[str], channel: str = "none",
            message_id: Optional[str] = None, email_message_id: Optional[str] = None,
            lead=None, lead_status: Optional[str] = None) -> None:
    # A survey is not outreach: the gateways mark a lead "sent", which would
    # move a booked/won family back in the pipeline. Keep what it was.
    if lead is not None and lead_status is not None and lead.status != lead_status:
        lead.status = lead_status
    fu.state, fu.state_reason = state, (reason or None) and reason[:500]
    fu.channel = channel
    fu.message_id = message_id or fu.message_id
    fu.email_message_id = email_message_id or fu.email_message_id
    sent = state == STATE_SENT
    fu.survey_link_sent = sent
    fu.thank_you_sent = sent
    fu.error = None if sent else fu.state_reason
    if sent:
        fu.sent_at = datetime.utcnow()
    db.commit()


def deliver(db: Session, fu, booking, lead, advisor, *, allow_text: bool = True,
            text_blocked_reason: Optional[str] = None) -> str:
    """Send ONE survey for a claimed followup. Returns the final state.
    allow_text=False: email or hold only (e.g. the family's time zone is unknown,
    so texting hours cannot be honoured)."""
    from app.services import send_source as ss
    from app.services import survey_identity as si
    from app.services.public_identity import survey_url
    fu.attempts = int(fu.attempts or 0) + 1
    fu.last_attempt_at = datetime.utcnow()
    db.commit()
    place, held = place_for(db, lead)
    if held:
        _record(db, fu, state=STATE_HELD, reason=held)
        return STATE_HELD
    url = survey_url(db, lead.organization_id, fu.survey_token)
    if not url:
        _record(db, fu, state=STATE_HELD, reason=si.HELD_NO_LINK)
        return STATE_HELD
    first = si.proper_name(lead.first_name) or "there"
    reasons: List[str] = []
    prior_status = lead.status
    keep = dict(lead=lead, lead_status=prior_status)

    # TEXT - only when the exact text the gateway sends still carries the link.
    if lead.phone and not allow_text:
        reasons.append(text_blocked_reason or "text: not allowed right now")
    elif lead.phone and getattr(lead, "phone_line_type", None) != "landline":
        from app.services import sms_service
        body = SMS_TEMPLATE.format(first=first, place=place, url=url)
        try:
            preview = sms_service.compose_body(body, lead, advisor, "")
        except Exception as exc:                          # noqa: BLE001
            preview = ""
            reasons.append("text not possible: %s" % str(exc)[:160])
        if preview and url not in preview:
            reasons.append("text: this sender is not approved to carry links, so no survey text")
        elif preview:
            msg = None
            try:
                msg = sms_service.send_sms(db, advisor, lead, body, include_booking_link=False,
                                           send_source=ss.APPOINTMENT_FOLLOWUP)
            except Exception as exc:                      # noqa: BLE001 - a refusal or a provider failure
                db.rollback()
                reasons.append("text refused: %s" % str(exc)[:200])
            if msg is not None:
                # The provider ACCEPTED a text. Recorded OUTSIDE the send's try:
                # a bookkeeping failure here must never be read as "the text was
                # refused" and fall through to an email as well.
                if url not in (getattr(msg, "body", "") or ""):
                    _record(db, fu, state=STATE_FAILED, channel="sms", message_id=getattr(msg, "id", None),
                            reason="a text went out WITHOUT the survey link - check the sender's link approval",
                            **keep)
                    return STATE_FAILED
                _record(db, fu, state=STATE_SENT, reason=None, channel="sms",
                        message_id=getattr(msg, "id", None), **keep)
                return STATE_SENT
    elif lead.phone:
        reasons.append("text: the number is a landline")

    # EMAIL - only when no text went.
    if (lead.email or "").strip() and getattr(lead, "allow_email", None) is not False:
        from app.services import outbound_email_gate
        row = None
        try:
            row = outbound_email_gate.send_lead_email(
                db, lead, advisor=advisor, subject=EMAIL_SUBJECT.format(place=place),
                body_html=_email_html(first, place, url), send_source=ss.APPOINTMENT_FOLLOWUP,
                actor_user_id=None)
        except Exception as exc:                          # noqa: BLE001
            db.rollback()
            reasons.append("email refused: %s" % str(exc)[:200])
        if row is not None:
            _record(db, fu, state=STATE_SENT, reason=None, channel="email",
                    email_message_id=getattr(row, "id", None), **keep)
            return STATE_SENT
    elif (lead.email or "").strip():
        reasons.append("email: the family unsubscribed")

    if not reasons:
        reasons.append("no phone that can take a survey link and no email")
    # Held = deliberately not sent (no channel allowed it); failed = a send was refused.
    refused = any("refused" in r for r in reasons)
    _record(db, fu, state=STATE_FAILED if refused else STATE_HELD, reason="; ".join(reasons), **keep)
    return STATE_FAILED if refused else STATE_HELD


# ── one pass ────────────────────────────────────────────────────────────────

def run(db: Session, now: Optional[datetime] = None) -> Dict:
    """One pass. Never raises for one bad booking."""
    from app.models.models import Lead, User
    from app.services import contact_hours
    out = {"enabled": sending_enabled(), "sent": 0, "held": 0, "failed": 0, "skipped_hours": 0, "lost_claim": 0}
    if not out["enabled"]:
        return out
    now = now or datetime.utcnow()
    for booking in candidates(db, now):
        bid = booking.id
        try:
            lead = db.query(Lead).filter(Lead.id == booking.lead_id).first()
            advisor = db.query(User).filter(User.id == booking.user_id).first()
            if lead is None or advisor is None:
                continue
            allow_text, why_not = True, None
            hours = contact_hours.check(lead, now)
            if not hours["permitted"]:
                if hours.get("code") in (contact_hours.ZONE_UNKNOWN, contact_hours.ZONE_UNRESOLVABLE):
                    # Texting hours can never be known for this family: email
                    # (or hold with the reason) - never silently skipped forever.
                    allow_text, why_not = False, "text: the family's time zone is unknown, so texting hours can't be honoured"
                else:
                    out["skipped_hours"] += 1        # unclaimed: the next pass in hours sends it
                    continue
            fu = claim(db, booking, now)
            if fu is None:
                out["lost_claim"] += 1
                continue
            state = deliver(db, fu, booking, lead, advisor, allow_text=allow_text, text_blocked_reason=why_not)
            out[{"sent": "sent", "held": "held"}.get(state, "failed")] += 1
            log.info("survey booking=%s followup=%s state=%s", bid, fu.id, state)   # no token, no phone
        except Exception as exc:                          # noqa: BLE001
            db.rollback()
            log.error("survey pass failed for booking %s: %s", bid, type(exc).__name__)
    return out


def run_with_engine(engine) -> int:
    from sqlalchemy.orm import Session as _S
    with _S(engine) as db:
        return run(db)["sent"]


# ── people ──────────────────────────────────────────────────────────────────

def _delivered(db: Session, fu) -> bool:
    """Did anything for this followup reach the family (or might it have)?
    Also counts any appointment-follow-up text/email to this family since the
    claim, in case the send went out but its id was never recorded."""
    from app.models.models import EmailMessage, Message
    from app.services import send_source as ss
    since = fu.created_at or datetime.utcnow()
    if db.query(Message.id).filter(Message.lead_id == fu.lead_id, Message.send_source == ss.APPOINTMENT_FOLLOWUP,
                                   Message.sent_at >= since,
                                   Message.delivery_status.notin_(("failed", "undelivered"))).first():
        return True
    if db.query(EmailMessage.id).filter(EmailMessage.lead_id == fu.lead_id,
                                        EmailMessage.send_source == ss.APPOINTMENT_FOLLOWUP,
                                        EmailMessage.sent_at >= since,
                                        EmailMessage.status.notin_(("failed", "bounced"))).first():
        return True
    if fu.message_id:
        m = db.query(Message).filter(Message.id == fu.message_id).first()
        if m is not None and (m.delivery_status or "pending") not in ("failed", "undelivered"):
            return True
    if fu.email_message_id:
        e = db.query(EmailMessage).filter(EmailMessage.id == fu.email_message_id).first()
        if e is not None and (e.status or "") not in ("failed", "bounced"):
            return True
    return False


def attention(db: Session, org_id: str, limit: int = 100) -> List[Dict]:
    """Surveys a person should look at: held, failed, stuck mid-send, or sent
    but the carrier/mail server reported a failure."""
    from app.models.models import BookingFollowup, EmailMessage, Lead, Message
    since = datetime.utcnow() - timedelta(days=30)
    rows = (db.query(BookingFollowup, Lead).join(Lead, Lead.id == BookingFollowup.lead_id)
            .filter(Lead.organization_id == org_id, BookingFollowup.state.isnot(None),
                    BookingFollowup.created_at >= since)
            .order_by(BookingFollowup.created_at.desc()).limit(500).all())
    out = []
    for fu, lead in rows:
        why = None
        if fu.state in (STATE_HELD, STATE_FAILED):
            why = fu.state_reason
        elif fu.state in (STATE_CLAIMED, "retrying") and (fu.last_attempt_at or fu.created_at) \
                and (fu.last_attempt_at or fu.created_at) < datetime.utcnow() - timedelta(hours=1):
            why = "the send never finished - check whether it reached the family before retrying"
        elif fu.state == STATE_SENT and fu.message_id:
            m = db.query(Message).filter(Message.id == fu.message_id).first()
            if m is not None and (m.delivery_status or "") in ("failed", "undelivered"):
                why = "the carrier did not deliver the text%s" % (
                    " (%s)" % m.error_code if getattr(m, "error_code", None) else "")
        elif fu.state == STATE_SENT and fu.email_message_id:
            e = db.query(EmailMessage).filter(EmailMessage.id == fu.email_message_id).first()
            if e is not None and (e.status or "") in ("failed", "bounced"):
                why = "the email did not arrive (%s)" % e.status
        if why:
            out.append({"followup_id": fu.id, "lead_id": lead.id,
                        "name": ("%s %s" % (lead.first_name or "", lead.last_name or "")).strip().title() or None,
                        "state": fu.state, "reason": why, "attempts": int(fu.attempts or 0),
                        "channel": fu.channel,
                        "created_at": fu.created_at.isoformat() + "Z" if fu.created_at else None})
        if len(out) >= limit:
            break
    return out


def retry(db: Session, followup_id: str, org_id: str, now: Optional[datetime] = None) -> Dict:
    """A person's controlled recovery: re-attempt a held/failed survey, ONLY
    if nothing for it reached the family, nobody answered, the visit is still
    a completed visit inside the window, and no other survey went to this
    visit. The same link (token) is reused."""
    from app.models.models import BookingFollowup, BookingLink, Lead, SurveyResponse, User
    now = now or datetime.utcnow()
    fu = (db.query(BookingFollowup).join(Lead, Lead.id == BookingFollowup.lead_id)
          .filter(BookingFollowup.id == followup_id, Lead.organization_id == org_id).first())
    if fu is None:
        return {"ok": False, "reason": "not found"}
    since = fu.last_attempt_at or fu.created_at
    stuck = (fu.state in (STATE_CLAIMED, "retrying") and since is not None
             and since < now - timedelta(hours=1))
    if fu.state not in (STATE_HELD, STATE_FAILED) and not stuck:
        return {"ok": False, "reason": "only a held, failed or stuck survey can be retried"}
    if db.query(SurveyResponse.id).filter(SurveyResponse.booking_followup_id == fu.id).first():
        return {"ok": False, "reason": "the family already answered"}
    if _delivered(db, fu):
        return {"ok": False, "reason": "something for this survey may already have reached the family"}
    booking = db.query(BookingLink).filter(BookingLink.id == fu.booking_link_id).first()
    if booking is None:
        return {"ok": False, "reason": "the appointment is gone"}
    others = (db.query(BookingFollowup).filter(BookingFollowup.booking_link_id == booking.id,
                                               BookingFollowup.id != fu.id).all())
    if any(o.survey_link_sent or o.state == STATE_SENT for o in others):
        return {"ok": False, "reason": "another survey already went to this visit"}
    if attendance(db, booking) != COMPLETED:
        return {"ok": False, "reason": "nobody has marked the visit completed"}
    end = appointment_end_utc(db, booking)
    if end is None or end + BUFFER > now:
        return {"ok": False, "reason": "the visit is not over yet"}
    if not sending_enabled():
        return {"ok": False, "reason": "survey sending is switched off"}
    lead = db.query(Lead).filter(Lead.id == fu.lead_id).first()
    advisor = db.query(User).filter(User.id == fu.advisor_id).first()
    if lead is None or advisor is None:
        return {"ok": False, "reason": "the family's record is missing"}
    from app.services import contact_hours
    hours = contact_hours.check(lead, now)
    allow_text, why_not = True, None
    if not hours["permitted"]:
        if hours.get("code") in (contact_hours.ZONE_UNKNOWN, contact_hours.ZONE_UNRESOLVABLE):
            allow_text, why_not = False, "text: the family's time zone is unknown, so texting hours can't be honoured"
        else:
            return {"ok": False, "reason": "it is outside texting hours for this family - try again later"}
    # Claim the retry. The state AND the attempt count must still be what this
    # person saw: a second click, tab or manager matches nothing and stops.
    won = db.execute(text("UPDATE booking_followups SET state = 'retrying', state_reason = 'retrying' "
                          "WHERE id = :id AND state = :st AND COALESCE(attempts, 0) = :att"),
                     {"id": fu.id, "st": fu.state, "att": int(fu.attempts or 0)})
    if won.rowcount != 1:
        db.rollback()
        return {"ok": False, "reason": "someone else is already retrying it"}
    db.commit()
    db.refresh(fu)
    state = deliver(db, fu, booking, lead, advisor, allow_text=allow_text, text_blocked_reason=why_not)
    return {"ok": state == STATE_SENT, "state": state, "reason": fu.state_reason}


# ── answers ─────────────────────────────────────────────────────────────────

def notify_low_rating(db: Session, response) -> bool:
    """A 1-3 star answer reaches the responsible team. SCI: the program's
    staff alert (one per answer, deduplicated). Everyone else: an in-app
    notice to the appointment's advisor. True when someone was notified."""
    from app.models.models import Lead, Notification, NotificationType
    if response.rating is None or response.rating > 3:
        return False
    lead = db.query(Lead).filter(Lead.id == response.lead_id).first()
    if lead is None:
        return False
    name = ("%s %s" % (lead.first_name or "", lead.last_name or "")).strip().title() or "A family"
    from app.services.programs.identity import program_for_org
    prog = program_for_org(db, lead.organization_id)
    if prog is not None:
        from app.services.programs import alert_ledger as al
        from app.services.survey_identity import location_identity
        loc = location_identity(db, lead) or {}
        words = (response.feedback or "").strip()
        sms = al.sms_line("survey_low", name, words=words or None, location=loc.get("name"),
                          detail=None if words else "rated the visit %d of 5" % response.rating)
        import html as _h
        rows = al.page(db, prog, lead_id=lead.id, key="survey:%s" % response.id, kind="survey_low",
                       sms_text=sms, subject="Survey: %s rated a visit %d of 5" % (name, response.rating),
                       html="<p>%s rated their visit%s %d of 5.</p><p>%s</p>" % (
                           _h.escape(name), (" at " + _h.escape(loc["name"])) if loc.get("name") else "",
                           response.rating, _h.escape(words) or "(no comment)"),
                       coalesce=False)
        db.commit()
        # Only a page that actually went (or is going) counts - not one the
        # staff-alert gate skipped.
        return any(getattr(r, "send_state", None) in ("sending", "sent") or getattr(r, "delivered", False)
                   for r in rows)
    if not response.advisor_id:
        return False
    db.add(Notification(user_id=response.advisor_id, lead_id=lead.id, type=NotificationType.SURVEY_LOW_RATING,
                        message="%s rated their visit %d of 5%s" % (
                            name, response.rating, (": " + response.feedback[:200]) if response.feedback else ".")))
    db.commit()
    return True
