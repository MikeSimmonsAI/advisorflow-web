"""APPOINTMENT MESSAGES for a program with its own appointment rules
(Issue #507, Restland): the booking confirmation, two reminders, change and
cancellation notices, and the survey 24 hours after a COMPLETED visit.

ONE SCHEDULE PER APPOINTMENT. Every message is an AppointmentNotification row:
(booking, purpose, channel, version) is unique, where `version` is the
appointment time it was written for. So:
  * a double-clicked Confirm, a webhook retry or two workers produce ONE
    confirmation per channel;
  * a reschedule SUPERSEDES every unsent row of the old time and writes the
    new time's rows (one "updated" notice instead of a second confirmation);
  * a cancellation supersedes everything still pending and writes one
    "cancelled" acknowledgement per channel.

OFF BY DEFAULT. Nothing goes unless the program's `notifications.enabled` is
on (the survey also needs `survey_enabled`). Saving settings or deploying
never sends. Every SMS still passes send_sms (the workspace texts switch,
STOP/DNC, suppression, the program sender) and every email passes
send_email_to_lead (opt-out, compliance gate). A booking never erases an
opt-out: the channel is re-checked immediately before each send.

TIMING (America/Chicago, daylight-saving aware):
  * confirmation / updated / cancelled: immediate - they answer something the
    family just did; not held for the outreach window.
  * reminders: 24 hours and 2 hours before the start (editable). Inside the
    notification window (9:00 AM - 6:00 PM, editable) they go at that time;
    outside it they move to the PRECEDING permitted time (5:45 PM the evening
    before for an early-morning time) - only if that is still after the
    booking was made and before the visit; otherwise that reminder is
    skipped. A reminder whose time has already passed when the family books is
    skipped, never sent late in a burst.
  * survey: 24 hours after the visit's recorded end (or its scheduled end),
    only for visits staff marked COMPLETED. Outside the window it waits for
    the next 9:00 AM. Marked late -> the next permitted time from now.
    Appointments booked before the activation date are never surveyed.
  * a message that could not go while sending was off is not dumped later:
    reminders more than 2 hours, confirmations more than 1 hour and surveys
    more than 3 days past due are skipped with that reason.
Retries: a provider failure retries twice (15, then 60 minutes), then FAILED
with the error, for a person to look at. A refusal (opt-out, texts off) is
recorded as SKIPPED with the reason - never retried blindly.
"""
import logging
import secrets
from datetime import datetime, time, timedelta
from typing import Dict, List, Optional

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.reset_models import AppointmentNotification, AppointmentSurvey

log = logging.getLogger(__name__)

TZ = "America/Chicago"
IMMEDIATE = ("confirmation", "updated", "cancelled")
LIVE = ("booked", "confirmed")
OPEN = ("scheduled", "retry")
DEFAULTS = {
    "enabled": False,               # appointment messages (confirmations, reminders, notices)
    "survey_enabled": False,        # the 24-hour survey invitation
    "reminder_minutes": [1440, 120],  # 24 hours and 2 hours before the start
    "window": ["09:00", "18:00"],   # non-immediate messages only
    "survey_delay_hours": 24,
    "survey_expiry_days": 30,
    "active_since": None,           # ISO UTC: appointments booked before this get no survey/reminders
}
STALE = {"confirmation": timedelta(hours=1), "updated": timedelta(hours=1), "cancelled": timedelta(hours=1),
         "reminder": timedelta(hours=2), "survey": timedelta(days=3)}
RETRY_STEPS = [timedelta(minutes=15), timedelta(minutes=60)]
SAFE_PREWINDOW = (17, 45)           # "the preceding permitted time" = 5:45 PM


# ── settings ────────────────────────────────────────────────────────────────

def settings(policy: Optional[Dict]) -> Dict:
    raw = dict((policy or {}).get("notifications") or {})
    out = dict(DEFAULTS)
    out.update({k: v for k, v in raw.items() if k in DEFAULTS})
    try:
        out["reminder_minutes"] = sorted({int(x) for x in out["reminder_minutes"] if 15 <= int(x) <= 10080},
                                         reverse=True)
    except (TypeError, ValueError):
        out["reminder_minutes"] = list(DEFAULTS["reminder_minutes"])
    out["enabled"], out["survey_enabled"] = bool(out["enabled"]), bool(out["survey_enabled"])
    return out


