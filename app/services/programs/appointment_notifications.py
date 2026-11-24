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
    skipped, never sent late in a burst. COLLISIONS: a reminder within 12
    hours of another REMINDER for the same appointment is skipped unless it is
    the final reminder going at its true time (see MIN_GAP); the confirmation
    only cancels a reminder landing within 2 hours of it. MORNING OF (owner,
    Nov 24): a visit before 1 PM whose last reminder would fall before 9 AM
    gets it at 8:30 AM that morning ("see you today"). The reminder text says
    "today" / "tomorrow" from the moment it actually goes.
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
import re
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
    confirm_at = now if (chans and not skip_immediate) else None
    for key, due, status, reason in plan_reminders(_start_utc(booking), s, now, confirm_at):
        for ch in chans:
            if _add(db, booking, lead, key, ch, version, due, status, reason):
                made += 1
    db.flush()
    return {"scheduled": made, "superseded": superseded, "channels": chans, "enabled": s["enabled"]}



# ── Reminder collision rule (deterministic) ─────────────────────────────────
# Reminders are written longest offset first. A reminder is SKIPPED when it
# would go within MIN_GAP of a message already planned for the same
# appointment (the confirmation, or an earlier reminder), unless it is the
# final reminder AND goes at its true time - the "shortly before" reminder is
# the one worth keeping when it is genuinely shortly before. So:
#   * 10:00 AM visit: the 2-hour reminder would be 8:00 AM (before 9 AM), so it
#     would move to 5:45 PM the evening before - 7 h 45 m after the 24-hour
#     reminder. It is skipped; the family gets the 24-hour one only.
#   * booked 26 hours ahead: the 24-hour reminder would follow the confirmation
#     by 2 hours, so it is skipped; the 2-hour reminder still goes.
MIN_GAP = timedelta(hours=12)
# Owner, Nov 24: the confirmation never cancels a reminder unless they would
# land almost together, and a morning visit gets a "see you today" text at
# 8:30 AM on the day (the 2-hour reminder would be before 9 AM; it used to
# move to 5:45 PM the evening before and then be dropped).
CONFIRM_GAP = timedelta(hours=2)
MORNING_OF = (8, 30)
MORNING_OF_BEFORE = (13, 0)        # visits that start before 1 PM


def _morning_of(start: datetime, ideal: datetime, s: Dict, now: datetime) -> Optional[datetime]:
    """8:30 AM Central on the visit day, for a visit before 1 PM whose reminder
    would fall before the notification window that morning."""
    st, idl = _local(start), _local(ideal)
    lo, _hi = _window(s)
    if st.time() >= time(*MORNING_OF_BEFORE) or idl.date() != st.date() or idl.time() >= lo:
        return None
    m = _utc(datetime.combine(st.date(), time(*MORNING_OF)))
    return m if now < m < start else None


def plan_reminders(start: datetime, s: Dict, now: datetime, confirm_at: Optional[datetime] = None):
    """[(key, due_utc, status, reason)] for every reminder, longest first."""
    out = []
    anchors = []                     # reminders already planned (12-hour rule)
    last = min(s["reminder_minutes"]) if s["reminder_minutes"] else None
    for mins in s["reminder_minutes"]:            # longest offset first (settings sorts it)
        key = "reminder_24h" if mins == 1440 else "reminder_2h" if mins == 120 else "reminder_%dm" % mins
        ideal = start - timedelta(minutes=mins)
        due, status, reason, morning = ideal, "scheduled", None, False
        if due <= now:
            status, reason = "skipped", "booked inside this reminder's interval - not sent late"
        else:
            adj = preceding_permitted(due, s)
            if adj != due:
                m = _morning_of(start, ideal, s, now) if mins == last else None
                if m is not None:
                    due, morning = m, True
                    reason = "sent at 8:30 AM Central the morning of the visit (the %s reminder would be %s, " \
                             "before 9 AM)" % (_gap_text(timedelta(minutes=mins)), _local(ideal).strftime("%-I:%M %p"))
                elif adj <= now or adj >= start:
                    status, reason = "skipped", "outside the notification window and no earlier permitted time"
                else:
                    due = adj
                    reason = moved_reason(ideal, adj, start, mins)
            if status == "scheduled":
                clash = _collision_reason(due, ideal, anchors, mins == last, exempt=(mins == last and morning))
                if clash is None and confirm_at is not None and abs(due - confirm_at) < CONFIRM_GAP \
                        and not (mins == last and (due == ideal or morning)):
                    clash = ("not sent: it would go at %s Central, only %s after the booking confirmation"
                             % (_local(due).strftime("%-I:%M %p %a"), _gap_text(abs(due - confirm_at))))
                if clash:
                    status, reason = "skipped", clash
                else:
                    anchors.append(due)
        out.append((key, due, status, reason))
    return out


