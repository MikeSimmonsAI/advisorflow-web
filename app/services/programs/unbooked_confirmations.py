"""TOLD A TIME, NOTHING BOOKED (owner, Nov 16).

A person texted a family "we will see you Thursday at 2:30" by hand and never
pressed Schedule: the family thought they were booked, but no appointment
existed - no calendar event, no reminders, no booked alert.

Every pass (the program loop, every 5 minutes) finds a staff-sent text or
email from the last week that confirms a day or time with a family who has no
upcoming booking, and pages the whole team ONCE for that message with a link
straight to the family. Read-only on the family: nothing is ever sent to them
from here. Booking the family through Schedule clears it.
"""
import html as _html
import logging
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

LOOKBACK = timedelta(days=7)
GRACE = timedelta(minutes=5)         # time to press Schedule after texting

# "we will see you", "you're all set", "you are confirmed", "booked you",
# "have you down for", "your appointment is", "look forward to seeing you"
_CONFIRM = re.compile(
    r"\b(see you|you.?re (all )?set|you are (all )?set|(is|are|you.?re|you are|been) (confirmed|booked|scheduled)"
    r"|(booked|scheduled|confirmed|put|got|have) you (in|down|for|on)|appointment (is|for|on)"
    r"|look(ing)? forward to (seeing|meeting) you|we.?ll be expecting you|expect(ing)? you)\b", re.I)
_WHEN = re.compile(
    r"\b(mon|tues?|wed(nes)?|thu(rs)?|fri|sat(ur)?|sun)(day)?\b|\btoday\b|\btomorrow\b|\btonight\b"
    r"|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.? \d{1,2}\b|\b\d{1,2}/\d{1,2}\b"
    r"|\b\d{1,2}(:\d{2})? ?(am|pm|a\.m\.?|p\.m\.?)\b|\bnoon\b", re.I)
_TAGS = re.compile(r"<[^>]+>")
_SIGN = re.compile(r"\s+-\s+[^-]{2,80}Reply STOP to opt out\.?\s*$|\s*Reply STOP to opt out\.?\s*$", re.I)


def confirms_a_time(text: Optional[str]) -> bool:
    """A staff message that tells the family they are coming in at a time."""
    t = _TAGS.sub(" ", text or "")
    return bool(_CONFIRM.search(t) and _WHEN.search(t))


def _clean(text: str) -> str:
    t = _SIGN.sub("", _TAGS.sub(" ", text or ""))
    return re.sub(r"\s+", " ", t).strip()


def _booked(db: Session, lead_id: str, now: datetime) -> bool:
    from app.models.models import BookingLink
    return db.query(BookingLink.id).filter(
        BookingLink.lead_id == lead_id, BookingLink.status.in_(("booked", "confirmed")),
        BookingLink.booked_time >= now - timedelta(days=1)).first() is not None


def find(db: Session, org_id: str, now: Optional[datetime] = None) -> List[Dict]:
    """Families told a time by a person, with nothing booked. Newest message
    per family; the message id is the alert's key."""
    from app.models.models import EmailMessage, Lead, Message
    from app.services import send_source as ss
    now = now or datetime.utcnow()
    since, until = now - LOOKBACK, now - GRACE
    rows = []
    for (mid, lead_id, body, at) in (db.query(Message.id, Message.lead_id, Message.body, Message.sent_at)
                                     .join(Lead, Lead.id == Message.lead_id)
                                     .filter(Lead.organization_id == org_id, Message.send_source == ss.MANUAL,
                                             Message.sent_at >= since, Message.sent_at <= until).all()):
        rows.append((mid, lead_id, body, at, "text"))
    for (mid, lead_id, body, at) in (db.query(EmailMessage.id, EmailMessage.lead_id, EmailMessage.body_html,
                                              EmailMessage.sent_at)
                                     .join(Lead, Lead.id == EmailMessage.lead_id)
                                     .filter(Lead.organization_id == org_id, EmailMessage.send_source == ss.MANUAL,
                                             EmailMessage.sent_at >= since, EmailMessage.sent_at <= until).all()):
        rows.append((mid, lead_id, body, at, "email"))
    newest: Dict[str, tuple] = {}
    for r in rows:
        if confirms_a_time(r[2]) and (r[1] not in newest or (r[3] or since) > (newest[r[1]][3] or since)):
            newest[r[1]] = r
    out = []
    leads = {l.id: l for l in db.query(Lead).filter(Lead.id.in_(list(newest))).all()} if newest else {}
    for lead_id, (mid, _, body, at, how) in newest.items():
        lead = leads.get(lead_id)
        if lead is None or getattr(lead, "is_test", False) or (lead.status or "") == "dnc":
            continue
        if _booked(db, lead_id, now):
            continue
        name = ("%s %s" % ((lead.first_name or "").title(), (lead.last_name or "").title())).strip()
        out.append({"lead_id": lead_id, "message_id": mid, "name": name or "A family", "said": _clean(body),
                    "at": at, "how": how})
    return out


def alert_all(db: Session, now: Optional[datetime] = None) -> int:
    """Page the team once per confirming message. Returns pages raised."""
    from app.models.program_models import OutreachProgram, ProgramAlert
    from app.services.programs import alert_ledger as L
    from app.services.programs import responses as R
    now = now or datetime.utcnow()
    raised = 0
    for prog in db.query(OutreachProgram).filter(OutreachProgram.is_active.is_(True)).all():
        for fam in find(db, prog.organization_id, now):
            key = "unbooked:%s" % fam["message_id"]
            if db.query(ProgramAlert.id).filter(ProgramAlert.dedupe_key == key).first() is not None:
                continue
            sms = L.sms_line("unbooked", fam["name"],
                             detail="was told a time by %s but nothing is booked - press Schedule to book it"
                                    % fam["how"],
                             link=L.family_link(fam["lead_id"]), program=L.program_label(db, prog))
            subject = "%s - NOT BOOKED: %s was told a time, nothing is on the calendar" % (
                R.alert_title(prog), fam["name"])
            body = ("<p><b>%s</b> was sent this by %s:</p><p>&ldquo;%s&rdquo;</p>"
                    "<p>Nothing is booked for them: no calendar event, no reminders. Open the family and "
                    "press <b>Schedule</b> to book that time so it goes on the calendar and they get "
                    "their confirmation and reminders.</p>"
                    % (_html.escape(fam["name"]), fam["how"], _html.escape(fam["said"][:400])))
            try:
                L.page(db, prog, lead_id=fam["lead_id"], key=key, kind="unbooked", sms_text=sms,
                       subject=subject, html=body, management=True, coalesce=False,
                       audit_message="%s was told a time by %s; nothing booked" % (fam["name"], fam["how"]))
                db.commit()
                raised += 1
            except Exception:                            # noqa: BLE001
                db.rollback()
                log.exception("unbooked-confirmation alert failed for lead %s", fam["lead_id"])
    return raised