def _policy_for_booking(db: Session, booking):
    from app.models.models import Lead
    from app.services.programs import booking_policy as BP
    lead = db.query(Lead).filter(Lead.id == booking.lead_id).first()
    if lead is None:
        return None, None
    return BP.for_org(db, lead.organization_id), lead


def _zone():
    from zoneinfo import ZoneInfo
    return ZoneInfo(TZ)


def _utc(local_naive: datetime) -> datetime:
    from datetime import timezone
    return local_naive.replace(tzinfo=_zone()).astimezone(timezone.utc).replace(tzinfo=None)


def _local(utc_naive: datetime) -> datetime:
    from datetime import timezone
    return utc_naive.replace(tzinfo=timezone.utc).astimezone(_zone()).replace(tzinfo=None)


def _window(s: Dict):
    (a, b) = s["window"]
    ah, am = (int(x) for x in a.split(":"))
    bh, bm = (int(x) for x in b.split(":"))
    return time(ah, am), time(bh, bm)


def in_window(utc_naive: datetime, s: Dict) -> bool:
    lo, hi = _window(s)
    t = _local(utc_naive).time()
    return lo <= t < hi


def preceding_permitted(utc_naive: datetime, s: Dict) -> datetime:
    """The latest permitted moment at or before this one (local wall clock)."""
    if in_window(utc_naive, s):
        return utc_naive
    lo, hi = _window(s)
    loc = _local(utc_naive)
    day = loc.date() if loc.time() >= hi else loc.date() - timedelta(days=1)
    pre = datetime.combine(day, time(*SAFE_PREWINDOW)) if time(*SAFE_PREWINDOW) < hi else \
        datetime.combine(day, hi) - timedelta(minutes=15)
    return _utc(pre)


def next_permitted(utc_naive: datetime, s: Dict) -> datetime:
    """The earliest permitted moment at or after this one."""
    if in_window(utc_naive, s):
        return utc_naive
    lo, _hi = _window(s)
    loc = _local(utc_naive)
    day = loc.date() if loc.time() < lo else loc.date() + timedelta(days=1)
    return _utc(datetime.combine(day, lo))


# ── which channels may carry appointment messages ───────────────────────────

def channels(db: Session, lead) -> List[str]:
    from app.services.programs.workspace_reset import e164
    out = []
    if e164(lead.phone) and lead.allow_sms is not False and (lead.status or "").lower() != "dnc":
        out.append("sms")
    if (lead.email or "").strip() and lead.allow_email is not False and (lead.manual_flag or "") != "bad_email" \
            and (lead.status or "").lower() != "dnc":
        out.append("email")
    return out


# ── scheduling ──────────────────────────────────────────────────────────────

def _version(booking) -> str:
    bt = booking.booked_time.replace(tzinfo=None) if getattr(booking.booked_time, "tzinfo", None) else booking.booked_time
    return bt.strftime("%Y-%m-%dT%H:%M")


def _start_utc(booking) -> datetime:
    bt = booking.booked_time.replace(tzinfo=None) if getattr(booking.booked_time, "tzinfo", None) else booking.booked_time
    return _utc(bt)                    # booked_time is Central wall time


def _add(db, booking, lead, purpose, channel, version, due, status="scheduled", reason=None) -> Optional[AppointmentNotification]:
    row = AppointmentNotification(organization_id=lead.organization_id, booking_id=booking.id, lead_id=lead.id,
                                  purpose=purpose, channel=channel, version=version, due_at=due, status=status,
                                  reason=reason)
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
        return row
    except IntegrityError:
        return None                    # already scheduled (another request / worker)


def _supersede(db: Session, booking, keep_version: Optional[str], why: str, purposes=None) -> int:
    q = db.query(AppointmentNotification).filter(AppointmentNotification.booking_id == booking.id,
                                                 AppointmentNotification.status.in_(OPEN))
    if keep_version is not None:
        q = q.filter(AppointmentNotification.version != keep_version)
    if purposes:
        q = q.filter(AppointmentNotification.purpose.in_(purposes))
    n = 0
    for r in q.all():
        r.status, r.reason = "superseded", why
        n += 1
    return n