def replan_reminders(db: Session, organization_id: str, now: Optional[datetime] = None) -> Dict:
    """Re-plan the reminders of every upcoming appointment under today's rules
    (owner, Nov 24). Only reminders that have NOT gone and were scheduled or
    skipped by the old timing rules change; nothing is sent here."""
    from app.models.models import BookingLink, Lead
    now = now or datetime.utcnow()
    changed = []
    rows = (db.query(AppointmentNotification)
            .filter(AppointmentNotification.organization_id == organization_id,
                    AppointmentNotification.purpose.like("reminder%"),
                    AppointmentNotification.status.in_(("scheduled", "skipped"))).all())
    by_booking: Dict[str, list] = {}
    for r in rows:
        by_booking.setdefault(r.booking_id, []).append(r)
    for bid, rs in by_booking.items():
        b = db.query(BookingLink).filter(BookingLink.id == bid).first()
        if b is None or (b.status or "") not in LIVE or b.booked_time is None:
            continue
        start = _start_utc(b)
        if start <= now:
            continue
        policy, lead = _policy_for_booking(db, b)
        if policy is None:
            continue
        s = settings(policy)
        version = _version(b)
        conf = (db.query(AppointmentNotification.created_at)
                .filter(AppointmentNotification.booking_id == bid,
                        AppointmentNotification.purpose.in_(("confirmation", "updated")),
                        AppointmentNotification.version == version).first())
        plan = {k: (d, st, why) for k, d, st, why in plan_reminders(start, s, now, conf[0] if conf else None)}
        for r in rs:
            if r.version != version or r.purpose not in plan:
                continue
            old_reason = r.reason or ""
            if r.status == "skipped" and not (old_reason.startswith("not sent:")
                                              or old_reason.startswith("outside the notification window")):
                continue                               # opted out, held, booked too late: unchanged
            d, st, why = plan[r.purpose]
            if (r.status, r.due_at, old_reason) != (st, d, why or "") and (st == "scheduled" or r.status == "scheduled"):
                changed.append({"id": r.id, "booking_id": bid, "purpose": r.purpose, "channel": r.channel,
                                "before": {"status": r.status, "due_at": r.due_at.isoformat() if r.due_at else None},
                                "after": {"status": st, "due_at": d.isoformat()}})
                r.status, r.due_at, r.reason = st, d, why
    db.commit()
    return {"changed": changed}


