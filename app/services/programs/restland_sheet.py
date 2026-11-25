"""RESTLAND HANDOFF SHEET (owner, Nov 22 2026).

"When somebody books an appointment ... that appointment for that particular
person needs to go on a spreadsheet for what date and time ... when an
appointment's there or it needs a follow up for an appointment, we need to put
it on a spreadsheet."

The system is the source of truth; the Google Sheet is the team's handoff
board. Two tabs are written by the system, every few minutes:

  Appointments   one row per booked appointment (BookingLink id)
  Follow-Ups     one row per family who needs a person or a later follow-up:
                 asked to follow up later ("after the holidays"), asked for a
                 call back, needs a person, or was told a time by staff with
                 nothing on the calendar yet.

THE SYSTEM WRITES ONLY ITS OWN COLUMNS (A to the last system column). The
columns to the right - Assigned To, Confirmed?, call link, Outcome, Notes -
belong to the team and are never touched, so what a person types stays.
Rows are found by the id in column A (sorting or filtering the sheet is
fine) and are never deleted: a row's System Status changes instead.

Same Google service account and safety rules as the VOLT sheets
(volt/sheets_export): values written RAW, text that could be read as a
formula prefixed with an apostrophe, dates as real Central date numbers,
errors reduced to a category. The spreadsheet id lives in the Restland
program's settings (booking_policy_json "handoff_sheet": {"id": ...}).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

APPOINTMENTS, FOLLOW_UPS = "Appointments", "Follow-Ups"
# Owner, Nov 22 8:45 PM: no "Family File" column - no links into the app.
APPT_COLUMNS = ("Appointment ID", "Booked On (CT)", "Date", "Time (CT)", "Day", "Family", "Phone", "Email",
                "How", "Home Address", "System Status", "What They Said Last")
FU_COLUMNS = ("Follow-Up ID", "Added (CT)", "Family", "Phone", "Email", "Why", "Follow Up On",
              "What They Said", "System Status")
COLUMNS = {APPOINTMENTS: APPT_COLUMNS, FOLLOW_UPS: FU_COLUMNS}
# The team's own columns, to the right of the system's (written once, as the
# header, by the sheet's setup - never by the sync).
STAFF_COLUMNS = {
    # Owner, Nov 23 4:41 PM: no "Confirmed With Family?" column.
    # Owner, Nov 23 4:55 PM: "a sold column ... and a note saying what was sold".
    APPOINTMENTS: ("Assigned To", "Remote Call Link / Details", "Outcome", "Team Notes", "Sold?", "What Was Sold"),
    FOLLOW_UPS: ("Assigned To", "Done?", "Team Notes", "Appointment Date", "Appointment Time", "How",
                 "Home Address"),
}
# Written by the system to the right of the team's Follow-Ups columns: what
# happened when the team filled in an appointment (owner, Nov 22 9:16 PM).
FU_RESULT_HEADER = "Booking Result"
# Written by the system on both tabs: when the team last changed the row
# (owner, Nov 23: "I need to know when it was updated or when the comments
# were made").
LAST_UPDATE_HEADER = "Last Update (CT)"
SOLD_CHOICES = ("Yes", "No")
# The team's follow-up log, to the right of the Booking Result (owner, Nov 23:
# "annotate what we followed up, what date do we get an appointment"). Read
# back like the other team columns; each change goes into the family's notes.
FU_WORK_COLUMNS = ("Followed Up On", "What Happened")
FU_WORK_FIRST = len(FU_COLUMNS) + len(STAFF_COLUMNS[FOLLOW_UPS]) + 1      # R (0-based 17)
WHAT_HAPPENED = ("Talked - booked appointment", "Talked - call back later", "Left voicemail", "Sent a text",
                 "No answer", "Not interested", "Already done - nothing needed", "Wrong number")
_BOOKED_RESULT = WHAT_HAPPENED[0]
START_TIMES = ("10:00 AM", "11:30 AM", "1:00 PM", "2:30 PM", "4:00 PM")
# The time picker on the sheet: every half hour from the first to the last
# start (owner, Nov 23 - any time the family asks for). Any other time can be
# typed in; the booking checks the calendar either way.
# Owner, Nov 24: Restland's hours run to 6 PM on weekdays (3 PM weekends -
# the booking itself checks the day's hours).
TIME_CHOICES = tuple(
    "%d:%02d %s" % ((h - 1) % 12 + 1, m, "AM" if h < 12 else "PM")
    for h in range(10, 19) for m in (0, 30) if (h, m) <= (18, 0))
# Owner, Nov 24: a phone call and a video call are separate choices.
HOW_CHOICES = {"At Restland": ("office", "visit"), "At Their Home": ("home", "visit"),
               "Phone Call": ("phone", "phone"), "Video Call": ("video", "phone"),
               "Phone / Video Call": ("remote", "phone")}
HOW = {"office": "At Restland", "home": "At Their Home", "phone": "Phone Call", "video": "Video Call",
       "remote": "Phone / Video Call"}
# what the drop-downs offer (the old combined choice only reads back)
HOW_PICK = ["At Restland", "At Their Home", "Phone Call", "Video Call"]
APPT_STATUS = {"booked": "Booked", "confirmed": "Confirmed", "completed": "Showed", "no_show": "No-Show",
               "cancelled": "Cancelled"}
OPEN_STATES = {"waiting_future": "Follow up later", "callback_requested": "Call back",
               "human": "Needs a person"}
DONE_STATES = {"booked": "Booked", "stopped": "Closed", "closed": "Closed"}
# Owner, Nov 23 2:33 PM: the sheet must follow a booking promptly. A change in
# the app (a booking, cancellation or move; a family's conversation) is written
# within seconds - at most one write every MIN_GAP; the team's own edits are
# read back at least every EVERY.
EVERY = timedelta(minutes=2)
MIN_GAP = timedelta(seconds=20)
_last_seen: Dict[str, Any] = {}
MAX_ROWS = 20000
_last_run: Dict[str, datetime] = {}


# ── settings ────────────────────────────────────────────────────────────────

# The Google login the server writes with. Owner, Nov 22: the board must not
# run on the VOLT login - EVOSYS_SHEETS_SERVICE_ACCOUNT_JSON (an EvoSys
# service account) is used when it is set; the VOLT one only until then.
CRED_ENV, FALLBACK_ENV = "EVOSYS_SHEETS_SERVICE_ACCOUNT_JSON", "VOLT_SHEETS_SERVICE_ACCOUNT_JSON"


def _cred_env() -> str:
    import os
    return CRED_ENV if (os.getenv(CRED_ENV) or "").strip() else FALLBACK_ENV


def sync_account_email() -> Optional[str]:
    """Who the sheet must be shared with (not a secret)."""
    import os
    try:
        return json.loads(os.getenv(_cred_env()) or "{}").get("client_email")
    except ValueError:
        return None


def api():
    """A Sheets client on the EvoSys login (falls back to the VOLT one)."""
    import os
    from app.services.volt.sheets_export import SCOPES, SheetsError
    raw = (os.getenv(_cred_env()) or "").strip()
    if not raw:
        raise SheetsError("not_configured")
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    try:
        creds = service_account.Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)
    except Exception:                                  # noqa: BLE001 - never echo the credential
        raise SheetsError("credential") from None
    import google_auth_httplib2
    import httplib2
    http = google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http(timeout=30))
    return build("sheets", "v4", http=http, cache_discovery=False)

def sheet_id(prog) -> Optional[str]:
    try:
        cfg = json.loads(getattr(prog, "booking_policy_json", None) or "{}").get("handoff_sheet") or {}
        sid = str(cfg.get("id") or "").strip()
        return sid if re.fullmatch(r"[A-Za-z0-9_-]{20,80}", sid) else None
    except (ValueError, AttributeError):
        return None


def sheet_url(prog) -> Optional[str]:
    sid = sheet_id(prog)
    return "https://docs.google.com/spreadsheets/d/%s/edit" % sid if sid else None


# ── rows ────────────────────────────────────────────────────────────────────

def _skip(lead) -> bool:
    from app.services.sci_staging_demo import is_demo_lead
    from app.services.test_records import is_owner_test_lead
    return lead is None or bool(getattr(lead, "is_test", False)) or is_demo_lead(lead) or is_owner_test_lead(lead)


def _phone(raw) -> str:
    """US numbers as (214) 240-3434; anything else as given (kept as text)."""
    from app.services.volt.sheets_export import safe_phone
    d = re.sub(r"\D", "", str(raw or ""))
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return "(%s) %s-%s" % (d[:3], d[3:6], d[6:]) if len(d) == 10 else safe_phone(raw)


def _name(lead) -> str:
    from app.services.programs.alert_ledger import first_last
    return first_last(lead)


def _local_serial(naive_central: Optional[datetime]):
    """A naive Central wall time (BookingLink.booked_time) -> a date number."""
    if naive_central is None:
        return ""
    d = naive_central.replace(tzinfo=None) - datetime(1899, 12, 30)
    return round(d.days + d.seconds / 86400.0, 8)


def _last_family_words(db, lead_id: str) -> str:
    from app.models.program_models import ProgramConversation, ProgramConversationTurn
    t = (db.query(ProgramConversationTurn.text)
         .join(ProgramConversation, ProgramConversation.id == ProgramConversationTurn.conversation_id)
         .filter(ProgramConversation.lead_id == lead_id, ProgramConversationTurn.role == "family",
                 ProgramConversationTurn.text.isnot(None))
         .order_by(ProgramConversationTurn.created_at.desc()).first())
    return re.sub(r"\s+", " ", (t[0] if t else "") or "").strip()[:300]


def appointment_rows(db: Session, org_id: str) -> List[List[Any]]:
    from app.models.models import BookingLink, Lead
    from app.models.reset_models import AppointmentNotification
    from app.services.volt.sheets_export import central_serial, safe, safe_phone
    out = []
    q = (db.query(BookingLink, Lead).join(Lead, Lead.id == BookingLink.lead_id)
         .filter(Lead.organization_id == org_id, BookingLink.booked_time.isnot(None))
         .order_by(BookingLink.booked_time))
    pairs = q.all()
    try:
        notes = office_notes(db, [lead.id for _, lead in pairs])   # owner, Nov 25: notes reach appointments too
    except Exception:                                    # noqa: BLE001 - the rows still go in
        log.exception("office notes lookup failed")
        notes = {}
    for b, lead in pairs:
        if _skip(lead):
            continue
        first_note = (db.query(AppointmentNotification.created_at)
                      .filter(AppointmentNotification.booking_id == b.id)
                      .order_by(AppointmentNotification.created_at).first())
        booked_on = first_note[0] if first_note else b.created_at
        bt = b.booked_time.replace(tzinfo=None)
        status = APPT_STATUS.get(b.status or "", (b.status or "").title())
        if b.rescheduled_at and status in ("Booked", "Confirmed"):
            status += " (moved)"
        out.append([b.id, central_serial(booked_on), _local_serial(datetime(bt.year, bt.month, bt.day)),
                    round((bt.hour * 60 + bt.minute) / 1440.0, 8), bt.strftime("%A"), safe(_name(lead)),
                    _phone(lead.phone), safe(lead.email), HOW.get(b.meeting_type or "office", "At Restland"),
                    safe(b.visit_address if b.meeting_type == "home" else ""), status,
                    safe(_said_with_notes(_last_family_words(db, lead.id), notes.get(lead.id)))])
    return out


def _plain_why(reason: Optional[str]) -> str:
    """The reason a person is needed, in the team's words - nothing about the
    system or the AI."""
    r = (reason or "").strip()
    r = re.sub(r"\s*Contact them personally\.?\s*$", "", r)
    m = re.match(r"^Needs a human - the AI could not move this to an appointment, so nothing was sent\s*\((.*)\)\.?$",
                 r, re.S)
    if m:
        r = m.group(1).strip()
    r = re.sub(r"\b(the )?AI\b", "we", r)
    return ("Needs a person: " + r) if r else "Needs a person"


def _conv_row(db, conv, lead, ev: Optional[Dict[str, Any]] = None, notes=None) -> List[Any]:
    from app.services.volt.sheets_export import central_serial, safe, safe_phone
    state = conv.state or ""
    if state == "waiting_future":
        why = "Asked us to follow up later" + (" - %s" % conv.followup_reason if conv.followup_reason else "")
    elif state == "callback_requested":
        why = "Asked for a call back" + (" (%s)" % conv.callback_window if conv.callback_window else "")
    else:
        why = _plain_why(conv.handoff_reason)
    status = ("Open" if state in OPEN_STATES else DONE_STATES.get(state, "In progress"))
    # A family whose newest message came after anything we sent is waiting on
    # us - Open, unless the case is done (booked / closed / opted out).
    if (ev and status == "In progress" and ev.get("family_at")
            and (ev.get("out_at") is None or ev["family_at"] > ev["out_at"])):
        status = "Open"
    # The date a follow-up is due: the family's own "later" date, or a date the
    # team set on a needs-a-person follow-up (that one never texts the family).
    fu_on = (central_serial(conv.next_followup_at)
             if conv.next_followup_at and state in ("waiting_future", "human") else "")
    return ["FU-" + conv.id, central_serial(conv.created_at), safe(_name(lead)), _phone(lead.phone),
            safe(lead.email), safe(_with_notes(why, notes)), fu_on, safe(_last_family_words(db, lead.id)), status]


# ── notes a person typed on the family in the app (owner, Nov 25) ───────────
#
# "if they say book me in about two weeks ... I put notes inside ... the
# system, but it doesn't update the notes on the spreadsheet ... the person
# who's working the ticket [needs to understand] that's where it's at."
# The newest notes a PERSON wrote on the family (not the system's own notes)
# go under Why on the follow-up row - so they show on the main tab, the
# person's Follow-Ups tab and the Restland Board - and move Last Update.
NOTE_DAYS = 45
NOTES_SHOWN = 2


def office_notes(db: Session, lead_ids) -> Dict[str, List[tuple]]:
    """{lead id: [(created_at, body), ...]} newest first: notes a person wrote
    in the last NOTE_DAYS days, at most NOTES_SHOWN per family."""
    from app.models.work_models import LeadNote
    ids = [i for i in set(lead_ids) if i]
    out: Dict[str, List[tuple]] = {}
    cutoff = datetime.utcnow() - timedelta(days=NOTE_DAYS)
    for i in range(0, len(ids), 500):
        for lid, at, body in (db.query(LeadNote.lead_id, LeadNote.created_at, LeadNote.body)
                              .filter(LeadNote.lead_id.in_(ids[i:i + 500]), LeadNote.author_user_id.isnot(None),
                                      LeadNote.created_at >= cutoff)
                              .order_by(LeadNote.created_at.desc()).all()):
            if body and body.strip() and len(out.setdefault(lid, [])) < NOTES_SHOWN:
                out[lid].append((at, body.strip()))
    return out


def _note_lines(notes) -> List[str]:
    out = []
    for at, body in notes or []:
        t = _central(at)
        out.append("NOTE %d/%d: %s" % (t.month, t.day, " ".join(body.split())[:300]))
    return out


def _said_with_notes(said: str, notes) -> str:
    """An appointment's last words from the family, under the office's notes."""
    lines = _note_lines(notes)
    if not lines:
        return said or ""
    return ("\n".join(lines) + ("\nSaid: " + said if said else ""))[:900]


def _with_notes(why: str, notes) -> str:
    if not notes:
        return why[:300]
    lines = []
    for at, body in notes:
        t = _central(at)
        lines.append("NOTE %d/%d: %s" % (t.month, t.day, " ".join(body.split())[:300]))
    return (why[:300] + "\n" + "\n".join(lines))[:900]


# ── what happened on a follow-up in the app (owner, Nov 24) ─────────────────
#
# A person's reply sent from the app (Send text, Send & keep human control,
# email) is a follow-up done: it goes in Followed Up On / What Happened. A later
# family message updates What They Said and Last Update. Everything is read
# from the message history on every pass, so a retry or an out-of-order event
# lands on the same answer. Only a reply that went out counts - a failed or
# undelivered send is not a follow-up.
FAILED_SMS = ("failed", "undelivered", "canceled", "cancelled", "rejected")
FAILED_EMAIL = ("failed", "bounced", "rejected", "dropped")
REPLIED_PREFIX = "Replied by "


def followup_events(db: Session, convs) -> Dict[str, Dict[str, Any]]:
    """{conversation id: {"human_at", "human_channel", "family_at", "out_at"}}
    from the conversation's own start: the newest reply a PERSON sent from the
    app that went out, the newest family message, and the newest message of
    any kind that went out to the family."""
    from app.models.models import EmailMessage, Message
    from app.models.program_models import ProgramConversationTurn
    from app.services import send_source as SS
    from sqlalchemy import func
    convs = [c for c in convs if c is not None]
    if not convs:
        return {}
    by_lead = {c.lead_id: c for c in convs}
    since = {c.lead_id: (c.created_at or datetime.min) for c in convs}
    out: Dict[str, Dict[str, Any]] = {c.id: {"human_at": None, "human_channel": None, "family_at": None,
                                              "out_at": None, "note_at": None} for c in convs}
    leads = list(by_lead)

    def put(lid, key, at, channel=None):
        c = by_lead.get(lid)
        if c is None or at is None or at < since[lid]:
            return
        ev = out[c.id]
        if ev[key] is None or at > ev[key]:
            ev[key] = at
            if key == "human_at":
                ev["human_channel"] = channel
    for i in range(0, len(leads), 500):
        part = leads[i:i + 500]
        for lid, src, at, st1, st2, st3 in (db.query(Message.lead_id, Message.send_source, Message.sent_at,
                                                     Message.delivery_status, Message.twilio_status,
                                                     Message.send_state)
                                            .filter(Message.lead_id.in_(part), Message.sent_at.isnot(None)).all()):
            if any((x or "").lower() in FAILED_SMS for x in (st1, st2, st3)):
                continue
            put(lid, "out_at", at)
            if src == SS.MANUAL:
                put(lid, "human_at", at, "text")
        for lid, src, at, st in (db.query(EmailMessage.lead_id, EmailMessage.send_source, EmailMessage.sent_at,
                                          EmailMessage.status)
                                 .filter(EmailMessage.lead_id.in_(part), EmailMessage.sent_at.isnot(None)).all()):
            if (st or "").lower() in FAILED_EMAIL or (st or "").lower() == "queued":
                continue
            put(lid, "out_at", at)
            if src == SS.MANUAL:
                put(lid, "human_at", at, "email")
    for cid, at in (db.query(ProgramConversationTurn.conversation_id, func.max(ProgramConversationTurn.created_at))
                    .filter(ProgramConversationTurn.conversation_id.in_([c.id for c in convs]),
                            ProgramConversationTurn.role == "family")
                    .group_by(ProgramConversationTurn.conversation_id).all()):
        if cid in out:
            out[cid]["family_at"] = at
    try:
        for lid, items in office_notes(db, leads).items():
            c = by_lead.get(lid)
            if c is not None and items:
                out[c.id]["note_at"] = items[0][0]       # newest note a person wrote
    except Exception:                                    # noqa: BLE001
        log.exception("office notes lookup failed")
    return out


def _central(at: datetime) -> datetime:
    from datetime import timezone
    from app.services.volt.sheets_export import CENTRAL
    a = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    return a.astimezone(CENTRAL)


def _ct_text(at: datetime) -> str:
    t = _central(at)
    return "%d/%d/%d %d:%02d %s" % (t.month, t.day, t.year, (t.hour % 12) or 12, t.minute,
                                    "AM" if t.hour < 12 else "PM")


def _replied_text(ev: Dict[str, Any]) -> str:
    t = _central(ev["human_at"])
    return "%s%s (%d:%02d %s)" % (REPLIED_PREFIX, ev["human_channel"] or "text", (t.hour % 12) or 12, t.minute,
                                  "AM" if t.hour < 12 else "PM")


def follow_up_rows(db: Session, org_id: str, known_ids=()) -> List[List[Any]]:
    """Every family that is open now, plus every one already on the sheet (so a
    closed one shows its new status instead of disappearing)."""
    from app.models.models import BookingLink, Lead
    from app.models.program_models import ProgramConversation
    from app.services.programs import unbooked_confirmations as UC
    from app.services.volt.sheets_export import central_serial, safe, safe_phone
    known = {k[3:] for k in known_ids if str(k).startswith("FU-")}
    convs = (db.query(ProgramConversation)
             .filter(ProgramConversation.organization_id == org_id,
                     (ProgramConversation.state.in_(list(OPEN_STATES)))
                     | (ProgramConversation.id.in_(list(known) or ["-"]))).all())
    leads = {l.id: l for l in db.query(Lead).filter(Lead.id.in_([c.lead_id for c in convs] or ["-"])).all()}
    out = []
    try:
        events = followup_events(db, convs)
    except Exception:                                    # noqa: BLE001 - the rows still go in
        log.exception("follow-up events lookup failed")
        events = {}
    try:
        notes = office_notes(db, [c.lead_id for c in convs])
    except Exception:                                    # noqa: BLE001 - the rows still go in
        log.exception("office notes lookup failed")
        notes = {}
    for c in convs:
        lead = leads.get(c.lead_id)
        if _skip(lead) or c.is_test:
            continue
        out.append(_conv_row(db, c, lead, events.get(c.id), notes.get(c.lead_id)))
    # Told a time by a person, nothing on the calendar: lock it in.
    told_known = {k[5:] for k in known_ids if str(k).startswith("TOLD-")}
    try:
        told = UC.find(db, org_id)
    except Exception:                                    # noqa: BLE001
        log.exception("unbooked confirmations lookup failed")
        told = []
    seen = set()
    for t in told:
        lead = db.query(Lead).filter(Lead.id == t["lead_id"]).first()
        if _skip(lead):
            continue
        seen.add(t["message_id"])
        out.append(["TOLD-" + t["message_id"], central_serial(t["at"]), safe(t["name"]), _phone(lead.phone),
                    safe(lead.email), "Told a time - not on the calendar yet. Book it.", "",
                    safe(t["said"][:300]), "Open"])
    for mid in told_known - seen:
        # No longer unbooked (booked since, or past the look-back): mark it done.
        out.append(["TOLD-" + mid, None, None, None, None, None, None, None, "Booked or handled"])
    return out


# ── the board's look (applied once by the server) ───────────────────────────
#
# Restland's own colors (#507 design decision): forest green, warm ivory,
# muted brass. Applied the first time the server writes to the sheet, then
# recorded ("formatted": true) so a person's later changes are left alone.

PERSON_TABS = ("Person 1", "Person 2", "Person 3", "Person 4", "Person 5")
NEW_FAMILIES = "New Families"
TAB_IDS = {"Dashboard": 0, APPOINTMENTS: 1, FOLLOW_UPS: 2, "Person 1": 3, "Person 2": 4, "Team": 5,
           "Person 3": 7, "Person 4": 8, "Person 5": 9, NEW_FAMILIES: 10}
GREEN, IVORY, BRASS, GOLD, TEXT, GREY, LINE = ("#193C32", "#F7F5ED", "#C3A66A", "#F6EDD9", "#24392F",
                                               "#5B6B63", "#E2DDCD")


def _rgb(hexcolor: str) -> Dict[str, float]:
    h = hexcolor.lstrip("#")
    return {"red": round(int(h[0:2], 16) / 255.0, 3), "green": round(int(h[2:4], 16) / 255.0, 3),
            "blue": round(int(h[4:6], 16) / 255.0, 3)}


def _grid(sid: int, a1: str) -> Dict[str, int]:
    m = re.fullmatch(r"([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?", a1)
    c1, r1, c2, r2 = m.group(1), int(m.group(2)), m.group(3) or m.group(1), int(m.group(4) or m.group(2))

    def ci(col):
        n = 0
        for ch in col:
            n = n * 26 + ord(ch) - 64
        return n - 1
    return {"sheetId": sid, "startRowIndex": r1 - 1, "endRowIndex": r2, "startColumnIndex": ci(c1),
            "endColumnIndex": ci(c2) + 1}


def _fmt(sid: int, a1: str, *, bold=None, italic=None, size=None, fg=None, bg=None, align=None,
         valign=None, wrap=None, number=None) -> Dict:
    f, fields, tf = {}, [], {}
    if bold is not None:
        tf["bold"] = bold
        fields.append("userEnteredFormat.textFormat.bold")
    if italic is not None:
        tf["italic"] = italic
        fields.append("userEnteredFormat.textFormat.italic")
    if size is not None:
        tf["fontSize"] = size
        fields.append("userEnteredFormat.textFormat.fontSize")
    if fg is not None:
        tf["foregroundColor"] = _rgb(fg)
        fields.append("userEnteredFormat.textFormat.foregroundColor")
    tf["fontFamily"] = "Arial"
    fields.append("userEnteredFormat.textFormat.fontFamily")
    f["textFormat"] = tf
    if bg is not None:
        f["backgroundColor"] = _rgb(bg)
        fields.append("userEnteredFormat.backgroundColor")
    if align:
        f["horizontalAlignment"] = align
        fields.append("userEnteredFormat.horizontalAlignment")
    if valign:
        f["verticalAlignment"] = valign
        fields.append("userEnteredFormat.verticalAlignment")
    if wrap:
        f["wrapStrategy"] = "WRAP"
        fields.append("userEnteredFormat.wrapStrategy")
    if number:
        f["numberFormat"] = {"type": "PERCENT" if "%" in number else "DATE_TIME" if ("d" in number or "h" in number)
                             else "NUMBER", "pattern": number}
        fields.append("userEnteredFormat.numberFormat")
    return {"repeatCell": {"range": _grid(sid, a1), "cell": {"userEnteredFormat": f}, "fields": ",".join(fields)}}


def _widths(sid: int, widths: Dict[str, int]) -> List[Dict]:
    out = []
    for col, px in widths.items():
        g = _grid(sid, "%s1" % col)
        out.append({"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS",
                                                             "startIndex": g["startColumnIndex"],
                                                             "endIndex": g["endColumnIndex"]},
                                                   "properties": {"pixelSize": px}, "fields": "pixelSize"}})
    return out


def _height(sid: int, row: int, px: int) -> Dict:
    return {"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "ROWS", "startIndex": row - 1,
                                                    "endIndex": row}, "properties": {"pixelSize": px},
                                          "fields": "pixelSize"}}


def _borders(sid: int, a1: str) -> Dict:
    b = {"style": "SOLID", "width": 1, "color": _rgb(LINE)}
    return {"updateBorders": {"range": _grid(sid, a1), "top": b, "bottom": b, "left": b, "right": b,
                              "innerHorizontal": b, "innerVertical": b}}


def _list(sid: int, a1: str, values: List[str]) -> Dict:
    return {"setDataValidation": {"range": _grid(sid, a1), "rule": {
        "condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": v} for v in values]},
        "strict": False, "showCustomUi": True}}}


def _team_list(sid: int, a1: str) -> Dict:
    return {"setDataValidation": {"range": _grid(sid, a1), "rule": {
        "condition": {"type": "ONE_OF_RANGE", "values": [{"userEnteredValue": "=Team!$A$2:$A$30"}]},
        "strict": False, "showCustomUi": True}}}


def _checkbox(sid: int, a1: str) -> Dict:
    return {"setDataValidation": {"range": _grid(sid, a1), "rule": {"condition": {"type": "BOOLEAN"}}}}


def _when(sid: int, a1: str, formula: str, bg: str, fg: Optional[str] = None) -> Dict:
    fmt: Dict[str, Any] = {"backgroundColor": _rgb(bg)}
    if fg:
        fmt["textFormat"] = {"foregroundColor": _rgb(fg)}
    return {"addConditionalFormatRule": {"rule": {"ranges": [_grid(sid, a1)], "booleanRule": {
        "condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": formula}]}, "format": fmt}},
        "index": 0}}


def layout_requests() -> List[Dict]:
    """Every formatting request for the board, in one batch."""
    A, F, D, T = TAB_IDS[APPOINTMENTS], TAB_IDS[FOLLOW_UPS], TAB_IDS["Dashboard"], TAB_IDS["Team"]
    r: List[Dict] = []
    tab_color = {D: GREEN, A: GREEN, F: BRASS, T: IVORY}
    tab_color.update({TAB_IDS[p]: GOLD for p in PERSON_TABS})
    for sid, color in tab_color.items():
        r.append({"updateSheetProperties": {"properties": {"sheetId": sid, "tabColorStyle": {"rgbColor": _rgb(color)}},
                                            "fields": "tabColorStyle"}})
    # Appointments: system A-L, the team's M-P (Assigned To, Remote Call Link, Outcome, Team Notes)
    r += [_fmt(A, "A2:P2000", size=10, fg=TEXT, valign="TOP"),
          _fmt(A, "A1:L1", bold=True, fg="#FFFFFF", bg=GREEN, valign="MIDDLE", wrap=True),
          _fmt(A, "M1:P1", bold=True, fg="#FFFFFF", bg=BRASS, valign="MIDDLE", wrap=True),
          _fmt(A, "M2:P2000", bg=GOLD), _fmt(A, "B2:B2000", number="m/d/yyyy h:mm am/pm"),
          _fmt(A, "C2:C2000", number="ddd, mmm d, yyyy"), _fmt(A, "D2:D2000", number="h:mm am/pm"),
          _fmt(A, "L2:L2000", wrap=True), _fmt(A, "N2:N2000", wrap=True), _fmt(A, "P2:P2000", wrap=True),
          _borders(A, "A1:P2000"), _height(A, 1, 40),
          _team_list(A, "M2:M2000"),
          _list(A, "O2:O2000", ["Showed", "No-Show", "Rescheduled", "Cancelled"]),
          _when(A, "A2:P2000", '=AND($C2<>"",$C2=TODAY())', "#DCEFE3"),
          _when(A, "A2:P2000", '=OR(LEFT($K2,6)="Cancel",$O2="Cancelled")', "#EEEEEE", GREY),
          _when(A, "M2:M2000", '=AND($A2<>"",$M2="",$C2>=TODAY())', "#FBE3DF")]
    # Sold? / What Was Sold (team, Q-R) and Last Update (system, S)
    r += [_fmt(A, "Q1:R1", bold=True, fg="#FFFFFF", bg=BRASS, valign="MIDDLE", wrap=True),
          _fmt(A, "S1", bold=True, fg="#FFFFFF", bg=GREEN, valign="MIDDLE", wrap=True),
          _fmt(A, "Q2:R2000", bg=GOLD, size=10, fg=TEXT, valign="TOP"), _fmt(A, "R2:R2000", wrap=True),
          _fmt(A, "S2:S2000", number="m/d/yyyy h:mm am/pm", size=10, fg=TEXT, valign="TOP"),
          _list(A, "Q2:Q2000", list(SOLD_CHOICES)), _borders(A, "A1:S2000"),
          _when(A, "Q2:Q2000", '=$Q2="Yes"', "#DCEFE3")]
    r += _widths(A, {"A": 90, "B": 135, "C": 130, "D": 80, "E": 85, "F": 170, "G": 115, "H": 190, "I": 135,
                     "J": 200, "K": 120, "L": 260, "M": 140, "N": 200, "O": 110, "P": 240, "Q": 70, "R": 220,
                     "S": 135})
    # Follow-Ups: system A-I, the team's J-P, the booking result Q, the team's
    # follow-up log R-S
    r += [_fmt(F, "A2:S2000", size=10, fg=TEXT, valign="TOP"),
          _fmt(F, "R1:S1", bold=True, fg="#FFFFFF", bg=BRASS, valign="MIDDLE", wrap=True),
          _fmt(F, "R2:S2000", bg=GOLD), _fmt(F, "R2:R2000", number="ddd, mmm d, yyyy"),
          {"setDataValidation": {"range": _grid(F, "R2:R2000"), "rule": {
              "condition": {"type": "DATE_IS_VALID"}, "strict": False, "showCustomUi": True}}},
          _list(F, "S2:S2000", list(WHAT_HAPPENED)),
          _fmt(F, "A1:I1", bold=True, fg="#FFFFFF", bg=GREEN, valign="MIDDLE", wrap=True),
          _fmt(F, "J1:P1", bold=True, fg="#FFFFFF", bg=BRASS, valign="MIDDLE", wrap=True),
          _fmt(F, "Q1", bold=True, fg="#FFFFFF", bg=GREEN, valign="MIDDLE", wrap=True),
          _fmt(F, "J2:P2000", bg=GOLD), _fmt(F, "B2:B2000", number="m/d/yyyy h:mm am/pm"),
          _fmt(F, "G2:G2000", number="ddd, mmm d, yyyy"), _fmt(F, "M2:M2000", number="ddd, mmm d, yyyy"),
          _fmt(F, "F2:F2000", wrap=True), _fmt(F, "H2:H2000", wrap=True), _fmt(F, "L2:L2000", wrap=True),
          _fmt(F, "P2:P2000", wrap=True), _fmt(F, "Q2:Q2000", wrap=True, bold=True),
          _borders(F, "A1:S2000"), _height(F, 1, 40),
          _team_list(F, "J2:J2000"), _checkbox(F, "K2:K2000"),
          {"setDataValidation": {"range": _grid(F, "M2:M2000"), "rule": {
              "condition": {"type": "DATE_IS_VALID"}, "strict": False, "showCustomUi": True}}},
          _list(F, "N2:N2000", list(TIME_CHOICES)), _list(F, "O2:O2000", HOW_PICK),
          _when(F, "A2:S2000", '=OR($K2=TRUE,$I2<>"Open")', "#EEEEEE", GREY),
          _when(F, "A2:S2000", '=AND($I2="Open",$K2<>TRUE,OR($G2="",$G2<TODAY()+1))', "#FBE3DF"),
          _when(F, "Q2:Q2000", '=LEFT($Q2,6)="Booked"', "#DCEFE3"),
          _when(F, "Q2:Q2000", '=LEFT($Q2,10)="Not booked"', "#FBE3DF")]
    r += _widths(F, {"A": 90, "B": 135, "C": 170, "D": 115, "E": 190, "F": 260, "G": 130, "H": 280, "I": 110,
                     "J": 140, "K": 70, "L": 260, "M": 140, "N": 110, "O": 150, "P": 220, "Q": 260,
                     "R": 130, "S": 200, "T": 135})
    r += [_fmt(F, "T1", bold=True, fg="#FFFFFF", bg=GREEN, valign="MIDDLE", wrap=True),
          _fmt(F, "T2:T2000", number="m/d/yyyy h:mm am/pm", size=10, fg=TEXT, valign="TOP"),
          _borders(F, "A1:T2000")]
    # Dashboard
    r += [_fmt(D, "A1:H31", size=10, fg=TEXT),
          _fmt(D, "A1", bold=True, size=18, fg=GREEN), _fmt(D, "A2", italic=True, fg=GREY),
          _fmt(D, "B4:G4", bold=True, fg="#FFFFFF", bg=GREEN, align="CENTER", valign="MIDDLE", wrap=True),
          _fmt(D, "B5:G5", bold=True, size=22, fg=GREEN, bg=IVORY, align="CENTER", number="#,##0"),
          _fmt(D, "G5", number="0.0%"),
          _fmt(D, "B7:G7", bold=True, fg="#FFFFFF", bg=BRASS, align="CENTER", valign="MIDDLE", wrap=True),
          _fmt(D, "B8:G8", bold=True, size=22, fg=GREEN, bg=IVORY, align="CENTER", number="#,##0"),
          _fmt(D, "A10:F10", bold=True, fg="#FFFFFF", bg=GREEN, valign="MIDDLE", wrap=True),
          _fmt(D, "B11:F20", align="CENTER"), _borders(D, "A10:F20"),
          _fmt(D, "A22", bold=True, size=12, fg=GREEN),
          _fmt(D, "A23:C23", bold=True, fg="#FFFFFF", bg=GREEN, valign="MIDDLE", wrap=True),
          _fmt(D, "A24:A31", number="mmm d, yyyy"), _fmt(D, "B24:C31", align="CENTER"), _borders(D, "A23:C31"),
          {"mergeCells": {"range": _grid(D, "A1:G1"), "mergeType": "MERGE_ALL"}},
          {"mergeCells": {"range": _grid(D, "A2:G2"), "mergeType": "MERGE_ALL"}},
          _height(D, 4, 36), _height(D, 5, 54), _height(D, 7, 36), _height(D, 8, 54), _height(D, 10, 34),
          _height(D, 23, 34)]
    r += _widths(D, {"A": 160, "B": 135, "C": 135, "D": 135, "E": 135, "F": 135, "G": 135})
    r.append({"addChart": {"chart": {"spec": {"title": "Appointments by Week", "basicChart": {
        "chartType": "COLUMN", "legendPosition": "BOTTOM_LEGEND", "headerCount": 1,
        "domains": [{"domain": {"sourceRange": {"sources": [_grid(D, "A23:A31")]}}}],
        "series": [{"series": {"sourceRange": {"sources": [_grid(D, "B23:B31")]}}, "targetAxis": "LEFT_AXIS",
                    "colorStyle": {"rgbColor": _rgb(GREEN)}},
                   {"series": {"sourceRange": {"sources": [_grid(D, "C23:C31")]}}, "targetAxis": "LEFT_AXIS",
                    "colorStyle": {"rgbColor": _rgb(BRASS)}}]}},
        "position": {"overlayPosition": {"anchorCell": {"sheetId": D, "rowIndex": 21, "columnIndex": 4},
                                         "widthPixels": 560, "heightPixels": 300}}}}})
    # Person tabs
    for sid in (TAB_IDS[p] for p in PERSON_TABS):
        r += [_fmt(sid, "A1:P500", size=10, fg=TEXT, valign="TOP"),
              _fmt(sid, "A1", bold=True, size=16, fg=GREEN), _fmt(sid, "A2", bold=True),
              _fmt(sid, "B2", bold=True, size=14, fg=GREEN, bg=GOLD), _fmt(sid, "D2", italic=True, size=9, fg=GREY),
              _fmt(sid, "K3", bold=True, fg=GREEN),
              _fmt(sid, "A4:I4", bold=True, fg="#FFFFFF", bg=GREEN, valign="MIDDLE", wrap=True),
              _fmt(sid, "K4:P4", bold=True, fg="#FFFFFF", bg=BRASS, valign="MIDDLE", wrap=True),
              _fmt(sid, "A5:A500", number="ddd, mmm d, yyyy"), _fmt(sid, "B5:B500", number="h:mm am/pm"),
              _fmt(sid, "K5:K500", number="ddd, mmm d, yyyy"), _fmt(sid, "N5:O500", wrap=True),
              _fmt(sid, "I5:I500", wrap=True), _height(sid, 4, 36)]
        r += _widths(sid, {"A": 130, "B": 80, "C": 85, "D": 160, "E": 110, "F": 130, "G": 180, "H": 110, "I": 200,
                           "J": 24, "K": 130, "L": 160, "M": 110, "N": 220, "O": 260, "P": 60})
    # New Families: the team's entries A-L, the system's answer M-O
    N = TAB_IDS[NEW_FAMILIES]
    r += [{"updateSheetProperties": {"properties": {"sheetId": N, "tabColorStyle": {"rgbColor": _rgb(BRASS)}},
                                     "fields": "tabColorStyle"}},
          _fmt(N, "A2:O2000", size=10, fg=TEXT, valign="TOP"),
          _fmt(N, "A1:L1", bold=True, fg="#FFFFFF", bg=BRASS, valign="MIDDLE", wrap=True),
          _fmt(N, "M1:O1", bold=True, fg="#FFFFFF", bg=GREEN, valign="MIDDLE", wrap=True),
          _fmt(N, "A2:L2000", bg=GOLD), _fmt(N, "N2:N2000", number="m/d/yyyy h:mm am/pm"),
          _fmt(N, "L2:M2000", wrap=True), _borders(N, "A1:O2000"), _height(N, 1, 40),
          _team_list(N, "A2:A2000"),
          {"setDataValidation": {"range": _grid(N, "B2:B2000"), "rule": {
              "condition": {"type": "ONE_OF_RANGE", "values": [{"userEnteredValue": "=Appointments!$F$2:$F"}]},
              "strict": False, "showCustomUi": True}}},
          _when(N, "M2:M2000", '=LEFT($M2,5)="Added"', "#DCEFE3"),
          _when(N, "M2:M2000", '=OR(LEFT($M2,7)="Missing",LEFT($M2,7)="Already")', "#FBE3DF")]
    r += _widths(N, {"A": 140, "B": 170, "C": 120, "D": 130, "E": 200, "F": 120, "G": 60, "H": 70,
                     "I": 120, "J": 200, "K": 140, "L": 220, "M": 240, "N": 135, "O": 110})
    # Team (Active? is the team's switch; Status is written by the system)
    r += [_fmt(T, "A1:F1", bold=True, fg="#FFFFFF", bg=GREEN), _fmt(T, "A2:E30", size=10, bg=GOLD),
          _fmt(T, "F2:F30", size=10), _checkbox(T, "E2:E30"), _borders(T, "A1:F30")]
    r += _widths(T, {"A": 180, "B": 130, "C": 220, "D": 240, "E": 70, "F": 320})
    return r


def ensure_layout(db: Session, prog, client, sid: str) -> bool:
    """Apply the board's look once; record it so a person's later changes stay."""
    pol = json.loads(prog.booking_policy_json or "{}")
    cfg = pol.get("handoff_sheet") or {}
    if cfg.get("formatted"):
        return False
    client.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": layout_requests()}).execute()
    cfg["formatted"] = True
    pol["handoff_sheet"] = cfg
    prog.booking_policy_json = json.dumps(pol)
    db.commit()
    return True


# ── writing ─────────────────────────────────────────────────────────────────

def _rng(tab: str, a1: str) -> str:
    return "'%s'!%s" % (tab.replace("'", "''"), a1)


def upsert(client, sid: str, tab: str, rows: List[List[Any]]) -> Dict[str, int]:
    """Write the system columns of each row, found by its id in column A.
    A None cell leaves that cell as it is. Staff columns are never written."""
    from app.services.volt.sheets_export import SheetsError, column
    cols = COLUMNS[tab]
    width = len(cols)
    values = client.spreadsheets().values()
    head = values.get(spreadsheetId=sid, range=_rng(tab, "A1:%s1" % column(width))).execute()
    have = [str(c).strip() for c in (head.get("values") or [[]])[0]]
    if have != list(cols):
        # Owner, Nov 24: someone blanked the first header ("Follow-Up ID") and
        # the whole sync stopped. Only the id header blank, the rest right:
        # put it back and carry on. Anything else still stops the sync.
        if have and not have[0] and have[1:] == list(cols)[1:]:
            values.update(spreadsheetId=sid, range=_rng(tab, "A1"), valueInputOption="RAW",
                          body={"values": [[cols[0]]]}).execute()
            log.warning("handoff sheet: put back the blank %r header on %s", cols[0], tab)
        else:
            raise SheetsError("sheet_layout")
    got = values.get(spreadsheetId=sid, range=_rng(tab, "A2:A%d" % (MAX_ROWS + 1))).execute()
    where: Dict[str, int] = {}
    filled = 1
    for i, cells in enumerate(got.get("values") or []):
        key = str(cells[0]).strip() if cells else ""
        if key:
            where.setdefault(key, i + 2)
            filled = i + 2
    data, nxt, ins = [], filled + 1, 0
    for row in rows:
        rid = str(row[0])
        if rid in where:
            n = where[rid]
        else:
            if any(c is None for c in row):
                continue                                 # an update-only row for an id the sheet doesn't have
            n, nxt, ins = nxt, nxt + 1, ins + 1
            where[rid] = n
        if all(c is not None for c in row):
            data.append({"range": _rng(tab, "A%d:%s%d" % (n, column(width), n)), "values": [row]})
        else:
            for j, cell in enumerate(row):
                if cell is not None:
                    data.append({"range": _rng(tab, "%s%d" % (column(j + 1), n)), "values": [[cell]]})
    for i in range(0, len(data), 500):
        values.batchUpdate(spreadsheetId=sid, body={"valueInputOption": "RAW", "data": data[i:i + 500]}).execute()
    return {"written": len(data), "inserted": ins}


def known_ids(client, sid: str, tab: str) -> List[str]:
    got = client.spreadsheets().values().get(spreadsheetId=sid, range=_rng(tab, "A2:A%d" % (MAX_ROWS + 1))).execute()
    return [str(c[0]).strip() for c in (got.get("values") or []) if c and str(c[0]).strip()]


# ── the team's side: read back, round robin, pause ─────────────────────────
#
# Owner, Nov 22 8:45 PM: "when they update it here, it can get updated on the
# app ... We need to know what has happened" / "a team of five ... auto
# assigned ... round robin ... pause putting somebody in there if they hadn't
# been updating the spreadsheet ... a week ... alert Mike Sleater and myself."

PAUSE_AFTER = timedelta(days=7)
PLACEHOLDER = re.compile(r"^\s*Person\s+\d+\s*$", re.I)
TEAM_RANGE = "'Team'!A2:E30"
APPT_KEYS = ("assigned", "link", "outcome", "notes", "sold", "sold_what")
FU_KEYS = ("assigned", "done", "notes", "appt_date", "appt_time", "appt_how", "appt_address",
           "fu_date", "fu_result")
FU_WORK_KEYS = ("fu_date", "fu_result")
APPT_FIELDS = ("appt_date", "appt_time", "appt_how", "appt_address")
KEYS = {APPOINTMENTS: APPT_KEYS, FOLLOW_UPS: FU_KEYS}


def _truthy(v) -> bool:
    return str(v).strip().upper() == "TRUE" or v is True


def read_staff(client, sid: str, tab: str) -> Dict[str, Dict[str, Any]]:
    """{row id: {"row": n, keys...}} - the team's columns as they are now,
    found by their HEADER (not their position), so a column the owner adds,
    moves or deletes never makes one column read as another."""
    from app.services.volt.sheets_export import column
    labels = list(STAFF_COLUMNS[tab]) + (list(FU_WORK_COLUMNS) if tab == FOLLOW_UPS else [])
    keys = list(KEYS[tab])
    got = client.spreadsheets().values().get(
        spreadsheetId=sid, range=_rng(tab, "A1:%s%d" % (column(40), MAX_ROWS + 1))).execute()
    values = got.get("values") or []
    header = [str(c).strip() for c in (values[0] if values else [])]
    where = {k: (header.index(lbl) if lbl in header else None) for k, lbl in zip(keys, labels)}
    out: Dict[str, Dict[str, Any]] = {}
    for i, cells in enumerate(values[1:]):
        rid = str(cells[0]).strip() if cells else ""
        if not rid or rid in out:
            continue
        vals = {k: (str(cells[j]).strip() if j is not None and j < len(cells) else "") for k, j in where.items()}
        out[rid] = dict(vals, row=i + 2)
    return out

def read_team(client, sid: str) -> List[Dict[str, Any]]:
    """The people on the Team tab, in order: name, active (the Active? box),
    and whether the name is still a placeholder ("Person 3")."""
    got = client.spreadsheets().values().get(spreadsheetId=sid, range=TEAM_RANGE).execute()
    team = []
    for i, cells in enumerate(got.get("values") or []):
        cells = list(cells) + [""] * 5
        name = str(cells[0]).strip()
        if not name:
            continue
        team.append({"name": name, "row": i + 2, "active": str(cells[4]).strip().upper() != "FALSE",
                     "placeholder": bool(PLACEHOLDER.match(name)),
                     "phone": str(cells[1]).strip(), "email": str(cells[2]).strip()})
    return team


def _lead_for(db, tab: str, rid: str) -> Optional[str]:
    from app.models.models import BookingLink, EmailMessage, Message
    from app.models.program_models import ProgramConversation
    if tab == APPOINTMENTS:
        b = db.query(BookingLink.lead_id).filter(BookingLink.id == rid).first()
        return b[0] if b else None
    if rid.startswith("FU-"):
        c = db.query(ProgramConversation.lead_id).filter(ProgramConversation.id == rid[3:]).first()
        return c[0] if c else None
    if rid.startswith("TOLD-"):
        for M in (Message, EmailMessage):
            m = db.query(M.lead_id).filter(M.id == rid[5:]).first()
            if m:
                return m[0]
    return None


def _note(db, org_id: str, lead_id: Optional[str], body: str) -> None:
    if not lead_id:
        return
    from app.models.work_models import LeadNote
    db.add(LeadNote(organization_id=org_id, lead_id=lead_id, body=body[:4000], kind="internal"))


def _parse_slot(date_text: str, time_text: str, now: datetime) -> Optional[datetime]:
    """'Tue, Dec 1, 2026' / '12/1' + '10:00 AM' -> naive Central datetime."""
    from dateutil import parser as P
    from zoneinfo import ZoneInfo
    from datetime import timezone
    if not (date_text or "").strip() or not (time_text or "").strip():
        return None
    today = now.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("America/Chicago")).replace(tzinfo=None)
    try:
        d = P.parse(date_text, default=datetime(today.year, 1, 1))
        t = P.parse(time_text, default=datetime(today.year, 1, 1))
    except (ValueError, OverflowError):
        return None
    if not re.search(r"\b\d{4}\b", date_text) and d.date() < today.date():
        d = d.replace(year=d.year + 1)                 # "1/4" typed in December means next year
    return datetime(d.year, d.month, d.day, t.hour, t.minute)


def _book_from_sheet(db, prog, rid: str, lead_id: Optional[str], new: Dict, who: str,
                     now: datetime) -> str:
    """The team filled in an appointment on a follow-up: book it for real (same
    path as every booking - the real calendar, the family's confirmation) and
    say what happened in plain words."""
    from zoneinfo import ZoneInfo
    from datetime import timezone
    from app.models.models import Lead
    from app.models.program_models import ProgramConversation
    from app.services.programs import appointments as A
    slot = _parse_slot(new.get("appt_date", ""), new.get("appt_time", ""), now)
    if slot is None:
        return "Not booked: fill in both the date and the time (e.g. 12/1 and 10:00 AM)"
    local_now = now.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("America/Chicago")).replace(tzinfo=None)
    if slot <= local_now:
        return "Not booked: that date and time has already passed"
    lead = db.query(Lead).filter(Lead.id == lead_id).first() if lead_id else None
    if lead is None:
        return "Not booked: this family wasn't found"
    key, kind = HOW_CHOICES.get((new.get("appt_how") or "").strip(), HOW_CHOICES["At Restland"])
    addr = (new.get("appt_address") or "").strip() or None
    res = A.book(db, prog, lead, slot, kind, meeting_key=key, visit_address=addr if key == "home" else None)
    when = slot.strftime("%a %b %-d, %-I:%M %p")
    if res.get("ok"):
        if rid.startswith("FU-"):
            c = db.query(ProgramConversation).filter(ProgramConversation.id == rid[3:]).first()
            if c is not None:
                c.state, c.booked_time, c.booking_link_id = "booked", slot, res.get("booking_id")
                c.next_followup_at = None
        try:
            A.notify_team_booked(db, prog, lead, slot, kind)
        except Exception:                                # noqa: BLE001 - the booking stands either way
            log.exception("booked-from-sheet alert failed")
        return "Booked for %s%s" % (when, " (simulated - test family)" if res.get("simulated") else "")
    why = {"taken": "that time is already taken - pick another time",
           "already_booked": "this family already has an appointment (%s)" % (res.get("booked_time") or ""),
           "opted_out": "this family asked us to stop contacting them",
           "no_calendar": "no booking calendar is set up"}.get(res.get("status"), res.get("reason") or "it didn't go through")
    if res.get("status") == "needs_place":
        why = "for a home visit, put the full home address (street, city, state, ZIP)"
    return "Not booked: %s" % why


def _apply(db, prog, tab: str, rid: str, lead_id: Optional[str], old: Dict, new: Dict,
           now: datetime, results: Optional[Dict[str, str]] = None) -> List[str]:
    """What the team changed on the sheet, put into the app. Returns the
    changes in plain words (also written to the family's notes)."""
    from app.models.models import BookingLink
    from app.models.program_models import ProgramConversation
    who = new.get("assigned") or "the team"
    said: List[str] = []
    changed = [k for k in KEYS[tab] if (old.get(k) or "") != (new.get(k) or "")]
    if not changed:
        return said
    if "assigned" in changed and new.get("assigned"):
        said.append("assigned to %s" % new["assigned"])
    if tab == APPOINTMENTS:
        b = db.query(BookingLink).filter(BookingLink.id == rid).first()
        if "outcome" in changed and new.get("outcome"):
            out = new["outcome"]
            said.append("outcome: %s" % out)
            status = {"showed": "completed", "no-show": "no_show"}.get(out.strip().lower())
            if b is not None and status and (b.status or "") in ("booked", "confirmed", "completed", "no_show"):
                from app.services.programs import appointment_ops as OPS
                from app.services.programs import appointments as A
                actor = A.program_advisor(db, prog)          # the program's booking calendar owner
                sp = db.begin_nested()
                try:
                    OPS.attendance(db, prog, b, status, actor, now=now)
                    if sp.is_active:
                        sp.commit()
                except Exception as exc:                 # noqa: BLE001 - recorded as a note instead
                    if sp.is_active:
                        sp.rollback()
                    said.append("(not recorded as attendance: %s)" % str(exc)[:120])
        if "link" in changed and new.get("link"):
            said.append("call details: %s" % new["link"])
        if ("sold" in changed or "sold_what" in changed) and (new.get("sold") or new.get("sold_what")):
            if (new.get("sold") or "").strip().lower() == "no":
                said.append("nothing sold")
            else:
                said.append("SOLD: %s" % (new.get("sold_what") or "(what was sold not filled in yet)"))
    else:
        if any(k in changed for k in APPT_FIELDS) and (new.get("appt_date") or new.get("appt_time")):
            result = _book_from_sheet(db, prog, rid, lead_id, new, who, now)
            said.append(result)
            if results is not None:
                results[rid] = result
        if any(k in changed for k in FU_WORK_KEYS) and (new.get("fu_date") or new.get("fu_result")):
            said.append("followed up%s%s" % (" on %s" % new["fu_date"] if new.get("fu_date") else "",
                                              ": %s" % new["fu_result"] if new.get("fu_result") else ""))
            if ((new.get("fu_result") or "").strip() == _BOOKED_RESULT and results is not None
                    and rid not in results and not (new.get("appt_date") and new.get("appt_time"))):
                results[rid] = "Not booked yet: fill in Appointment Date and Appointment Time to book it"
        if "done" in changed and _truthy(new.get("done")):
            said.append("follow-up done")
            if rid.startswith("FU-"):
                c = db.query(ProgramConversation).filter(ProgramConversation.id == rid[3:]).first()
                if c is not None and c.state in OPEN_STATES:
                    c.state, c.next_followup_at = "closed", None
                    c.handoff_reason = ("Handled by %s on the Restland sheet" % who)[:250]
    if "notes" in changed and new.get("notes"):
        said.append("notes: %s" % new["notes"])
    if said:
        _note(db, prog.organization_id, lead_id, "Restland sheet (%s): %s" % (who, "; ".join(said)))
    return said


def _eligible(tab: str, row: List[Any], today_serial: float) -> bool:
    """Rows that need a person now: upcoming booked appointments, open follow-ups."""
    if tab == APPOINTMENTS:
        status = str(row[10] or "")
        date = row[2]
        return status.startswith(("Booked", "Confirmed")) and isinstance(date, (int, float)) and date >= today_serial
    return str(row[8] or "") == "Open"


def _cfg(prog) -> Dict[str, Any]:
    return json.loads(prog.booking_policy_json or "{}").get("handoff_sheet") or {}


def _save_cfg(prog, cfg: Dict[str, Any]) -> None:
    pol = json.loads(prog.booking_policy_json or "{}")
    pol["handoff_sheet"] = cfg
    prog.booking_policy_json = json.dumps(pol)


def _pause_alert(db, prog, name: str, since: datetime, now: datetime) -> None:
    from app.services.programs import alert_ledger as L
    when = since.strftime("%b %-d")
    sms = ("Restland sheet: %s hasn't updated the sheet since %s. New families are no longer being assigned "
           "to them until they do." % (name, when))
    L.page(db, prog, lead_id=None, key="handoff-paused:%s:%s" % (name.lower(), now.strftime("%Y%m%d")),
           kind="handoff", sms_text=sms, subject="Restland sheet - %s paused (no updates in a week)" % name,
           html="<p>%s</p>" % sms, management=True, coalesce=False, audit_message=sms)


# ── each person's own tab: a working list they update (owner, Nov 23 4:55 PM) ─
#
# "they need to be able to update that on their sheet, and then ultimately when
# they update that sheet, it needs to [update everything]". A person tab is
# written by the system. Owner, Nov 23 5:54 PM: "they need to know which block
# is what ... it needs to say it up top ... read better" - so the tab is two
# blocks STACKED (not side by side): MY APPOINTMENTS on top, MY FOLLOW-UPS
# below, each with a banner, a "who & when" group and a gold "your update"
# group. What they type is copied to the main tab and into the app on the next
# pass; if the same field changed on both, their own tab wins.

# Owner, Nov 25: everyone added on the Team tab gets BOTH tabs (appointments
# and follow-ups). The first five use the tabs the board was built with; more
# people get new tabs (Team rows 7-16).
PERSON_TAB_IDS = (3, 4, 7, 8, 9) + tuple(range(101, 111))   # Team rows 2-16, in order
PT_APPT = (("ref", "Ref"), ("date", "Date"), ("time", "Time"), ("family", "Family"), ("phone", "Phone"),
           ("how", "How"), ("address", "Home Address"), ("said", "Notes / What They Said"), ("status", "Status"),
           ("outcome", "Outcome"), ("sold", "Sold?"), ("sold_what", "What Was Sold"),
           ("link", "Call Link / Details"), ("notes", "My Notes"), ("updated", "Last Update"))
# Owner, Nov 24 9:14 PM ("need dates, last updated, better all around"): the
# dates come first and stay on screen - Follow Up On, Last Update, Family.
PT_FU = (("ref", "Ref"), ("due", "Follow Up On"), ("updated", "Last Update"), ("family", "Family"),
         ("phone", "Phone"), ("why", "Why"), ("said", "What They Said"),
         ("done", "Done?"), ("fu_date", "Followed Up On"), ("fu_result", "What Happened"),
         ("appt_date", "Appt Date"), ("appt_time", "Appt Time"), ("appt_how", "How"),
         ("appt_address", "Home Address"), ("notes", "My Notes"))
PT_APPT_EDIT = ("link", "outcome", "sold", "sold_what", "notes")
PT_FU_EDIT = ("done", "fu_date", "fu_result", "appt_date", "appt_time", "appt_how", "appt_address", "notes")
PT_APPT_INFO = 9                                     # A-I who & when, J-N your update, O last update
PT_FU_INFO = 7                                       # A-G when, who & why; H-O your update
PT_WIDTH = max(len(PT_APPT), len(PT_FU))             # A..O
# rows (1-based)
PT_TITLE, PT_HELP = 1, 2
PT_APPT_BANNER, PT_APPT_GROUPS, PT_APPT_HEAD, PT_APPT_FIRST, PT_APPT_MAX = 4, 5, 6, 7, 40
PT_FU_BANNER = PT_APPT_FIRST + PT_APPT_MAX + 1       # 48
PT_FU_GROUPS, PT_FU_HEAD, PT_FU_FIRST, PT_FU_MAX = PT_FU_BANNER + 1, PT_FU_BANNER + 2, PT_FU_BANNER + 3, 200
PT_LAST_ROW = PT_FU_FIRST + PT_FU_MAX - 1
# Owner, Nov 24: follow-ups get a tab of their own per person ("Kitty Arnold -
# Follow-Ups"); the person tab keeps only their appointments.
FU_TAB_IDS = (11, 12, 13, 14, 15) + tuple(range(201, 211))  # Team rows 2-16, in order
FT_BANNER, FT_GROUPS, FT_HEAD, FT_FIRST, FT_MAX = 4, 5, 6, 7, 200
FT_LAST_ROW = FT_FIRST + FT_MAX - 1
PT_APPT_LAST_ROW = PT_APPT_FIRST + PT_APPT_MAX - 1
# where each kind of tab keeps its one block: (main tab, first data row, max rows, columns)
KIND_BLOCK = {"appt": (APPOINTMENTS, PT_APPT_FIRST, PT_APPT_MAX, PT_APPT),
              "fu": (FOLLOW_UPS, FT_FIRST, FT_MAX, PT_FU)}


def team_row_of(tab_id: int) -> int:
    """The Team tab row a person tab belongs to (2 = the first person)."""
    for ids in (PERSON_TAB_IDS, FU_TAB_IDS):
        if tab_id in ids:
            return ids.index(tab_id) + 2
    return 2


def fu_tab_title(name: str) -> str:
    return "%s \u2013 Follow-Ups" % name


def _person_tabs(client, sid: str, team: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """[{"name", "title", "sheet_id", "kind"}] for each real person: their
    appointments tab ("appt") and their follow-ups tab ("fu")."""
    try:
        meta = client.spreadsheets().get(spreadsheetId=sid, fields="sheets.properties").execute()
    except Exception:                                    # noqa: BLE001
        return []
    titles = {s_["properties"]["sheetId"]: s_["properties"]["title"] for s_ in meta.get("sheets") or []}
    by_row = {p["row"]: p for p in team}
    out = []
    for ids, kind in ((PERSON_TAB_IDS, "appt"), (FU_TAB_IDS, "fu")):
        for i, tid in enumerate(ids):
            p = by_row.get(i + 2)
            if p is None or p.get("placeholder") or tid not in titles:
                continue
            out.append({"name": p["name"], "title": titles[tid], "sheet_id": tid, "kind": kind})
    return out


def _cell(v) -> str:
    return str(v).strip() if v is not None else ""


def _pt_blocks(client, sid: str, t: Dict[str, Any]) -> Dict[str, List[List[Any]]]:
    from app.services.volt.sheets_export import column
    tab, first, n, cols = KIND_BLOCK[t.get("kind", "appt")]
    width = len(cols)
    got = client.spreadsheets().values().get(
        spreadsheetId=sid, range=_rng(t["title"], "A%d:%s%d" % (first, column(width), first + n - 1))).execute()
    return {tab: [list(r) + [""] * (width - len(r)) for r in (got.get("values") or [])]}


def person_tabs_read(client, sid: str, tabs: List[Dict[str, Any]], staff: Dict[str, Dict[str, Dict[str, Any]]],
                     known: Dict[str, Any]) -> Dict[str, Any]:
    """Edits typed on person tabs -> merged into `staff` (as if typed on the
    main tab). Returns {"grids": {title: {tab: rows read}}, "edits": n, "copy": [(tab, rid, key, value)]}."""
    grids, copy, edits = {}, [], 0
    for t in tabs:
        try:
            blocks = _pt_blocks(client, sid, t)
        except Exception:                                # noqa: BLE001 - a person tab that can't be read changes nothing
            continue
        grids[t["title"]] = blocks
        for tab, cols, editable in ((APPOINTMENTS, PT_APPT, PT_APPT_EDIT), (FOLLOW_UPS, PT_FU, PT_FU_EDIT)):
            for r in blocks.get(tab) or []:
                vals = dict(zip([k for k, _ in cols], [_cell(v) for v in r]))
                rid = vals.get("ref")
                cur = staff[tab].get(rid) if rid else None
                rec = known.get(rid) if rid else None
                if cur is None:
                    continue
                snap = json.loads(rec.staff_json or "{}") if rec is not None else {}
                for k in editable:
                    mine, before, now_main = vals.get(k, ""), snap.get(k) or "", cur.get(k) or ""
                    if k == "done":                       # unticked == blank
                        mine, before, now_main = ("TRUE" if str(x).upper() == "TRUE" else ""
                                                  for x in (mine, before, now_main))
                    if mine != before and mine != now_main:
                        cur[k] = mine
                        copy.append((tab, rid, k, mine))
                        edits += 1
    return {"grids": grids, "edits": edits, "copy": copy}


BOARD_KEYS = {APPOINTMENTS: APPT_KEYS, FOLLOW_UPS: FU_KEYS}
BOARD_GIVE_UP = timedelta(days=2)


def board_edits_in(db, org: str, staff, now: datetime) -> List[tuple]:
    """Changes made on the team board -> merged into `staff` as if typed on the
    main tab; returns [(tab, row id, key, value)] for the sheet. Oldest first,
    so the newest change to a field wins. A row no longer on the sheet keeps
    its change for BOARD_GIVE_UP, then it is dropped (recorded, not lost)."""
    from app.models.program_models import HandoffPendingEdit as E
    todo = (db.query(E).filter(E.organization_id == org, E.applied_at.is_(None))
            .order_by(E.created_at).all())
    copy: List[tuple] = []
    for e in todo:
        if e.key not in BOARD_KEYS.get(e.tab, ()):
            e.applied_at, e.result = now, "unknown_field"
            continue
        cur = staff.get(e.tab, {}).get(e.row_id)
        if cur is None:
            if e.created_at and now - e.created_at > BOARD_GIVE_UP:
                e.applied_at, e.result = now, "row_gone"
            continue
        val = e.value or ""
        if e.key == "done":
            val = "TRUE" if str(val).upper() == "TRUE" else ""
        if (cur.get(e.key) or "") != val:
            cur[e.key] = val
            copy = [c for c in copy if not (c[0] == e.tab and c[1] == e.row_id and c[2] == e.key)]
            copy.append((e.tab, e.row_id, e.key, val))
        e.applied_at, e.result = now, "applied"
    db.flush()
    return copy


def copy_to_main(client, sid: str, staff, copy) -> int:
    """Write person-tab edits into the main tabs' team columns (by header)."""
    from app.services.volt.sheets_export import column
    if not copy:
        return 0
    labels = {APPOINTMENTS: dict(zip(KEYS[APPOINTMENTS], STAFF_COLUMNS[APPOINTMENTS])),
              FOLLOW_UPS: dict(zip(KEYS[FOLLOW_UPS], list(STAFF_COLUMNS[FOLLOW_UPS]) + list(FU_WORK_COLUMNS)))}
    data = []
    for tab in (APPOINTMENTS, FOLLOW_UPS):
        mine = [c for c in copy if c[0] == tab]
        if not mine:
            continue
        head = client.spreadsheets().values().get(spreadsheetId=sid, range=_rng(tab, "A1:AN1")).execute()
        header = [str(c).strip() for c in ((head.get("values") or [[]])[0])]
        for _tab, rid, k, v in mine:
            lbl = labels[tab].get(k)
            row = staff[tab].get(rid, {}).get("row")
            if lbl in header and row:
                val = (v == "TRUE") if k == "done" else v
                data.append({"range": _rng(tab, "%s%d" % (column(header.index(lbl) + 1), row)), "values": [[val]]})
    if data:
        client.spreadsheets().values().batchUpdate(
            spreadsheetId=sid, body={"valueInputOption": "USER_ENTERED", "data": data}).execute()
    return len(data)


def _team_touched(rec, editable) -> bool:
    """Has the team filled in anything on this row (not just an assignment)?"""
    if rec is None or not rec.staff_changed_at:
        return False
    snap = json.loads(rec.staff_json or "{}")
    return any((snap.get(k) or "") not in ("", "FALSE") for k in editable)


def person_tabs_write(client, sid: str, tabs: List[Dict[str, Any]], rows: Dict[str, List[List[Any]]],
                      staff, known, grids: Dict[str, Dict[str, List[List[Any]]]], today_serial: float) -> int:
    """Rewrite each person tab's two blocks from the main tabs - only rows that differ."""
    from app.services.volt.sheets_export import central_serial, column
    appt_rows = {str(r[0]): r for r in rows.get(APPOINTMENTS) or []}
    fu_rows = {str(r[0]): r for r in rows.get(FOLLOW_UPS) or []}
    writes = 0
    for t in tabs:
        name = t["name"]

        def mine(tab, rid):
            cur = staff[tab].get(rid) or {}
            rec = known.get(rid)
            return (cur.get("assigned") or (rec.assigned_to if rec is not None else "") or "") == name

        appts = []
        for rid, r in appt_rows.items():
            if not mine(APPOINTMENTS, rid) or str(r[10] or "").startswith("Cancel"):
                continue
            if isinstance(r[2], (int, float)) and r[2] < today_serial - 14:
                continue                                 # older than two weeks: off the working list
            cur, rec = staff[APPOINTMENTS].get(rid) or {}, known.get(rid)
            appts.append([rid, r[2], r[3], r[5], r[6], r[8], r[9], r[11], cur.get("outcome") or r[10],
                          cur.get("outcome", ""), cur.get("sold", ""), cur.get("sold_what", ""),
                          cur.get("link", ""), cur.get("notes", ""),
                          central_serial(rec.staff_changed_at) if _team_touched(rec, PT_APPT_EDIT) else ""])
        appts.sort(key=lambda x: (x[1] if isinstance(x[1], (int, float)) else 1e9,
                                  x[2] if isinstance(x[2], (int, float)) else 0))
        appts = appts[-PT_APPT_MAX:] if len(appts) > PT_APPT_MAX else appts
        fus = []
        for rid, r in fu_rows.items():
            cur, rec = staff[FOLLOW_UPS].get(rid) or {}, known.get(rid)
            if not mine(FOLLOW_UPS, rid) or str(r[8] or "") != "Open" or (cur.get("done") or "").upper() == "TRUE":
                continue
            last_ev = getattr(rec, "_last_event", None) if rec is not None else None
            if last_ev is None and rec is not None:
                last_ev = rec.staff_changed_at or rec.assigned_at
            vals = {"ref": rid, "due": r[6], "updated": central_serial(last_ev) if last_ev is not None else "",
                    "family": r[2], "phone": r[3], "why": r[5], "said": r[7], "done": False}
            vals.update({k: cur.get(k, "") for k in ("fu_date", "fu_result", "appt_date", "appt_time", "appt_how",
                                                     "appt_address", "notes")})
            fus.append([vals.get(k, "") for k, _ in PT_FU])
        _i_due, _i_upd = _FU_COL["due"], _FU_COL["updated"]
        # due now (no date, or today/past) first, then later dates; newest update first within each
        fus.sort(key=lambda x: (
            (x[_i_due] if isinstance(x[_i_due], (int, float)) and x[_i_due] > today_serial + 1 else 0),
            -(x[_i_upd] if isinstance(x[_i_upd], (int, float)) else 0), str(x[0])))
        fus = fus[:FT_MAX]
        old = grids.get(t["title"]) or {}
        data = []
        blocks = ((APPOINTMENTS, appts, PT_APPT_FIRST, PT_APPT, _PT_APPT_TEXT, len(PT_APPT) - 1),) \
            if t.get("kind", "appt") == "appt" else \
            ((FOLLOW_UPS, fus, FT_FIRST, PT_FU, _PT_FU_TEXT, _FU_COL["updated"]),)
        for tab, new_rows, first, cols, text_cols, stamp_col in blocks:
            was_rows = old.get(tab) or []
            width = len(cols)
            for i in range(max(len(new_rows), len(was_rows))):
                new = list(new_rows[i]) if i < len(new_rows) else [""] * width
                was = [_cell(v) for v in (was_rows[i] if i < len(was_rows) else [""] * width)]
                if i < len(was_rows) and _same_row(was, [_cell(v) for v in new], text_cols, stamp_col):
                    continue
                data.append({"range": _rng(t["title"], "A%d:%s%d" % (first + i, column(width), first + i)),
                             "values": [new]})
        if data:
            client.spreadsheets().values().batchUpdate(
                spreadsheetId=sid, body={"valueInputOption": "RAW", "data": data}).execute()
            writes += len(data)
    return writes


def _pt_header_rows(kind: str = "appt") -> Dict[int, List[str]]:
    """The banner / group / column-name rows of a person tab, by sheet row."""
    W = PT_WIDTH

    def row(vals):
        return (list(vals) + [""] * W)[:W]
    if kind == "fu":
        return {
            FT_BANNER: row(["MY FOLLOW-UPS   -   call the ones at the top first, then fill in the gold columns"]),
            FT_GROUPS: row(["", "WHEN, WHO & WHY"] + [""] * (PT_FU_INFO - 2) + ["YOUR UPDATE  (type here)"]),
            FT_HEAD: row([lbl for _, lbl in PT_FU]),
        }
    return {
        PT_APPT_BANNER: row(["MY APPOINTMENTS   -   after each visit, fill in the gold columns"]),
        PT_APPT_GROUPS: row(["", "THE APPOINTMENT"] + [""] * (PT_APPT_INFO - 2) + ["YOUR UPDATE  (type here)"]),
        PT_APPT_HEAD: row([lbl for _, lbl in PT_APPT]),
    }


def person_tab_headers_heal(client, sid: str, tabs: List[Dict[str, Any]]) -> int:
    """Put the header rows back if anything (a deploy switch-over, a person)
    changed them. One read per tab; writes only when they differ."""
    from app.services.volt.sheets_export import column
    fixed = 0
    for t in tabs:
        want = _pt_header_rows(t.get("kind", "appt"))
        try:
            got = client.spreadsheets().values().batchGet(
                spreadsheetId=sid, ranges=[_rng(t["title"], "A%d:%s%d" % (r, column(PT_WIDTH), r)) for r in want]
            ).execute()
        except Exception:                                # noqa: BLE001
            continue
        data = []
        for (r, row), vr in zip(want.items(), got.get("valueRanges") or []):
            have = [_cell(v) for v in ((vr.get("values") or [[]])[0])]
            have = (have + [""] * PT_WIDTH)[:PT_WIDTH]
            if have != row:
                data.append({"range": _rng(t["title"], "A%d:%s%d" % (r, column(PT_WIDTH), r)), "values": [row]})
        if data:
            client.spreadsheets().values().batchUpdate(
                spreadsheetId=sid, body={"valueInputOption": "RAW", "data": data}).execute()
            fixed += len(data)
    return fixed


_PT_APPT_TEXT = [i for i, (k, _) in enumerate(PT_APPT) if k not in ("date", "time", "updated")]
_PT_FU_TEXT = [i for i, (k, _) in enumerate(PT_FU) if k not in ("due", "updated", "done")]
_FU_COL = {k: i for i, (k, _) in enumerate(PT_FU)}


def _same_row(was: List[str], shown: List[str], text_cols: List[int], stamp_col: int) -> bool:
    """Equal in every text field (dates/times are written as numbers and read
    back formatted), and a time stamp blank/filled the same way."""
    def at(r, i):
        return r[i] if i < len(r) else ""
    return all(at(was, i) == at(shown, i) for i in text_cols) and bool(at(was, stamp_col)) == bool(at(shown, stamp_col))


def _fu_number_formats(sid: int) -> List[Dict]:
    """The date columns of a follow-ups tab, as their own requests."""
    def nf(col, typ, pattern):
        return {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": FT_FIRST - 1, "endRowIndex": FT_LAST_ROW,
                                         "startColumnIndex": col, "endColumnIndex": col + 1},
                               "cell": {"userEnteredFormat": {"numberFormat": {"type": typ, "pattern": pattern}}},
                               "fields": "userEnteredFormat.numberFormat"}}
    return [nf(_FU_COL["due"], "DATE", "ddd, mmm d"), nf(_FU_COL["updated"], "DATE_TIME", "m/d h:mm am/pm")]


HEAL_EVERY = timedelta(minutes=10)
_last_heal: Dict[str, datetime] = {}


def heal_date_formats(client, sid: str, now: Optional[datetime] = None) -> bool:
    """Owner, Nov 24: date cells on the board lost their format (rows deleted,
    pasted or sorted by hand) and showed 46356.375. Every 10 minutes the date
    columns get their format back - formats only, never a value."""
    now = now or datetime.utcnow()
    if sid in _last_heal and now - _last_heal[sid] < HEAL_EVERY:
        return False
    _last_heal[sid] = now
    reqs: List[Dict] = []
    for tid in FU_TAB_IDS:
        reqs += _fu_number_formats(tid)
    # Owner, Nov 25: a tab added later got the first person's name in its
    # title ("MY FOLLOW-UPS · NEOSO WALKER" on Johnny's tab) - the title
    # always points at the tab's own Team row.
    for ids, label in ((PERSON_TAB_IDS, "MY APPOINTMENTS"), (FU_TAB_IDS, "MY FOLLOW-UPS")):
        for tid in ids:
            reqs.append({"updateCells": {
                "range": {"sheetId": tid, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0,
                          "endColumnIndex": 1},
                "rows": [{"values": [{"userEnteredValue": {"formulaValue": '="%s  \u00b7  "&UPPER(Team!A%d)'
                                                           % (label, team_row_of(tid))}}]}],
                "fields": "userEnteredValue"}})

    def nf(sheet, c0, typ, pattern):
        return {"repeatCell": {"range": {"sheetId": sheet, "startRowIndex": 1, "endRowIndex": 5000,
                                         "startColumnIndex": c0, "endColumnIndex": c0 + 1},
                               "cell": {"userEnteredFormat": {"numberFormat": {"type": typ, "pattern": pattern}}},
                               "fields": "userEnteredFormat.numberFormat"}}
    lu = len(FU_COLUMNS) + len(STAFF_COLUMNS[FOLLOW_UPS]) + 1 + len(FU_WORK_COLUMNS)     # T: Last Update (CT)
    reqs += [nf(TAB_IDS[FOLLOW_UPS], 1, "DATE_TIME", "m/d/yyyy h:mm am/pm"),
             nf(TAB_IDS[FOLLOW_UPS], 6, "DATE", "ddd, mmm d, yyyy"),
             nf(TAB_IDS[FOLLOW_UPS], lu, "DATE_TIME", "m/d/yyyy h:mm am/pm")]
    meta = client.spreadsheets().get(spreadsheetId=sid, fields="sheets.properties.sheetId").execute()
    have = {s_["properties"]["sheetId"] for s_ in meta.get("sheets") or []}
    reqs = [q for q in reqs if next(iter(q.values()))["range"]["sheetId"] in have]
    if reqs:
        client.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": reqs}).execute()
    return True


def person_tab_layout_requests(ids=PERSON_TAB_IDS, kind: str = "appt") -> List[Dict]:
    """A person's tab look (applied once by sync): a title, one line on how to
    use the tab, then one block - MY APPOINTMENTS on the person tab, MY
    FOLLOW-UPS on their follow-ups tab - with a banner, a 'who & when' group
    and a gold 'your update' group."""
    INK, SAGE, SAGE_D, CREAM, STRIPE = "#17352C", "#E3ECE7", "#C9D9D0", "#FBF7EE", "#F3F6F4"
    r: List[Dict] = []

    def g(sid, r0, r1, c0, c1):
        return {"sheetId": sid, "startRowIndex": r0 - 1, "endRowIndex": r1, "startColumnIndex": c0,
                "endColumnIndex": c1}

    def sv(v):
        return {"userEnteredValue": {"stringValue": v}}

    def fmt(sid, r0, r1, c0, c1, **k):
        f: Dict[str, Any] = {}
        tf: Dict[str, Any] = {}
        if "bg" in k:
            f["backgroundColor"] = _rgb(k["bg"])
        if "fg" in k:
            tf["foregroundColor"] = _rgb(k["fg"])
        if "bold" in k:
            tf["bold"] = k["bold"]
        if "size" in k:
            tf["fontSize"] = k["size"]
        if tf:
            f["textFormat"] = tf
        if "align" in k:
            f["horizontalAlignment"] = k["align"]
        if "valign" in k:
            f["verticalAlignment"] = k["valign"]
        if "wrap" in k:
            f["wrapStrategy"] = k["wrap"]
        if "num" in k:
            f["numberFormat"] = {"type": k["num"][0], "pattern": k["num"][1]}
        if "pad" in k:
            f["padding"] = {"left": k["pad"], "right": k["pad"], "top": 2, "bottom": 2}
        fields = ",".join("userEnteredFormat.%s" % x for x in f)
        return {"repeatCell": {"range": g(sid, r0, r1, c0, c1), "cell": {"userEnteredFormat": f}, "fields": fields}}

    def merge(sid, r0, r1, c0, c1):
        return {"mergeCells": {"range": g(sid, r0, r1, c0, c1), "mergeType": "MERGE_ALL"}}

    def put(sid, row, col, values):
        return {"updateCells": {"range": g(sid, row, row, col, col + len(values)),
                                "rows": [{"values": [sv(v) if v is not None else {} for v in values]}],
                                "fields": "userEnteredValue"}}

    def lst(sid, r0, r1, c, vals):
        return {"setDataValidation": {"range": g(sid, r0, r1, c, c + 1), "rule": {"condition": {
            "type": "ONE_OF_LIST", "values": [{"userEnteredValue": v} for v in vals]},
            "strict": False, "showCustomUi": True}}}

    def height(sid, r0, r1, px):
        return {"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "ROWS", "startIndex": r0 - 1,
                                                        "endIndex": r1}, "properties": {"pixelSize": px},
                                              "fields": "pixelSize"}}

    widths = [24, 112, 78, 160, 115, 110, 160, 300, 110, 120, 70, 170, 170, 240, 130]
    W = PT_WIDTH
    appt = kind == "appt"

    def fu_look(sid: int, team_row: int) -> List[Dict]:
        """Owner, Nov 24 9:14 PM: a follow-ups tab that reads at a glance -
        Follow Up On, Last Update and Family first, the headers frozen, wide
        name/phone, Why and What They Said wrapped."""
        W, n, info = len(PT_FU), len(PT_FU), PT_FU_INFO
        F0, f_end = FT_FIRST, FT_LAST_ROW
        c = _FU_COL
        q: List[Dict] = [
            {"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {
                "rowCount": 500, "columnCount": 30}}, "fields": "gridProperties.rowCount,gridProperties.columnCount"}},
            {"unmergeCells": {"range": g(sid, 1, 500, 0, 30)}},
            {"setDataValidation": {"range": g(sid, 1, 500, 0, 30)}},
            {"repeatCell": {"range": g(sid, 1, 500, 0, 30), "cell": {},
                            "fields": "userEnteredValue,userEnteredFormat"}},
            {"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {
                "rowCount": f_end + 5, "columnCount": W, "frozenRowCount": FT_HEAD, "frozenColumnCount": 0,
                "hideGridlines": True}},
                # no frozen columns: Sheets refuses a merge across a frozen column
                # (the title and banner rows); the dates are the first columns anyway
                "fields": "gridProperties.rowCount,gridProperties.columnCount,gridProperties.frozenRowCount,"
                          "gridProperties.frozenColumnCount,gridProperties.hideGridlines"}},
            {"updateCells": {"range": g(sid, PT_TITLE, PT_TITLE, 0, 1), "rows": [{"values": [
                {"userEnteredValue": {"formulaValue": '="MY FOLLOW-UPS  ·  "&UPPER(Team!A%d)' % team_row}}]}],
                "fields": "userEnteredValue"}},
            put(sid, PT_HELP, 0, [
                "Call the families at the top first (due now).  Type only in the GOLD columns - it saves to the "
                "main tabs and the app by itself within a couple of minutes.  To book, fill in Appt Date + Appt "
                "Time - the family gets their confirmation.  Replies sent from the app fill in Followed Up On by "
                "themselves."]),
            merge(sid, PT_TITLE, PT_TITLE, 0, W), merge(sid, PT_HELP, PT_HELP, 0, W),
            fmt(sid, PT_TITLE, PT_TITLE, 0, W, bg=INK, fg="#FFFFFF", bold=True, size=16, valign="MIDDLE", pad=10),
            fmt(sid, PT_HELP, PT_HELP, 0, W, bg=CREAM, fg=INK, size=10, valign="MIDDLE", wrap="WRAP", pad=10),
            height(sid, PT_TITLE, PT_TITLE, 44), height(sid, PT_HELP, PT_HELP, 40), height(sid, 3, 3, 26),
            {"updateCells": {"range": g(sid, 3, 3, 1, 2), "rows": [{"values": [{"userEnteredValue": {"formulaValue": (
                '=IF(COUNTA(D{0}:D{1})=0,"","   "&COUNTA(D{0}:D{1})&" follow-ups      ·      "'
                '&(COUNTIFS(D{0}:D{1},"<>",B{0}:B{1},"<="&TODAY())+COUNTIFS(D{0}:D{1},"<>",B{0}:B{1},""))'
                '&" due now      ·      "&COUNTIFS(C{0}:C{1},">="&TODAY())&" updated today")').format(F0, f_end)}}]}],
                "fields": "userEnteredValue"}},
            merge(sid, 3, 3, 1, W), fmt(sid, 3, 3, 1, W, fg=INK, bold=True, size=11, valign="MIDDLE"),
            # banner, groups, headers
            put(sid, FT_BANNER, 0, ["MY FOLLOW-UPS   -   call the ones at the top first, then fill in the gold columns"]),
            merge(sid, FT_BANNER, FT_BANNER, 0, W),
            fmt(sid, FT_BANNER, FT_BANNER, 0, W, bg=GREEN, fg="#FFFFFF", bold=True, size=13, valign="MIDDLE", pad=10),
            height(sid, FT_BANNER, FT_BANNER, 36),
            put(sid, FT_GROUPS, 1, ["WHEN, WHO & WHY"]), merge(sid, FT_GROUPS, FT_GROUPS, 1, info),
            put(sid, FT_GROUPS, info, ["YOUR UPDATE  (type here)"]), merge(sid, FT_GROUPS, FT_GROUPS, info, n),
            fmt(sid, FT_GROUPS, FT_GROUPS, 0, info, bg=SAGE_D, fg=INK, bold=True, size=9, align="CENTER"),
            fmt(sid, FT_GROUPS, FT_GROUPS, info, n, bg=BRASS, fg="#FFFFFF", bold=True, size=9, align="CENTER"),
            put(sid, FT_HEAD, 0, [lbl for _, lbl in PT_FU]),
            fmt(sid, FT_HEAD, FT_HEAD, 0, info, bg=SAGE, fg=INK, bold=True, size=10, valign="MIDDLE", wrap="WRAP"),
            fmt(sid, FT_HEAD, FT_HEAD, info, n, bg=GOLD, fg=INK, bold=True, size=10, valign="MIDDLE", wrap="WRAP"),
            height(sid, FT_HEAD, FT_HEAD, 32),
            # the rows
            fmt(sid, F0, f_end, 0, n, fg=TEXT, size=10, valign="MIDDLE", wrap="CLIP"),
            fmt(sid, F0, f_end, c["why"], c["said"] + 1, wrap="WRAP"),
            fmt(sid, F0, f_end, c["notes"], c["notes"] + 1, wrap="WRAP"),
            fmt(sid, F0, f_end, info, n, bg=CREAM),
            {"addBanding": {"bandedRange": {"range": g(sid, F0, f_end, 0, info), "rowProperties": {
                "firstBandColorStyle": {"rgbColor": _rgb("#FFFFFF")},
                "secondBandColorStyle": {"rgbColor": _rgb(STRIPE)}}}}},
            {"updateBorders": {"range": g(sid, FT_HEAD, f_end, 0, n),
                               "innerHorizontal": {"style": "SOLID", "colorStyle": {"rgbColor": _rgb(LINE)}},
                               "bottom": {"style": "SOLID", "colorStyle": {"rgbColor": _rgb(LINE)}}}},
            {"updateBorders": {"range": g(sid, FT_GROUPS, f_end, info, n),
                               "left": {"style": "SOLID_MEDIUM", "colorStyle": {"rgbColor": _rgb(BRASS)}},
                               "right": {"style": "SOLID_MEDIUM", "colorStyle": {"rgbColor": _rgb(BRASS)}}}},
            fmt(sid, F0, f_end, c["due"], c["due"] + 1, bold=True),
            fmt(sid, F0, f_end, c["updated"], c["updated"] + 1, fg=GREY, size=9),
            fmt(sid, F0, f_end, c["family"], c["family"] + 1, bold=True),
            {"setDataValidation": {"range": g(sid, F0, f_end, c["done"], c["done"] + 1),
                                   "rule": {"condition": {"type": "BOOLEAN"}}}},
            fmt(sid, F0, f_end, c["done"], c["done"] + 1, align="CENTER"),
            {"setDataValidation": {"range": g(sid, F0, f_end, c["fu_date"], c["fu_date"] + 1), "rule": {"condition": {
                "type": "DATE_IS_VALID"}, "strict": False, "showCustomUi": True}}},
            lst(sid, F0, f_end, c["fu_result"], list(WHAT_HAPPENED)),
            {"setDataValidation": {"range": g(sid, F0, f_end, c["appt_date"], c["appt_date"] + 1), "rule": {
                "condition": {"type": "DATE_IS_VALID"}, "strict": False, "showCustomUi": True}}},
            lst(sid, F0, f_end, c["appt_time"], list(TIME_CHOICES)),
            lst(sid, F0, f_end, c["appt_how"], HOW_PICK),
            _when(sid, "B%d:G%d" % (F0, f_end), '=AND($A%d<>"",OR($B%d="",$B%d<TODAY()+1))' % (F0, F0, F0), "#FCEBD9"),
        ]
        q += _fu_number_formats(sid)
        widths = [24, 105, 115, 175, 120, 260, 300, 60, 135, 175, 105, 90, 110, 180, 230]
        for i, px in enumerate(widths[:W]):
            q.append({"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS",
                                                              "startIndex": i, "endIndex": i + 1},
                                                    "properties": {"pixelSize": px}, "fields": "pixelSize"}})
        q.append({"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": 0,
                                                          "endIndex": 1}, "properties": {"hiddenByUser": True},
                                                "fields": "hiddenByUser"}})
        return q

    if not appt:
        for sid in ids:
            r += fu_look(sid, team_row_of(sid))
        return r
    for sid in ids:
        a_end = PT_APPT_LAST_ROW
        f_end = FT_LAST_ROW
        last_row = PT_APPT_LAST_ROW if appt else FT_LAST_ROW
        # clear the old layout first (it may be wider and taller - or smaller than
        # the range cleared, so make room first), then size the tab
        r.append({"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {
            "rowCount": 500, "columnCount": 30}}, "fields": "gridProperties.rowCount,gridProperties.columnCount"}})
        r.append({"unmergeCells": {"range": g(sid, 1, 500, 0, 30)}})
        r.append({"setDataValidation": {"range": g(sid, 3, 500, 0, 30)}})
        r.append({"repeatCell": {"range": g(sid, 1, 500, 0, 30), "cell": {},
                                 "fields": "userEnteredValue,userEnteredFormat"}})
        r.append({"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {
            "rowCount": last_row + 5, "columnCount": W, "frozenRowCount": 2, "hideGridlines": True}},
            "fields": "gridProperties.rowCount,gridProperties.columnCount,gridProperties.frozenRowCount,"
                      "gridProperties.hideGridlines"}})
        # title + how to use
        team_row = team_row_of(sid)
        r.append({"updateCells": {"range": g(sid, PT_TITLE, PT_TITLE, 0, 1), "rows": [{"values": [
            {"userEnteredValue": {"formulaValue": '="%s  ·  "&UPPER(Team!A%d)'
                                  % ("MY APPOINTMENTS" if appt else "MY FOLLOW-UPS", team_row)}}]}],
            "fields": "userEnteredValue"}})
        r.append(put(sid, PT_HELP, 0, [
            "How this tab works:  1) Work the families below.  2) Type only in the GOLD columns.  "
            "3) It saves to the main tabs and to the app by itself within a couple of minutes.  "
            + ("Your follow-ups are on your own Follow-Ups tab." if appt else
               "To book a follow-up, fill in Appt Date + Appt Time - the family gets their confirmation.")]))
        r += [merge(sid, PT_TITLE, PT_TITLE, 0, W), merge(sid, PT_HELP, PT_HELP, 0, W),
              fmt(sid, PT_TITLE, PT_TITLE, 0, W, bg=INK, fg="#FFFFFF", bold=True, size=16, valign="MIDDLE", pad=10),
              fmt(sid, PT_HELP, PT_HELP, 0, W, bg=CREAM, fg=INK, size=10, valign="MIDDLE", wrap="WRAP", pad=10),
              height(sid, PT_TITLE, PT_TITLE, 44), height(sid, PT_HELP, PT_HELP, 40), height(sid, 3, 3, 26)]
        # the counts line under the title
        if appt:
            a0, a1 = PT_APPT_FIRST, PT_APPT_LAST_ROW
            summary = ('=IF(COUNTA(D{0}:D{1})=0,"","   "&COUNTA(D{0}:D{1})&" appointments      ·      "'
                       '&COUNTIFS(B{0}:B{1},TODAY())&" today")').format(a0, a1)
        else:
            f0, f1 = FT_FIRST, FT_LAST_ROW
            summary = ('=IF(COUNTA(C{0}:C{1})=0,"","   "&COUNTA(C{0}:C{1})&" follow-ups to call      ·      "'
                       '&(COUNTIFS(C{0}:C{1},"<>",B{0}:B{1},"<="&TODAY())+COUNTIFS(C{0}:C{1},"<>",B{0}:B{1},""))'
                       '&" due now")').format(f0, f1)
        r.append({"updateCells": {"range": g(sid, 3, 3, 1, 2), "rows": [{"values": [
            {"userEnteredValue": {"formulaValue": summary}}]}], "fields": "userEnteredValue"}})
        r += [merge(sid, 3, 3, 1, W), fmt(sid, 3, 3, 1, W, fg=INK, bold=True, size=11, valign="MIDDLE")]
        blocks = (((PT_APPT_BANNER, PT_APPT_GROUPS, PT_APPT_HEAD, PT_APPT_FIRST, a_end, PT_APPT, PT_APPT_INFO,
                    "MY APPOINTMENTS   -   after each visit, fill in the gold columns",
                    "THE APPOINTMENT", "YOUR UPDATE  (type here)"),) if appt else
                  ((FT_BANNER, FT_GROUPS, FT_HEAD, FT_FIRST, f_end, PT_FU, PT_FU_INFO,
                    "MY FOLLOW-UPS   -   call these families, then fill in the gold columns",
                    "WHO TO CALL & WHY", "YOUR UPDATE  (type here)"),))
        for (banner, groups, head, first, last, cols, info, title, info_lbl, upd_lbl) in blocks:
            n = len(cols)
            r += [put(sid, banner, 0, [title]), merge(sid, banner, banner, 0, W),
                  fmt(sid, banner, banner, 0, W, bg=GREEN, fg="#FFFFFF", bold=True, size=13, valign="MIDDLE", pad=10),
                  height(sid, banner, banner, 36),
                  put(sid, groups, 1, [info_lbl]), merge(sid, groups, groups, 1, info),
                  put(sid, groups, info, [upd_lbl]), merge(sid, groups, groups, info, n - 1),
                  fmt(sid, groups, groups, 0, info, bg=SAGE_D, fg=INK, bold=True, size=9, align="CENTER"),
                  fmt(sid, groups, groups, info, n - 1, bg=BRASS, fg="#FFFFFF", bold=True, size=9, align="CENTER"),
                  fmt(sid, groups, groups, n - 1, n, bg=SAGE_D),
                  put(sid, head, 0, [lbl for _, lbl in cols]),
                  fmt(sid, head, head, 0, info, bg=SAGE, fg=INK, bold=True, size=10, valign="MIDDLE", wrap="WRAP"),
                  fmt(sid, head, head, info, n - 1, bg=GOLD, fg=INK, bold=True, size=10, valign="MIDDLE",
                      wrap="WRAP"),
                  fmt(sid, head, head, n - 1, n, bg=SAGE, fg=INK, bold=True, size=10, valign="MIDDLE", wrap="WRAP"),
                  height(sid, head, head, 32),
                  fmt(sid, first, last, 0, n, fg=TEXT, size=10, valign="MIDDLE", wrap="CLIP"),
                  fmt(sid, first, last, info, n - 1, bg=CREAM),
                  {"addBanding": {"bandedRange": {"range": g(sid, first, last, 0, info), "rowProperties": {
                      "firstBandColorStyle": {"rgbColor": _rgb("#FFFFFF")},
                      "secondBandColorStyle": {"rgbColor": _rgb(STRIPE)}}}}},
                  height(sid, first, last, 30),
                  {"updateBorders": {"range": g(sid, head, last, 0, n),
                                     "innerHorizontal": {"style": "SOLID", "colorStyle": {"rgbColor": _rgb(LINE)}},
                                     "bottom": {"style": "SOLID", "colorStyle": {"rgbColor": _rgb(LINE)}}}},
                  {"updateBorders": {"range": g(sid, groups, last, info, n - 1),
                                     "left": {"style": "SOLID_MEDIUM", "colorStyle": {"rgbColor": _rgb(BRASS)}},
                                     "right": {"style": "SOLID_MEDIUM", "colorStyle": {"rgbColor": _rgb(BRASS)}}}},
                  fmt(sid, first, last, n - 1, n, fg=GREY, size=9, num=("DATE_TIME", "m/d h:mm am/pm"))]
        # number formats and pickers
        if appt:
            r += [fmt(sid, PT_APPT_FIRST, a_end, 1, 2, num=("DATE", "ddd, mmm d"), bold=True),
                  fmt(sid, PT_APPT_FIRST, a_end, 2, 3, num=("TIME", "h:mm am/pm"), bold=True),
                  fmt(sid, PT_APPT_FIRST, a_end, 3, 4, bold=True),
                  fmt(sid, PT_APPT_FIRST, a_end, 7, 8, wrap="WRAP", size=9),     # notes / what they said
                  lst(sid, PT_APPT_FIRST, a_end, 9, ["Showed", "No-Show", "Rescheduled", "Cancelled"]),
                  lst(sid, PT_APPT_FIRST, a_end, 10, list(SOLD_CHOICES)),
                  _when(sid, "K%d:K%d" % (PT_APPT_FIRST, a_end), '=$K%d="Yes"' % PT_APPT_FIRST, "#DCEFE3"),
                  _when(sid, "J%d:J%d" % (PT_APPT_FIRST, a_end), '=$J%d="Showed"' % PT_APPT_FIRST, "#DCEFE3"),
                  _when(sid, "J%d:J%d" % (PT_APPT_FIRST, a_end), '=$J%d="No-Show"' % PT_APPT_FIRST, "#FBE3DF")]
        else:
            F0 = FT_FIRST
            r += [fmt(sid, F0, f_end, 1, 2, num=("DATE", "ddd, mmm d")),
                  fmt(sid, F0, f_end, 2, 3, bold=True),
                  {"setDataValidation": {"range": g(sid, F0, f_end, 6, 7), "rule": {"condition": {"type": "BOOLEAN"}}}},
                  fmt(sid, F0, f_end, 6, 7, align="CENTER"),
                  {"setDataValidation": {"range": g(sid, F0, f_end, 7, 8), "rule": {"condition": {
                      "type": "DATE_IS_VALID"}, "strict": False, "showCustomUi": True}}},
                  lst(sid, F0, f_end, 8, list(WHAT_HAPPENED)),
                  {"setDataValidation": {"range": g(sid, F0, f_end, 9, 10), "rule": {"condition": {
                      "type": "DATE_IS_VALID"}, "strict": False, "showCustomUi": True}}},
                  lst(sid, F0, f_end, 10, list(TIME_CHOICES)),
                  lst(sid, F0, f_end, 11, HOW_PICK),
                  _when(sid, "B%d:F%d" % (F0, f_end), '=AND($A%d<>"",OR($B%d="",$B%d<TODAY()+1))' % (F0, F0, F0),
                        "#FCEBD9"),
                  # Last Update as a date and time, set on its own (Nov 24: on the
                  # live Follow-Ups tabs it was missing and showed 46350.79)
                  {"repeatCell": {"range": g(sid, F0, f_end, 14, 15), "cell": {"userEnteredFormat": {
                      "numberFormat": {"type": "DATE_TIME", "pattern": "m/d h:mm am/pm"}}},
                      "fields": "userEnteredFormat.numberFormat"}}]
        for c, px in enumerate(widths[:W]):
            r.append({"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS",
                                                              "startIndex": c, "endIndex": c + 1},
                                                    "properties": {"pixelSize": px}, "fields": "pixelSize"}})
        r.append({"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": 0,
                                                          "endIndex": 1}, "properties": {"hiddenByUser": True},
                                                "fields": "hiddenByUser"}})
    return r