def on_booked(db: Session, booking, *, event: str = "booked", now: Optional[datetime] = None,
              skip_immediate: bool = False) -> Dict:
    """Write this appointment's messages. Idempotent. Sends nothing itself."""
    now = now or datetime.utcnow()
    policy, lead = _policy_for_booking(db, booking)
    if policy is None or lead is None or booking.booked_time is None:
        return {"scheduled": 0, "reason": "no program appointment rules"}
    s = settings(policy)
    version = _version(booking)
    superseded = _supersede(db, booking, version, "the appointment moved") if event == "rescheduled" else 0
    if event == "rescheduled":
        superseded += _cancel_survey(db, booking)
    chans = channels(db, lead)
    made = 0
    purpose = "updated" if event == "rescheduled" else "confirmation"
    for ch in chans:
        if skip_immediate:
            continue
        if _add(db, booking, lead, purpose, ch, version, now):
            made += 1
    start = _start_utc(booking)
    for mins in s["reminder_minutes"]:
        key = "reminder_24h" if mins == 1440 else "reminder_2h" if mins == 120 else "reminder_%dm" % mins
        due = start - timedelta(minutes=mins)
        status, reason = "scheduled", None
        if due <= now:
            status, reason = "skipped", "booked inside this reminder's interval - not sent late"
        else:
            adj = preceding_permitted(due, s)
            if adj != due:
                if adj <= now or adj >= start:
                    status, reason = "skipped", "outside the notification window and no earlier permitted time"
                else:
                    reason = "moved into the notification window (was %s Central)" % _local(due).strftime("%-I:%M %p")
                    due = adj
        for ch in chans:
            if _add(db, booking, lead, key, ch, version, due, status, reason):
                made += 1
    db.flush()
    return {"scheduled": made, "superseded": superseded, "channels": chans, "enabled": s["enabled"]}


def on_cancelled(db: Session, booking, *, now: Optional[datetime] = None, acknowledge: bool = True) -> Dict:
    now = now or datetime.utcnow()
    policy, lead = _policy_for_booking(db, booking)
    if policy is None or lead is None:
        return {"cancelled": 0}
    n = 0
    for r in (db.query(AppointmentNotification).filter(AppointmentNotification.booking_id == booking.id,
                                                       AppointmentNotification.status.in_(OPEN)).all()):
        r.status, r.reason = "cancelled", "appointment cancelled"
        n += 1
    n += _cancel_survey(db, booking)
    made = 0
    if acknowledge and booking.booked_time is not None:
        for ch in channels(db, lead):
            if _add(db, booking, lead, "cancelled", ch, _version(booking), now):
                made += 1
    db.flush()
    return {"cancelled": n, "acknowledgements": made}


def _cancel_survey(db: Session, booking) -> int:
    n = 0
    for r in (db.query(AppointmentNotification).filter(AppointmentNotification.booking_id == booking.id,
                                                       AppointmentNotification.purpose == "survey",
                                                       AppointmentNotification.status.in_(OPEN)).all()):
        r.status, r.reason = "superseded", "the appointment changed"
        n += 1
    return n