def _gap_text(d: timedelta) -> str:
    m = int(d.total_seconds() // 60)
    return ("%d h %02d m" % (m // 60, m % 60)) if m >= 60 else ("%d minutes" % m)


def moved_reason(ideal: datetime, adj: datetime, start: datetime, mins: int) -> str:
    before = start - adj
    return ("sent at %s Central, %s before the visit, instead of %s before (%s Central is outside the "
            "%s notification window)" % (_local(adj).strftime("%-I:%M %p %a"), _gap_text(before),
                                          _gap_text(timedelta(minutes=mins)),
                                          _local(ideal).strftime("%-I:%M %p"), "9 AM-6 PM"))


def _collision_reason(due: datetime, ideal: datetime, anchors, is_last: bool, exempt: bool = False) -> Optional[str]:
    near = [a for a in anchors if abs(due - a) < MIN_GAP]
    if not near or exempt or (is_last and due == ideal):
        return None
    gap = min(abs(due - a) for a in near)
    moved = "" if due == ideal else (" (moved from %s, which is outside 9 AM-6 PM)" % _local(ideal).strftime("%-I:%M %p"))
    return ("not sent: it would go at %s Central%s, only %s from another message for this appointment"
            % (_local(due).strftime("%-I:%M %p %a"), moved, _gap_text(gap)))


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
    mt = getattr(booking, "meeting_type", None)
    remote = mt in BP.CALLS
    addr = (booking.visit_address or "").strip()
    office = (BP.option(policy, BP.OFFICE) or {}).get("address") or ""
    phone = _office_phone(db, lead)
    return {"first_name": (lead.first_name or "").strip().title() or "there", "advisor": who,
            "advisor_first": who.split(" ")[0], "date": bt.strftime("%A, %B %-d"),
            "time": bt.strftime("%-I:%M %p").replace(":00 ", " "), "minutes": int(booking.appt_duration or 45),
            "home": home, "remote": remote, "call": mt if mt in BP.CALLS else None,
            "family_phone": _pretty_phone(getattr(lead, "phone", None)),
            "address": addr if home else (addr or office), "office_phone": phone,
            "title": policy.get("label") or "appointment"}


def _office_phone(db, lead) -> str:
    from app.services.programs import identity
    prof = identity.location_profile_for_lead(db, lead)
    raw = getattr(prof, "facility_phone", None) or ""
    d = "".join(c for c in raw if c.isdigit())[-10:]
    return "(%s) %s-%s" % (d[:3], d[3:6], d[6:]) if len(d) == 10 else ""


def _lead_in(sent: Optional[datetime], start: datetime) -> str:
    """Said from the moment the reminder actually goes, not from its label."""
    if sent is None:
        return "coming up"
    d = (_local(start).date() - _local(sent).date()).days
    return "today" if d == 0 else "tomorrow" if d == 1 else "coming up"


def _pretty_phone(raw) -> str:
    d = re.sub(r"\D", "", str(raw or ""))
    d = d[1:] if len(d) == 11 and d.startswith("1") else d
    return "(%s) %s-%s" % (d[:3], d[3:6], d[6:]) if len(d) == 10 else ""


def _where(f: Dict) -> str:
    if f.get("call") == "phone":
        return "by phone - %s will call you%s" % (f["advisor_first"],
                                                  (" at %s" % f["family_phone"]) if f.get("family_phone") else "")
    if f.get("call") == "video":
        return "by video call - %s will send you the video call link" % f["advisor_first"]
    if f.get("remote"):
        return "by phone or video call - %s will send you the call details" % f["advisor_first"]
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
        lead_in = _lead_in(row.due_at, _start_utc(booking))
        text = ("Hi %s, a reminder that your %s with %s is %s: %s, %s. %s"
                % (f["first_name"], f["title"], f["advisor"], lead_in, when, _where(f), _change_line(f)))
        subj = "Reminder: your %s on %s at %s" % (f["title"], f["date"], f["time"])
    elif p == "survey":
        sv = db.query(AppointmentSurvey).filter(AppointmentSurvey.booking_id == booking.id).first()
        link = survey_url(db, lead.organization_id, sv.token) if sv else ""
        # "at Restland" only when the visit was there - a home visit was not.
        text = ("Hi %s, thank you for meeting with %s%s for your %s. Would you share how it went? "
                "It takes about a minute: %s" % (f["first_name"], f["advisor"], "" if (f["home"] or f.get("remote")) else " at Restland",
                                                f["title"], link))
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
        from app.services.programs import outreach_slots as _os
        from app.services import no_contact_days as _ncd
        closed_today = _os.closed_for_org(db, prog.organization_id, now)
        holding = _ncd.holding_reminders(now)
        for row in rows:
            if holding:
                # No-contact day (Thanksgiving): nothing goes out. A reminder
                # whose moment passes during the day is skipped, not sent late;
                # anything else waits until 8:30 AM the next morning.
                if row.purpose.startswith("reminder") and _ncd.quiet_now(now):
                    row.status, row.reason = "skipped", "no-contact day (no messages sent)"
                    db.commit()
                    out["skipped"] += 1
                continue
            if closed_today and row.purpose == "survey":
                continue                           # closed day (Sundays): the survey waits for Monday
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