def _strip_old_look(client, sid: str, ids) -> List[Dict]:
    """Requests removing row striping and color rules left on these tabs by an
    earlier layout (Google refuses to stripe a range twice)."""
    first: List[Dict] = []
    try:
        meta = client.spreadsheets().get(
            spreadsheetId=sid, fields="sheets(properties.sheetId,bandedRanges.bandedRangeId,conditionalFormats)"
        ).execute()
        for sh in meta.get("sheets") or []:
            sheet_id = (sh.get("properties") or {}).get("sheetId")
            if sheet_id not in ids:
                continue
            first += [{"deleteBanding": {"bandedRangeId": b["bandedRangeId"]}} for b in sh.get("bandedRanges") or []]
            first += [{"deleteConditionalFormatRule": {"sheetId": sheet_id, "index": 0}}
                      for _ in sh.get("conditionalFormats") or []]
    except Exception:                                    # noqa: BLE001 - a fresh sheet has nothing to remove
        first = []
    return first


def ensure_person_tabs(db: Session, prog, client, sid: str, team: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Each person's appointments tab, named after them. Owner, Nov 25: a
    person added on the Team tab got a Follow-Ups tab but their appointments
    tab stayed hidden as "Person 4" - now the tab is created (if needed),
    renamed and shown when the name is typed, and hidden again as "Person N"
    when the row is cleared. A tab gets its look once."""
    meta = client.spreadsheets().get(spreadsheetId=sid, fields="sheets.properties").execute()
    props = {s_["properties"]["sheetId"]: s_["properties"] for s_ in meta.get("sheets") or []}
    team = read_team(client, sid) if team is None else team
    by_row = {p["row"]: p for p in team}
    reqs, adds, made = [], [], []
    for i, tid in enumerate(PERSON_TAB_IDS):
        p = by_row.get(i + 2)
        real = p is not None and not p.get("placeholder")
        title = p["name"] if real else "Person %d" % (i + 1)
        if tid not in props:
            if not real:
                continue                                 # made when that person is added
            pr: Dict[str, Any] = {"sheetId": tid, "title": title, "hidden": False,
                                  "tabColorStyle": {"rgbColor": _rgb(GOLD)}}
            if i:                                        # right after the person before them
                prev = props.get(FU_TAB_IDS[i - 1]) or props.get(PERSON_TAB_IDS[i - 1]) or {}
                if prev.get("index") is not None:
                    pr["index"] = prev["index"] + 1
            adds.append({"addSheet": {"properties": pr}})
            made.append(tid)
            continue
        cur = props[tid]
        if cur.get("title") != title or bool(cur.get("hidden")) != (not real):
            reqs.append({"updateSheetProperties": {"properties": {"sheetId": tid, "title": title,
                                                                  "hidden": not real},
                                                   "fields": "title,hidden"}})
    adds.sort(key=lambda a: -(a["addSheet"]["properties"].get("index") or 0))
    reqs = adds + reqs
    if reqs:
        client.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": reqs}).execute()
    pol = json.loads(prog.booking_policy_json or "{}")
    cfg = pol.get("handoff_sheet") or {}
    # v7 (owner, Nov 25): a "Notes / What They Said" column - every tab gets the new look once.
    done = set(cfg.get("person_tabs_v7_ids") or [])
    todo = [tid for tid in PERSON_TAB_IDS if tid not in done and (tid in props or tid in made)]
    if todo:
        client.spreadsheets().batchUpdate(spreadsheetId=sid, body={
            "requests": _strip_old_look(client, sid, todo) + person_tab_layout_requests(todo)}).execute()
    if todo or cfg.get("person_tabs_v7_ids") is None:
        cfg["person_tabs_v7_ids"] = sorted(done | set(todo))
        pol["handoff_sheet"] = cfg
        prog.booking_policy_json = json.dumps(pol)
        db.commit()
    return {"created": made, "laid_out": todo, "changed": len(reqs)}


def ensure_fu_tabs(db: Session, prog, client, sid: str, team: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Each person's own Follow-Ups tab ("Kitty Arnold - Follow-Ups"), right
    after their appointments tab: created once, renamed when the Team name
    changes, hidden while the Team row is a placeholder."""
    meta = client.spreadsheets().get(spreadsheetId=sid, fields="sheets.properties").execute()
    props = {s_["properties"]["sheetId"]: s_["properties"] for s_ in meta.get("sheets") or []}
    by_row = {p["row"]: p for p in team}
    reqs, made, laid = [], [], []
    adds = []
    for i, tid in enumerate(FU_TAB_IDS):
        p = by_row.get(i + 2)
        real = p is not None and not p.get("placeholder")
        title = fu_tab_title(p["name"] if real else "Person %d" % (i + 1))
        hidden = not real
        if tid not in props:
            if not real:
                continue                                 # made when that person is added
            idx = (props.get(PERSON_TAB_IDS[i]) or {}).get("index")
            pr: Dict[str, Any] = {"sheetId": tid, "title": title, "hidden": False}
            if idx is not None:
                pr["index"] = idx + 1
            adds.append({"addSheet": {"properties": pr}})
            made.append(tid)
            continue
        cur = props[tid]
        if cur.get("title") != title or bool(cur.get("hidden")) != hidden:
            reqs.append({"updateSheetProperties": {"properties": {"sheetId": tid, "title": title, "hidden": hidden},
                                                   "fields": "title,hidden"}})
    # added highest position first, so an earlier insert never shifts a later one
    adds.sort(key=lambda a: -(a["addSheet"]["properties"].get("index") or 0))
    reqs = adds + reqs
    if reqs:
        client.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": reqs}).execute()
    pol = json.loads(prog.booking_policy_json or "{}")
    cfg = pol.get("handoff_sheet") or {}
    done = set(cfg.get("fu_tabs_laid_out_v2") or [])
    todo = [tid for tid in FU_TAB_IDS if tid not in done and (tid in props or tid in made)]
    if todo:
        client.spreadsheets().batchUpdate(spreadsheetId=sid, body={
            "requests": _strip_old_look(client, sid, todo) + person_tab_layout_requests(todo, "fu")}).execute()
        cfg["fu_tabs_laid_out_v2"] = sorted(done | set(todo))
        pol["handoff_sheet"] = cfg
        prog.booking_policy_json = json.dumps(pol)
        db.commit()
        laid = todo
    return {"created": made, "laid_out": laid, "changed": len(reqs)}


def _app_followups(db, client, sid: str, fu_staff: Dict[str, Dict[str, Any]], known) -> Dict[str, int]:
    """A person's reply sent from the app -> Followed Up On / What Happened;
    the newest app event -> Last Update (CT). Reconciling with the team:
      * Followed Up On / What Happened are the team's (gold) columns. The app
        fills them from its newest successful reply when they are blank, when
        they hold an earlier app entry ("Replied by ..."), or when the reply is
        newer than the team's last change to the row. Anything the team typed
        after the reply stays.
      * Last Update moves forward only, to the newest of: the team's last
        change, the reply, the family's newest message.
    What the app wrote is recorded as the row's known state, so the next pass
    never reads it back as a team edit (no notes, no booking, no done)."""
    from app.models.program_models import ProgramConversation
    from app.services.volt.sheets_export import central_serial, column
    rids = [r for r in fu_staff if str(r).startswith("FU-")]
    if not rids:
        return {"followed_up": 0, "last_update": 0}
    convs = []
    for i in range(0, len(rids), 500):
        convs += db.query(ProgramConversation).filter(
            ProgramConversation.id.in_([r[3:] for r in rids[i:i + 500]])).all()
    events = followup_events(db, convs)
    head = client.spreadsheets().values().get(spreadsheetId=sid, range=_rng(FOLLOW_UPS, "A1:AN1")).execute()
    header = [str(c).strip() for c in ((head.get("values") or [[]])[0])]
    labels = dict(zip(FU_WORK_KEYS, FU_WORK_COLUMNS))
    cols = {k: header.index(lbl) for k, lbl in labels.items() if lbl in header}
    lu = header.index(LAST_UPDATE_HEADER) if LAST_UPDATE_HEADER in header else None
    data, done, moved = [], 0, 0
    for rid in rids:
        ev, cur, rec = events.get(rid[3:]), fu_staff[rid], known.get(rid)
        if ev is None or rec is None:
            continue
        snap = json.loads(rec.staff_json or "{}")
        team_at = rec.staff_changed_at
        last = max([t for t in (team_at, ev["human_at"], ev["family_at"], ev.get("note_at")) if t is not None]
                   or [None])
        rec._last_event = last                           # read by the person tabs
        if ev["human_at"] is not None and len(cols) == 2:
            want = {"fu_date": _ct_text(ev["human_at"]), "fu_result": _replied_text(ev)}
            have = {k: (cur.get(k) or "") for k in FU_WORK_KEYS}
            app_entry = have["fu_result"].startswith(REPLIED_PREFIX) or not (have["fu_date"] or have["fu_result"])
            team_newer = team_at is not None and team_at > ev["human_at"]
            if have != want and (not team_newer or (app_entry and have["fu_result"].startswith(REPLIED_PREFIX))):
                for k, v in want.items():
                    data.append({"range": _rng(FOLLOW_UPS, "%s%d" % (column(cols[k] + 1), cur["row"])),
                                 "values": [[v]]})
                    cur[k] = v
                    snap[k] = v
                done += 1
        if lu is not None and last is not None and (team_at is None or last > team_at) \
                and snap.get("_last_update") != last.isoformat():
            data.append({"range": _rng(FOLLOW_UPS, "%s%d" % (column(lu + 1), cur["row"])),
                         "values": [[central_serial(last)]]})
            snap["_last_update"] = last.isoformat()
            moved += 1
        rec.staff_json = json.dumps(snap)
    if data:
        for i in range(0, len(data), 500):
            client.spreadsheets().values().batchUpdate(
                spreadsheetId=sid, body={"valueInputOption": "RAW", "data": data[i:i + 500]}).execute()
    db.flush()
    return {"followed_up": done, "last_update": moved}


def team_pass(db: Session, prog, client, sid: str, rows: Dict[str, List[List[Any]]],
              now: Optional[datetime] = None) -> Dict[str, Any]:
    """Read the team's columns back into the app, pause anyone with no
    updates in a week (alerting the owners once), and assign new families
    round robin to the active people."""
    from app.models.program_models import HandoffSheetRow as R
    from app.services.volt.sheets_export import column, central_serial
    now = now or datetime.utcnow()
    org = prog.organization_id
    today_serial = int(central_serial(now))
    out: Dict[str, Any] = {"changes": 0, "assigned": 0, "paused": []}
    team = read_team(client, sid)
    try:                                                 # the team board's people follow the Team tab
        from app.services.programs import team_board as TB
        TB.sync_members(db, org, team, now)
    except Exception as exc:                             # noqa: BLE001
        db.rollback()
        log.warning("team board members not synced: %s", str(exc)[:300])
    known = {r.row_id: r for r in db.query(R).filter(R.organization_id == org).all()}
    staff = {tab: read_staff(client, sid, tab) for tab in (APPOINTMENTS, FOLLOW_UPS)}
    # 0. what people typed on their own tabs -> as if typed on the main tabs
    ptabs = _person_tabs(client, sid, team)
    pt = person_tabs_read(client, sid, ptabs, staff, known) if ptabs else {"grids": {}, "edits": 0, "copy": []}
    if pt["copy"]:
        out["person_tab_edits"] = copy_to_main(client, sid, staff, pt["copy"])
    # 0b. changes made on the team board (owner, Nov 25) - the same road
    try:
        board = board_edits_in(db, org, staff, now)
        if board:
            out["board_edits"] = copy_to_main(client, sid, staff, board)
    except Exception as exc:                             # noqa: BLE001 - the sheet's own edits still go in
        db.rollback()
        log.warning("team board edits not applied: %s", str(exc)[:300])
    booking_results: Dict[str, str] = {}
    stamped: List[tuple] = []                            # (tab, sheet row) the team changed this pass
    # 1. what the team typed -> the app
    for tab, by_id in staff.items():
        for rid, cur in by_id.items():
            vals = {k: cur.get(k, "") for k in KEYS[tab]}
            rec = known.get(rid)
            if rec is None:
                rec = R(organization_id=org, row_id=rid, tab=tab, lead_id=_lead_for(db, tab, rid),
                        staff_json=json.dumps({k: "" for k in KEYS[tab]}))
                db.add(rec)
                known[rid] = rec
            old = json.loads(rec.staff_json or "{}")
            if any((old.get(k) or "") != (vals.get(k) or "") for k in KEYS[tab]):
                if _apply(db, prog, tab, rid, rec.lead_id, old, vals, now, results=booking_results):
                    out["changes"] += 1
                rec.staff_json = json.dumps(vals)
                rec.staff_changed_at = now
                stamped.append((tab, cur["row"]))
                if vals.get("assigned") != rec.assigned_to:
                    rec.assigned_to, rec.auto_assigned = vals.get("assigned") or None, False
                    rec.assigned_at = now if vals.get("assigned") else None
    db.flush()
    # 1b. what happened in the app -> the follow-up rows (owner, Nov 24)
    try:
        out["app_followups"] = _app_followups(db, client, sid, staff[FOLLOW_UPS], known)
    except Exception as exc:                             # noqa: BLE001 - the rest of the pass still runs
        from app.services.volt.sheets_export import categorize
        log.warning("follow-up app events not written: %s", categorize(exc))
    # 2. who is open for new families: active, a real name, not paused
    cfg = _cfg(prog)
    paused = dict(cfg.get("paused") or {})
    open_ids = {r[0] for tab in rows for r in rows[tab] if _eligible(tab, r, today_serial)}
    status_lines: Dict[str, str] = {}
    for p in team:
        name = p["name"]
        if p["placeholder"]:
            status_lines[name] = "Type the person's name here to add them to the rotation"
            continue
        if not p["active"]:
            status_lines[name] = "Not in the rotation (Active? is unchecked)"
            continue
        mine = [r for r in known.values() if (r.assigned_to or "") == name]
        has_open = any(r.row_id in open_ids for r in mine)
        last = max([r.staff_changed_at or r.assigned_at for r in mine if (r.staff_changed_at or r.assigned_at)]
                   or [None]) if mine else None
        if has_open and last is not None and now - last > PAUSE_AFTER:
            if name not in paused:
                paused[name] = now.isoformat()
                _pause_alert(db, prog, name, last, now)
                out["paused"].append(name)
            status_lines[name] = "Paused: no updates since %s. Owners alerted." % last.strftime("%b %-d")
        else:
            paused.pop(name, None)
            status_lines[name] = "In the rotation"
    rotation = [p["name"] for p in team if status_lines.get(p["name"]) == "In the rotation"]
    # 3. every row that needs a person and has nobody. Owner, Nov 23 5:53 PM
    # ("Kitty Arnold is sitting appointmentless"): appointments and follow-ups
    # are balanced SEPARATELY - a new appointment goes to whoever has the
    # fewest upcoming appointments, a new follow-up to whoever has the fewest
    # open follow-ups (ties: in turn). A newcomer catches up first.
    # Owner, Nov 25 ("everybody's getting an even amount of appointments
    # starting now"): the count starts over whenever the people in the
    # rotation change (someone added, removed or paused), and from then on
    # each new one goes to whoever has received the fewest since - a newcomer
    # gets their even share, not everything until they catch up.
    since_raw = cfg.get("rr_since")
    if sorted(cfg.get("rr_members") or []) != sorted(rotation) or not since_raw:
        cfg["rr_since"], cfg["rr_members"] = now.isoformat(), list(rotation)
        since = now
    else:
        since = datetime.fromisoformat(since_raw)
    load = {t: {n: 0 for n in rotation} for t in (APPOINTMENTS, FOLLOW_UPS)}
    for r in known.values():
        if r.tab in load and (r.assigned_to or "") in load[r.tab] and r.assigned_at and r.assigned_at >= since:
            load[r.tab][r.assigned_to] += 1
    appt_owner_by_lead: Dict[str, str] = {}
    for t in (APPOINTMENTS, FOLLOW_UPS):
        for row in rows[t]:
            rid0 = str(row[0])
            a = (staff[t].get(rid0) or {}).get("assigned") or ""
            if a and _eligible(t, row, today_serial):
                if t == APPOINTMENTS and known.get(rid0) is not None and known[rid0].lead_id:
                    appt_owner_by_lead[known[rid0].lead_id] = a
    turn_key = {APPOINTMENTS: "rr_last_appt", FOLLOW_UPS: "rr_last_fu"}

    def _pick(t: str) -> Optional[str]:
        if not rotation:
            return None
        last = cfg.get(turn_key[t]) or cfg.get("rr_last")
        start = (rotation.index(last) + 1) if last in rotation else 0
        order = rotation[start:] + rotation[:start]
        return min(order, key=lambda n: load[t][n])
    writes = []
    for tab in (APPOINTMENTS, FOLLOW_UPS):
        first_staff = len(COLUMNS[tab]) + 1
        for row in rows[tab]:
            rid = str(row[0])
            cur = staff[tab].get(rid)
            if cur is None or cur.get("assigned") or not _eligible(tab, row, today_serial):
                continue
            # An appointment booked from a follow-up stays with whoever had the follow-up.
            owner = None
            rec0 = known.get(rid)
            if tab == APPOINTMENTS and rec0 is not None and rec0.lead_id:
                owner = next((r.assigned_to for r in known.values()
                              if r.tab in (FOLLOW_UPS, NEW_FAMILIES) and r.lead_id == rec0.lead_id
                              and r.assigned_to), None)
            # A family the team entered (New Families) stays with whoever entered it -
            # its follow-ups and its appointments (owner, Nov 23).
            if tab == FOLLOW_UPS and not owner:
                lid = rec0.lead_id if rec0 is not None else _lead_for(db, tab, rid)
                if lid:
                    owner = next((r.assigned_to for r in known.values()
                                  if r.tab == NEW_FAMILIES and r.lead_id == lid and r.assigned_to), None)
                    # a family with an upcoming appointment: its follow-up goes to that person
                    owner = owner or appt_owner_by_lead.get(lid)
            if owner:
                name = owner
            else:
                name = _pick(tab)
                if name is None:
                    continue
            writes.append({"range": _rng(tab, "%s%d" % (column(first_staff), cur["row"])), "values": [[name]]})
            cur["assigned"] = name
            rec = known[rid]
            vals = json.loads(rec.staff_json or "{}")
            vals["assigned"] = name
            rec.staff_json, rec.assigned_to, rec.auto_assigned, rec.assigned_at = json.dumps(vals), name, True, now
            _note(db, org, rec.lead_id, "Restland sheet: assigned to %s (%s)"
                  % (name, "same person as the family's other work" if owner else "automatic, fewest open"))
            if not owner:
                cfg[turn_key[tab]] = name
            if name in load[tab]:
                load[tab][name] += 1
            if tab == APPOINTMENTS and rec.lead_id:
                appt_owner_by_lead[rec.lead_id] = name
            out["assigned"] += 1
    # what happened to appointments the team filled in on Follow-Ups
    res_col = column(len(COLUMNS[FOLLOW_UPS]) + len(STAFF_COLUMNS[FOLLOW_UPS]) + 1)
    for rid, text in booking_results.items():
        cur = staff[FOLLOW_UPS].get(rid)
        if cur is not None:
            writes.append({"range": _rng(FOLLOW_UPS, "%s%d" % (res_col, cur["row"])), "values": [[text]]})
    out["booked"] = sum(1 for t in booking_results.values() if t.startswith("Booked"))
    # when the team last changed each row (a column the system owns, found by its header)
    if stamped:
        from app.services.volt.sheets_export import central_serial
        for tab in (APPOINTMENTS, FOLLOW_UPS):
            rows_changed = [r for t, r in stamped if t == tab]
            if not rows_changed:
                continue
            try:
                head = client.spreadsheets().values().get(spreadsheetId=sid, range=_rng(tab, "A1:AN1")).execute()
                header = [str(c).strip() for c in ((head.get("values") or [[]])[0])]
            except Exception:                            # noqa: BLE001 - a courtesy column
                header = []
            if LAST_UPDATE_HEADER in header:
                lu = column(header.index(LAST_UPDATE_HEADER) + 1)
                for r in rows_changed:
                    writes.append({"range": _rng(tab, "%s%d" % (lu, r)), "values": [[central_serial(now)]]})
        out["stamped"] = len(stamped)
    if writes:
        client.spreadsheets().values().batchUpdate(
            spreadsheetId=sid, body={"valueInputOption": "RAW", "data": writes}).execute()
    # 4. each person's own tab, from the main tabs as they are now
    if ptabs:
        try:
            out["person_tab_rows"] = person_tabs_write(client, sid, ptabs, rows, staff, known, pt["grids"],
                                                       today_serial)
            out["person_tab_headers_fixed"] = person_tab_headers_heal(client, sid, ptabs)
        except Exception as exc:                         # noqa: BLE001 - the main tabs are already right
            from app.services.volt.sheets_export import categorize
            log.warning("person tabs not written: %s", categorize(exc))
    # 5. the Team tab's Status column
    team_status = [["Status"]]
    if team:
        rows_needed = max(p["row"] for p in team)
        col = [["Status"]] + [[""] for _ in range(rows_needed - 1)]
        for p in team:
            col[p["row"] - 1] = [status_lines.get(p["name"], "")]
        team_status = col
    try:
        client.spreadsheets().values().update(spreadsheetId=sid, range="'Team'!F1:F%d" % len(team_status),
                                              valueInputOption="RAW", body={"values": team_status}).execute()
    except Exception as exc:                             # noqa: BLE001 - the Status column is a courtesy
        from app.services.volt.sheets_export import categorize
        log.warning("handoff sheet team status not written: %s", categorize(exc))
    cfg["paused"] = paused
    _save_cfg(prog, cfg)
    db.commit()
    return out


# ── New Families: the people a family names on its Permission to Access form ─
#
# Owner, Nov 23 3:45 PM: at a file review the family's Permission to Access
# form names about six people (first, last, address, phone, email). The team
# member types them on the New Families tab; each becomes a Restland contact,
# tagged (source "permission_to_access", status "referral"), tied to the family
# they came from and to the person who entered them - who keeps them: their
# follow-ups and appointments go to that person, not the rotation. They are
# NOT texted or emailed: status "referral" keeps them out of every campaign
# and batch until the owner starts a campaign for them ("utilize that when
# the time is right").

NF_COLUMNS = ("Entered By", "Came From (Family)", "First Name", "Last Name", "Street Address", "City", "State",
              "ZIP", "Phone", "Email", "Relationship", "Notes", "Status", "Added On (CT)", "Ref #")
NF_FIELDS = ("entered_by", "came_from", "first", "last", "street", "city", "state", "zip", "phone", "email",
             "relationship", "notes")
NF_STATUS_COL = len(NF_FIELDS)                       # M (0-based 12)
REFERRAL_SOURCE, REFERRAL_STATUS = "permission_to_access", "referral"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def _referrer(db, org_id: str, name: str) -> Optional[Any]:
    """The family a referral came from, by the name on the Appointments tab
    (a booked family). None when no single family matches."""
    from app.models.models import BookingLink, Lead
    parts = (name or "").strip().split()
    if len(parts) < 2:
        return None
    first, last = parts[0], " ".join(parts[1:])
    rows = (db.query(Lead).join(BookingLink, BookingLink.lead_id == Lead.id)
            .filter(Lead.organization_id == org_id, Lead.first_name.ilike(first), Lead.last_name.ilike(last))
            .distinct().all())
    return rows[0] if len(rows) == 1 else None


def _existing(db, org_id: str, phone: Optional[str], email: Optional[str]):
    from app.models.models import Lead
    if phone:
        forms = {phone, phone.lstrip("+"), phone[2:]}       # +1214..., 1214... (Restland's form), 214...
        hit = db.query(Lead).filter(Lead.organization_id == org_id, Lead.phone.in_(forms)).first()
        if hit is not None:
            return hit
    if email:
        return db.query(Lead).filter(Lead.organization_id == org_id, Lead.email.ilike(email)).first()
    return None


def _title_of(client, sid: str, sheet_id: int, default: str) -> str:
    """A tab's current title, found by its fixed id - the owner may rename it
    (Nov 23: "New Families" became "6 Block")."""
    try:
        meta = client.spreadsheets().get(spreadsheetId=sid, fields="sheets.properties").execute()
        for s_ in meta.get("sheets") or []:
            if s_["properties"].get("sheetId") == sheet_id:
                return s_["properties"].get("title") or default
    except Exception:                                    # noqa: BLE001
        pass
    return default


def new_families_pass(db: Session, prog, client, sid: str, team_names: List[str],
                      now: Optional[datetime] = None) -> Dict[str, Any]:
    """Read the New Families tab; add each complete new row as a tagged
    Restland contact; say on the row what happened. A row already added (it
    has a Ref #) is never added twice; edits to it update the contact."""
    from app.models.models import Lead
    from app.models.program_models import HandoffSheetRow as R
    from app.services.volt.sheets_export import central_serial
    from app.services.wholesale_sms import normalize_e164
    now = now or datetime.utcnow()
    org = prog.organization_id
    width = len(NF_COLUMNS)
    nf_tab = _title_of(client, sid, TAB_IDS[NEW_FAMILIES], NEW_FAMILIES)
    got = client.spreadsheets().values().get(spreadsheetId=sid, range=_rng(nf_tab, "A1:O%d" % (MAX_ROWS + 1))
                                             ).execute()
    values = got.get("values") or []
    if not values or [str(c).strip() for c in values[0][:width]] != list(NF_COLUMNS):
        return {"skipped": "no New Families tab"}
    names = {n.lower(): n for n in team_names}
    out = {"added": 0, "updated": 0, "duplicates": 0, "incomplete": 0}
    writes: List[Dict] = []
    for i, cells in enumerate(values[1:]):
        n = i + 2
        cells = [str(c).strip() for c in list(cells) + [""] * (width - len(cells))]
        vals = dict(zip(NF_FIELDS, cells[:len(NF_FIELDS)]))
        status, ref = cells[NF_STATUS_COL], cells[NF_STATUS_COL + 2]
        if not any(vals[k] for k in NF_FIELDS if k != "entered_by"):
            continue                                     # an empty row (perhaps just a name picked)
        e164 = normalize_e164(vals["phone"]) if vals["phone"] else None
        phone = e164.lstrip("+") if e164 else None          # Restland stores 1XXXXXXXXXX
        email = vals["email"].lower() if _EMAIL.match(vals["email"] or "") else None
        who = names.get(vals["entered_by"].lower())

        def say(text, added=None, rid=None):
            row = [text, added if added is not None else cells[NF_STATUS_COL + 1], rid if rid is not None else ref]
            if row != cells[NF_STATUS_COL:NF_STATUS_COL + 3]:
                writes.append({"range": _rng(nf_tab, "M%d:O%d" % (n, n)), "values": [row]})

        if ref.startswith("NF-"):
            rec = db.query(R).filter(R.organization_id == org, R.row_id == ref).first()
            lead = db.query(Lead).filter(Lead.id == rec.lead_id).first() if rec is not None else None
            if rec is None or lead is None:
                continue
            snap = {k: vals[k] for k in NF_FIELDS}
            if json.loads(rec.staff_json or "{}") != snap:
                changed = []
                for f, attr, v in (("first", "first_name", vals["first"]), ("last", "last_name", vals["last"]),
                                   ("street", "street_address", vals["street"]), ("city", "city", vals["city"]),
                                   ("state", "state", vals["state"]), ("zip", "zip_code", vals["zip"]),
                                   ("email", "email", email), ("phone", "phone", phone)):
                    if v and getattr(lead, attr) != v:
                        setattr(lead, attr, v)
                        changed.append(f)
                if vals["phone"] and phone:
                    lead.phone_raw = vals["phone"]
                if who and who != rec.assigned_to:
                    rec.assigned_to, rec.assigned_at = who, now
                    changed.append("entered by")
                if changed or vals["notes"] != (json.loads(rec.staff_json or "{}").get("notes") or ""):
                    _note(db, org, lead.id, "Restland sheet (%s): New Families row updated (%s)%s"
                          % (rec.assigned_to or "the team", ", ".join(changed) or "notes",
                             "; notes: %s" % vals["notes"] if vals["notes"] else ""))
                    out["updated"] += 1
                rec.staff_json, rec.staff_changed_at = json.dumps(snap), now
            continue
        if ref.startswith("DUP-"):
            continue
        missing = [label for label, ok in (("Entered By (pick a name from the Team tab)", who),
                                           ("First Name", vals["first"]), ("Last Name", vals["last"]),
                                           ("a Phone or an Email", phone or email)) if not ok]
        if vals["phone"] and not phone:
            missing.append("a phone number with 10 digits")
        if missing:
            say("Missing: " + ", ".join(missing))
            out["incomplete"] += 1
            continue
        src = _referrer(db, org, vals["came_from"])
        dup = _existing(db, org, e164, email)
        if dup is not None:
            _note(db, org, dup.id, "Restland sheet (%s): also named on %s's Permission to Access form%s"
                  % (who, vals["came_from"] or "a family", " (%s)" % vals["relationship"] if vals["relationship"] else ""))
            say("Already in the system as %s %s - noted, not added twice"
                % (dup.first_name or "", dup.last_name or ""), "", "DUP-%s" % dup.id[:8])
            out["duplicates"] += 1
            continue
        detail = "Permission to Access - from %s%s; entered by %s" % (
            vals["came_from"] or "a family", " (%s)" % vals["relationship"] if vals["relationship"] else "", who)
        lead = Lead(organization_id=org, first_name=vals["first"], last_name=vals["last"], phone=phone,
                    phone_raw=vals["phone"] or None, email=email, street_address=vals["street"] or None,
                    city=vals["city"] or None, state=vals["state"] or None, zip_code=vals["zip"] or None,
                    status=REFERRAL_STATUS, source=REFERRAL_SOURCE, source_detail=detail[:250],
                    contact_channel="sms" if phone else "email_only")
        db.add(lead)
        db.flush()
        rid = "NF-%s" % lead.id
        db.add(R(organization_id=org, row_id=rid, tab=NEW_FAMILIES, lead_id=lead.id, assigned_to=who,
                 auto_assigned=False, assigned_at=now, staff_json=json.dumps({k: vals[k] for k in NF_FIELDS}),
                 staff_changed_at=now))
        _note(db, org, lead.id, "Restland sheet: added by %s - %s%s" % (
            who, detail, "; notes: %s" % vals["notes"] if vals["notes"] else ""))
        if src is not None:
            _note(db, org, src.id, "Restland sheet (%s): named %s %s on their Permission to Access form%s"
                  % (who, vals["first"], vals["last"], " (%s)" % vals["relationship"] if vals["relationship"] else ""))
        say("Added - %s's family to work" % who, central_serial(now), rid)
        out["added"] += 1
    db.commit()
    for k in range(0, len(writes), 500):
        client.spreadsheets().values().batchUpdate(
            spreadsheetId=sid, body={"valueInputOption": "RAW", "data": writes[k:k + 500]}).execute()
    return out


def sync(db: Session, prog, client=None) -> Dict[str, Any]:
    from app.services.volt import sheets_export as X
    sid = sheet_id(prog)
    if not sid:
        return {"skipped": "no handoff sheet"}
    client = client or api()
    org = prog.organization_id
    try:
        ensure_layout(db, prog, client, sid)
    except Exception as exc:                             # noqa: BLE001 - the data still goes in
        db.rollback()
        log.warning("handoff sheet layout not applied: %s", X.categorize(exc))
    team_now = read_team(client, sid)
    try:
        ensure_person_tabs(db, prog, client, sid, team_now)
    except Exception as exc:                             # noqa: BLE001 - the main tabs still sync
        db.rollback()
        log.warning("handoff sheet person tabs layout not applied: %s (%s)", X.categorize(exc), str(exc)[:300])
    try:
        ensure_fu_tabs(db, prog, client, sid, team_now)
    except Exception as exc:                             # noqa: BLE001 - the main tabs still sync
        db.rollback()
        log.warning("handoff sheet follow-up tabs not set up: %s (%s)", X.categorize(exc), str(exc)[:300])
    try:
        heal_date_formats(client, sid)
    except Exception as exc:                             # noqa: BLE001 - only the look
        log.warning("handoff sheet date formats not healed: %s", X.categorize(exc))
    appts = appointment_rows(db, org)
    fus = follow_up_rows(db, org, known_ids(client, sid, FOLLOW_UPS))
    a = upsert(client, sid, APPOINTMENTS, appts)
    f = upsert(client, sid, FOLLOW_UPS, fus)
    t = team_pass(db, prog, client, sid, {APPOINTMENTS: appts, FOLLOW_UPS: [r for r in fus if r[1] is not None]})
    try:
        nf = new_families_pass(db, prog, client, sid,
                               [p["name"] for p in read_team(client, sid) if not p["placeholder"]])
    except Exception as exc:                             # noqa: BLE001 - the rest of the board still syncs
        db.rollback()
        nf = {"error": X.categorize(exc)}
        log.warning("handoff sheet New Families not read: %s", X.categorize(exc))
    return {"appointments": a, "follow_ups": f, "team": t, "new_families": nf}


def fingerprint(db: Session, org_id: str):
    """A cheap marker that moves whenever something on the board would change:
    a booking, cancellation or move (each one writes appointment
    notifications) or a family's conversation."""
    from sqlalchemy import func
    from app.models.program_models import ProgramConversation
    from app.models.reset_models import AppointmentNotification as N
    conv = db.query(func.max(ProgramConversation.updated_at), func.count(ProgramConversation.id)) \
        .filter(ProgramConversation.organization_id == org_id).one()
    notes = db.query(func.max(N.updated_at), func.count(N.id)).filter(N.organization_id == org_id).one()
    return (str(conv[0]), conv[1], str(notes[0]), notes[1])


def due(db: Session, prog, now: datetime) -> bool:
    last = _last_run.get(prog.id)
    if last is None or now - last >= EVERY:
        return True
    if now - last < MIN_GAP:
        return False
    return fingerprint(db, prog.organization_id) != _last_seen.get(prog.id)


def pending(db: Session, now: Optional[datetime] = None) -> bool:
    """Is any board due for a write? (the loop asks before taking its lease)"""
    from app.models.program_models import OutreachProgram
    now = now or datetime.utcnow()
    return any(sheet_id(p) and due(db, p, now) for p in db.query(OutreachProgram).all())


def run(db: Session, now: Optional[datetime] = None, force: bool = False) -> Dict[str, Any]:
    """Every program with a handoff sheet: right after something changes, and
    at least every two minutes for the team's own edits."""
    from app.models.program_models import OutreachProgram
    from app.services.volt import sheets_export as X
    now = now or datetime.utcnow()
    out: Dict[str, Any] = {}
    for prog in db.query(OutreachProgram).all():
        if not sheet_id(prog):
            continue
        if not force and not due(db, prog, now):
            continue
        _last_run[prog.id] = now
        try:
            _last_seen[prog.id] = fingerprint(db, prog.organization_id)
        except Exception:                                # noqa: BLE001 - the timer still applies
            db.rollback()
            _last_seen.pop(prog.id, None)
        try:
            out[prog.organization_id] = sync(db, prog)
        except Exception as exc:                         # noqa: BLE001 - category only, never cell text
            db.rollback()                                # nothing half-done carries into the next job
            out[prog.organization_id] = {"error": X.categorize(exc)}
            log.warning("handoff sheet sync failed for %s: %s", prog.organization_id, X.categorize(exc))
    return out