def schedule_survey(db: Session, booking, *, now: Optional[datetime] = None) -> Dict:
    """Called when staff mark a visit COMPLETED. One survey per appointment;
    both channels open the same survey."""
    now = now or datetime.utcnow()
    policy, lead = _policy_for_booking(db, booking)
    if policy is None or lead is None:
        return {"scheduled": 0, "reason": "no program appointment rules"}
    s = settings(policy)
    if (booking.status or "") != "completed":
        return {"scheduled": 0, "reason": "only completed visits are surveyed"}
    since = s.get("active_since")
    if since:
        try:
            cut = datetime.fromisoformat(str(since).replace("Z", ""))
        except ValueError:
            cut = None
        created = getattr(booking, "created_at", None)
        if cut is not None and (_start_utc(booking) < cut or (created is not None and created < cut)):
            return {"scheduled": 0, "reason": "appointment predates appointment messages"}
    mins = int(getattr(booking, "appt_duration", None) or 45)
    anchor = booking.completed_at or (_start_utc(booking) + timedelta(minutes=mins))
    due = anchor + timedelta(hours=int(s["survey_delay_hours"]))
    due = next_permitted(max(due, now), s)
    sv = db.query(AppointmentSurvey).filter(AppointmentSurvey.booking_id == booking.id).first()
    if sv is None:
        sv = AppointmentSurvey(organization_id=lead.organization_id, booking_id=booking.id, lead_id=lead.id,
                               token=secrets.token_urlsafe(24),
                               expires_at=due + timedelta(days=int(s["survey_expiry_days"])))
        try:
            with db.begin_nested():
                db.add(sv)
                db.flush()
        except IntegrityError:
            sv = db.query(AppointmentSurvey).filter(AppointmentSurvey.booking_id == booking.id).first()
    made = 0
    for ch in channels(db, lead):
        if _add(db, booking, lead, "survey", ch, _version(booking), due):
            made += 1
    db.flush()
    return {"scheduled": made, "due_at": due.isoformat() + "Z", "survey_id": sv.id}


# ── the words ───────────────────────────────────────────────────────────────

def _fields(db: Session, booking, lead, policy) -> Dict:
    from app.models.models import User
    from app.services.programs import booking_policy as BP
    adv = db.query(User).filter(User.id == booking.user_id).first()
    who = (adv.full_name if adv and adv.full_name else policy.get("host_name")) or "your advisor"
    bt = booking.booked_time.replace(tzinfo=None) if getattr(booking.booked_time, "tzinfo", None) else booking.booked_time
    home = getattr(booking, "meeting_type", None) == BP.HOME
    addr = (booking.visit_address or "").strip()
    office = (BP.option(policy, BP.OFFICE) or {}).get("address") or ""
    phone = _office_phone(db, lead)
    return {"first_name": (lead.first_name or "").strip().title() or "there", "advisor": who,
            "advisor_first": who.split(" ")[0], "date": bt.strftime("%A, %B %-d"),
            "time": bt.strftime("%-I:%M %p").replace(":00 ", " "), "minutes": int(booking.appt_duration or 45),
            "home": home, "address": addr if home else (addr or office), "office_phone": phone,
            "title": policy.get("label") or "appointment"}


def _office_phone(db, lead) -> str:
    from app.services.programs import identity
    prof = identity.location_profile_for_lead(db, lead)
    raw = getattr(prof, "facility_phone", None) or ""
    d = "".join(c for c in raw if c.isdigit())[-10:]
    return "(%s) %s-%s" % (d[:3], d[3:6], d[6:]) if len(d) == 10 else ""


def _where(f: Dict) -> str:
    if f["home"]:
        return "%s will come to you at %s" % (f["advisor_first"], f["address"])
    return "at Restland, %s - ask for %s" % (f["address"], f["advisor"])


def _change_line(f: Dict) -> str:
    return ("Need to change or cancel? Just reply to this message%s." %
            (" or call %s" % f["office_phone"] if f["office_phone"] else ""))


def compose(db: Session, row: AppointmentNotification, booking, lead, policy) -> Dict:
    """Subject (email) and body for one message. No file details, ever."""
    f = _fields(db, booking, lead, policy)
    when = "%s at %s Central (%d minutes)" % (f["date"], f["time"], f["minutes"])
    p = row.purpose
    if p == "confirmation":
        text = ("Hi %s, your %s with %s is confirmed for %s, %s. %s"
                % (f["first_name"], f["title"], f["advisor"], when, _where(f), _change_line(f)))
        subj = "Your %s is confirmed: %s at %s" % (f["title"], f["date"], f["time"])
    elif p == "updated":
        text = ("Hi %s, your %s with %s has been moved to %s, %s. %s"
                % (f["first_name"], f["title"], f["advisor"], when, _where(f), _change_line(f)))
        subj = "Updated: your %s is now %s at %s" % (f["title"], f["date"], f["time"])
    elif p == "cancelled":
        text = ("Hi %s, your %s on %s at %s Central has been cancelled. If you'd like a new time, just reply "
                "here and %s will set one up." % (f["first_name"], f["title"], f["date"], f["time"], f["advisor_first"]))
        subj = "Your %s on %s is cancelled" % (f["title"], f["date"])
    elif p.startswith("reminder"):
        lead_in = "tomorrow" if p == "reminder_24h" else "coming up"
        text = ("Hi %s, a reminder that your %s with %s is %s: %s, %s. %s"
                % (f["first_name"], f["title"], f["advisor"], lead_in, when, _where(f), _change_line(f)))
        subj = "Reminder: your %s on %s at %s" % (f["title"], f["date"], f["time"])
    elif p == "survey":
        sv = db.query(AppointmentSurvey).filter(AppointmentSurvey.booking_id == booking.id).first()
        link = survey_url(db, lead.organization_id, sv.token) if sv else ""
        text = ("Hi %s, thank you for meeting with %s at Restland for your %s. Would you share how it went? "
                "It takes about a minute: %s" % (f["first_name"], f["advisor"], f["title"], link))
        subj = "Thank you for meeting with %s" % f["advisor"]
    else:
        raise ValueError("unknown purpose %s" % p)
    if row.channel == "sms":
        return {"body": text}
    sig = "\n\n%s\nRestland Cemetery and Funeral Home\n13005 Greenville Ave, Dallas, TX 75243" % f["advisor"]
    return {"subject": subj, "body": text + sig}


def survey_url(db: Session, organization_id: str, token: str) -> str:
    from app.services.public_identity import booking_url
    b = booking_url(db, organization_id, "x")          # the branded public host
    base = b[:-len("/book/x")] if b.endswith("/book/x") else ""
    return "%s/visit-survey/%s" % (base, token) if base else ""


# ── dispatch ────────────────────────────────────────────────────────────────

def _still_valid(db: Session, row: AppointmentNotification, booking, lead, s: Dict, now: datetime) -> Optional[str]:
    """Why this message must NOT go now (checked immediately before sending)."""
    if booking is None or lead is None:
        return "appointment no longer exists"
    if row.purpose != "cancelled" and booking.booked_time is not None and _version(booking) != row.version:
        return "the appointment moved"
    if row.purpose in ("confirmation", "updated") or row.purpose.startswith("reminder"):
        if (booking.status or "") not in LIVE:
            return "appointment is %s" % booking.status
        if row.purpose.startswith("reminder") and _start_utc(booking) <= now:
            return "the visit has started"
    if row.purpose == "cancelled" and (booking.status or "") != "cancelled":
        return "appointment is no longer cancelled"
    if row.purpose == "survey":
        if not s["survey_enabled"]:
            return "survey invitations are off"
        if (booking.status or "") != "completed":
            return "visit is not marked completed"
    if row.channel not in channels(db, lead):
        return "this channel is opted out or unusable"
    kind = "reminder" if row.purpose.startswith("reminder") else row.purpose
    if row.due_at is not None and now - row.due_at > STALE[kind]:
        return "missed its time while sending was off - not sent late"
    return None


def _claim(db: Session, row: AppointmentNotification, now: datetime) -> bool:
    res = db.execute(update(AppointmentNotification)
                     .where(AppointmentNotification.id == row.id, AppointmentNotification.status.in_(OPEN))
                     .values(status="claimed", attempted_at=now, attempts=AppointmentNotification.attempts + 1)
                     .execution_options(synchronize_session=False))
    db.commit()
    return res.rowcount == 1


def _send(db: Session, row, booking, lead, msg: Dict):
    from app.models.models import User
    from app.services import send_source as _ss
    adv = db.query(User).filter(User.id == booking.user_id).first()
    if row.channel == "sms":
        from app.services.sms_service import send_sms
        m = send_sms(db, adv, lead, msg["body"], include_booking_link=False, send_source=_ss.APPOINTMENT_FOLLOWUP)
        return getattr(m, "id", None), getattr(m, "twilio_sid", None)
    from app.services.email_service import send_email_to_lead
    from app.services.programs.email_touches import text_to_html
    e = send_email_to_lead(db, adv, lead, subject=msg["subject"], body_html=text_to_html(msg["body"]),
                           send_source=_ss.APPOINTMENT_FOLLOWUP, raise_on_provider_failure=True)
    return getattr(e, "id", None), getattr(e, "provider_message_id", None)


def run(db: Session, organization_id: Optional[str] = None, *, now: Optional[datetime] = None,
        booking_id: Optional[str] = None, sender=None) -> Dict:
    """One pass over due messages for programs that switched appointment
    messages ON. Returns counts. `sender` is injectable for tests."""
    from app.models.models import BookingLink, Lead
    from app.models.program_models import OutreachProgram
    from app.services.programs import booking_policy as BP
    now = now or datetime.utcnow()
    sender = sender or _send
    out = {"sent": 0, "failed": 0, "skipped": 0, "retry": 0, "held_off": 0}
    progs = db.query(OutreachProgram).filter(OutreachProgram.booking_policy_json.isnot(None))
    if organization_id:
        progs = progs.filter(OutreachProgram.organization_id == organization_id)
    for prog in progs.all():
        policy = BP.for_program(prog)
        s = settings(policy)
        q = (db.query(AppointmentNotification)
             .filter(AppointmentNotification.organization_id == prog.organization_id,
                     AppointmentNotification.status.in_(OPEN), AppointmentNotification.due_at <= now))
        if booking_id:
            q = q.filter(AppointmentNotification.booking_id == booking_id)
        rows = q.order_by(AppointmentNotification.due_at).limit(200).all()
        if not s["enabled"]:
            out["held_off"] += len(rows)          # left exactly as they are: sending is off
            continue
        for row in rows:
            if row.status == "retry" and row.next_attempt_at and row.next_attempt_at > now:
                continue
            booking = db.query(BookingLink).filter(BookingLink.id == row.booking_id).first()
            lead = db.query(Lead).filter(Lead.id == row.lead_id, Lead.organization_id == prog.organization_id).first()
            why = _still_valid(db, row, booking, lead, s, now)
            if why:
                row.status, row.reason = "skipped", why
                db.commit()
                out["skipped"] += 1
                continue
            if not _claim(db, row, now):
                continue                           # another worker has it
            db.refresh(row)
            try:
                msg = compose(db, row, booking, lead, policy)
                mid, pid = sender(db, row, booking, lead, msg)
                row.status, row.sent_at, row.message_id, row.provider_id, row.error = "sent", now, mid, pid, None
                out["sent"] += 1
            except (ValueError, PermissionError) as e:      # a refusal: opt-out, texts off, gate
                db.rollback()
                row = db.query(AppointmentNotification).filter_by(id=row.id).first()
                row.status, row.reason = "skipped", ("refused: %s" % e)[:500]
                out["skipped"] += 1
            except Exception as e:                           # noqa: BLE001 - provider trouble
                db.rollback()
                row = db.query(AppointmentNotification).filter_by(id=row.id).first()
                k = int(row.attempts or 1)
                row.error = str(e)[:500]
                if k <= len(RETRY_STEPS):
                    row.status, row.next_attempt_at = "retry", now + RETRY_STEPS[k - 1]
                    out["retry"] += 1
                else:
                    row.status = "failed"
                    out["failed"] += 1
            db.commit()
    return out


def delivery_state(db: Session, row: AppointmentNotification) -> Optional[str]:
    """The provider's own receipt, read live (late callbacks just show up)."""
    if not row.message_id:
        return None
    from app.models.models import EmailMessage, Message
    if row.channel == "sms":
        m = db.query(Message).filter(Message.id == row.message_id).first()
        return getattr(m, "delivery_status", None)
    e = db.query(EmailMessage).filter(EmailMessage.id == row.message_id).first()
    return getattr(e, "status", None)


def view(db: Session, row: AppointmentNotification) -> Dict:
    iso = lambda d: d.isoformat() + "Z" if d else None   # noqa: E731
    return {"id": row.id, "purpose": row.purpose, "channel": row.channel, "status": row.status,
            "due_at": iso(row.due_at), "sent_at": iso(row.sent_at), "reason": row.reason, "error": row.error,
            "attempts": row.attempts, "delivery": delivery_state(db, row)}
