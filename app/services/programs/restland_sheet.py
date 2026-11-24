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
TIME_CHOICES = tuple(
    "%d:%02d %s" % ((h - 1) % 12 + 1, m, "AM" if h < 12 else "PM")
    for h in range(10, 17) for m in (0, 30) if (h, m) <= (16, 0))
HOW_CHOICES = {"At Restland": ("office", "visit"), "At Their Home": ("home", "visit"),
               "Phone / Video Call": ("remote", "phone")}
HOW = {"office": "At Restland", "home": "At Their Home", "remote": "Phone / Video Call"}
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
    for b, lead in q.all():
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
                    safe(_last_family_words(db, lead.id))])
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


def _conv_row(db, conv, lead) -> List[Any]:
    from app.services.volt.sheets_export import central_serial, safe, safe_phone
    state = conv.state or ""
    if state == "waiting_future":
        why = "Asked us to follow up later" + (" - %s" % conv.followup_reason if conv.followup_reason else "")
    elif state == "callback_requested":
        why = "Asked for a call back" + (" (%s)" % conv.callback_window if conv.callback_window else "")
    else:
        why = _plain_why(conv.handoff_reason)
    status = ("Open" if state in OPEN_STATES else DONE_STATES.get(state, "In progress"))
    # The date a follow-up is due: the family's own "later" date, or a date the
    # team set on a needs-a-person follow-up (that one never texts the family).
    fu_on = (central_serial(conv.next_followup_at)
             if conv.next_followup_at and state in ("waiting_future", "human") else "")
    return ["FU-" + conv.id, central_serial(conv.created_at), safe(_name(lead)), _phone(lead.phone),
            safe(lead.email), safe(why[:300]), fu_on, safe(_last_family_words(db, lead.id)), status]


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
    for c in convs:
        lead = leads.get(c.lead_id)
        if _skip(lead) or c.is_test:
            continue
        out.append(_conv_row(db, c, lead))
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
          _list(F, "N2:N2000", list(TIME_CHOICES)), _list(F, "O2:O2000", list(HOW_CHOICES)),
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
    if [str(c).strip() for c in (head.get("values") or [[]])[0]] != list(cols):
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
                     "placeholder": bool(PLACEHOLDER.match(name))})
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

PERSON_TAB_IDS = (3, 4, 7, 8, 9)                     # Team rows 2-6, in order
PT_APPT = (("ref", "Ref"), ("date", "Date"), ("time", "Time"), ("family", "Family"), ("phone", "Phone"),
           ("how", "How"), ("address", "Home Address"), ("status", "Status"),
           ("outcome", "Outcome"), ("sold", "Sold?"), ("sold_what", "What Was Sold"),
           ("link", "Call Link / Details"), ("notes", "My Notes"), ("updated", "Last Update"))
PT_FU = (("ref", "Ref"), ("due", "Follow Up On"), ("family", "Family"), ("phone", "Phone"), ("why", "Why"),
         ("said", "What They Said"), ("done", "Done?"), ("fu_date", "Called On"),
         ("fu_result", "What Happened"), ("appt_date", "Appt Date"), ("appt_time", "Appt Time"),
         ("appt_how", "How"), ("appt_address", "Home Address"), ("notes", "My Notes"), ("updated", "Last Update"))
PT_APPT_EDIT = ("link", "outcome", "sold", "sold_what", "notes")
PT_FU_EDIT = ("done", "fu_date", "fu_result", "appt_date", "appt_time", "appt_how", "appt_address", "notes")
PT_APPT_INFO = 8                                     # A-H who & when, I-M your update, N last update
PT_FU_INFO = 6                                       # A-F who & why, G-N your update, O last update
PT_WIDTH = max(len(PT_APPT), len(PT_FU))             # A..O
# rows (1-based)
PT_TITLE, PT_HELP = 1, 2
PT_APPT_BANNER, PT_APPT_GROUPS, PT_APPT_HEAD, PT_APPT_FIRST, PT_APPT_MAX = 4, 5, 6, 7, 40
PT_FU_BANNER = PT_APPT_FIRST + PT_APPT_MAX + 1       # 48
PT_FU_GROUPS, PT_FU_HEAD, PT_FU_FIRST, PT_FU_MAX = PT_FU_BANNER + 1, PT_FU_BANNER + 2, PT_FU_BANNER + 3, 200
PT_LAST_ROW = PT_FU_FIRST + PT_FU_MAX - 1


def _person_tabs(client, sid: str, team: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """[{"name", "title", "sheet_id"}] for the person tabs whose Team row has a real name."""
    try:
        meta = client.spreadsheets().get(spreadsheetId=sid, fields="sheets.properties").execute()
    except Exception:                                    # noqa: BLE001
        return []
    titles = {s_["properties"]["sheetId"]: s_["properties"]["title"] for s_ in meta.get("sheets") or []}
    by_row = {p["row"]: p for p in team}
    out = []
    for i, tid in enumerate(PERSON_TAB_IDS):
        p = by_row.get(i + 2)
        if p is None or p.get("placeholder") or tid not in titles:
            continue
        out.append({"name": p["name"], "title": titles[tid], "sheet_id": tid})
    return out


def _cell(v) -> str:
    return str(v).strip() if v is not None else ""


def _pt_blocks(client, sid: str, title: str) -> Dict[str, List[List[Any]]]:
    from app.services.volt.sheets_export import column
    out = {}
    for key, first, n, width in ((APPOINTMENTS, PT_APPT_FIRST, PT_APPT_MAX, len(PT_APPT)),
                                 (FOLLOW_UPS, PT_FU_FIRST, PT_FU_MAX, len(PT_FU))):
        got = client.spreadsheets().values().get(
            spreadsheetId=sid, range=_rng(title, "A%d:%s%d" % (first, column(width), first + n - 1))).execute()
        out[key] = [list(r) + [""] * (width - len(r)) for r in (got.get("values") or [])]
    return out


def person_tabs_read(client, sid: str, tabs: List[Dict[str, Any]], staff: Dict[str, Dict[str, Dict[str, Any]]],
                     known: Dict[str, Any]) -> Dict[str, Any]:
    """Edits typed on person tabs -> merged into `staff` (as if typed on the
    main tab). Returns {"grids": {title: {tab: rows read}}, "edits": n, "copy": [(tab, rid, key, value)]}."""
    grids, copy, edits = {}, [], 0
    for t in tabs:
        try:
            blocks = _pt_blocks(client, sid, t["title"])
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
            appts.append([rid, r[2], r[3], r[5], r[6], r[8], r[9], cur.get("outcome") or r[10],
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
            fus.append([rid, r[6], r[2], r[3], r[5], r[7], False, cur.get("fu_date", ""), cur.get("fu_result", ""),
                        cur.get("appt_date", ""), cur.get("appt_time", ""), cur.get("appt_how", ""),
                        cur.get("appt_address", ""), cur.get("notes", ""),
                        central_serial(rec.staff_changed_at) if _team_touched(rec, PT_FU_EDIT) else ""])
        fus.sort(key=lambda x: (x[1] if isinstance(x[1], (int, float)) and x[1] else 0, str(x[0])))
        fus = fus[:PT_FU_MAX]
        old = grids.get(t["title"]) or {}
        data = []
        for tab, new_rows, first, cols, text_cols, stamp_col in (
                (APPOINTMENTS, appts, PT_APPT_FIRST, PT_APPT, _PT_APPT_TEXT, len(PT_APPT) - 1),
                (FOLLOW_UPS, fus, PT_FU_FIRST, PT_FU, _PT_FU_TEXT, len(PT_FU) - 1)):
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


_PT_APPT_TEXT = [i for i, (k, _) in enumerate(PT_APPT) if k not in ("date", "time", "updated")]
_PT_FU_TEXT = [i for i, (k, _) in enumerate(PT_FU) if k not in ("due", "updated", "done")]


def _same_row(was: List[str], shown: List[str], text_cols: List[int], stamp_col: int) -> bool:
    """Equal in every text field (dates/times are written as numbers and read
    back formatted), and a time stamp blank/filled the same way."""
    def at(r, i):
        return r[i] if i < len(r) else ""
    return all(at(was, i) == at(shown, i) for i in text_cols) and bool(at(was, stamp_col)) == bool(at(shown, stamp_col))


def person_tab_layout_requests() -> List[Dict]:
    """The person tabs' look (applied once by sync): a title, one line on how to
    use the tab, then MY APPOINTMENTS and MY FOLLOW-UPS stacked, each with a
    banner, a 'who & when' group and a gold 'your update' group."""
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

    widths = [24, 118, 78, 165, 118, 150, 175, 150, 120, 74, 190, 190, 260, 130, 130]
    W = PT_WIDTH
    for sid in PERSON_TAB_IDS:
        a_end = PT_APPT_FIRST + PT_APPT_MAX - 1
        f_end = PT_LAST_ROW
        # clear the old layout first (it may be wider and taller), then size the tab
        r.append({"unmergeCells": {"range": g(sid, 1, 500, 0, 30)}})
        r.append({"setDataValidation": {"range": g(sid, 3, 500, 0, 30)}})
        r.append({"repeatCell": {"range": g(sid, 1, 500, 0, 30), "cell": {},
                                 "fields": "userEnteredValue,userEnteredFormat"}})
        r.append({"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {
            "rowCount": PT_LAST_ROW + 5, "columnCount": W, "frozenRowCount": 2, "hideGridlines": True}},
            "fields": "gridProperties.rowCount,gridProperties.columnCount,gridProperties.frozenRowCount,"
                      "gridProperties.hideGridlines"}})
        # title + how to use
        team_row = PERSON_TAB_IDS.index(sid) + 2
        r.append({"updateCells": {"range": g(sid, PT_TITLE, PT_TITLE, 0, 1), "rows": [{"values": [
            {"userEnteredValue": {"formulaValue": '="MY WORK  ·  "&UPPER(Team!A%d)' % team_row}}]}],
            "fields": "userEnteredValue"}})
        r.append(put(sid, PT_HELP, 0, [
            "How this tab works:  1) Work the families below.  2) Type only in the GOLD columns.  "
            "3) It saves to the main tabs and to the app by itself within a couple of minutes.  "
            "To book a follow-up, fill in Appt Date + Appt Time - the family gets their confirmation."]))
        r += [merge(sid, PT_TITLE, PT_TITLE, 0, W), merge(sid, PT_HELP, PT_HELP, 0, W),
              fmt(sid, PT_TITLE, PT_TITLE, 0, W, bg=INK, fg="#FFFFFF", bold=True, size=16, valign="MIDDLE", pad=10),
              fmt(sid, PT_HELP, PT_HELP, 0, W, bg=CREAM, fg=INK, size=10, valign="MIDDLE", wrap="WRAP", pad=10),
              height(sid, PT_TITLE, PT_TITLE, 44), height(sid, PT_HELP, PT_HELP, 40), height(sid, 3, 3, 10)]
        for (banner, groups, head, first, last, cols, info, title, info_lbl, upd_lbl) in (
                (PT_APPT_BANNER, PT_APPT_GROUPS, PT_APPT_HEAD, PT_APPT_FIRST, a_end, PT_APPT, PT_APPT_INFO,
                 "MY APPOINTMENTS   -   after each visit, fill in the gold columns",
                 "THE APPOINTMENT", "YOUR UPDATE  (type here)"),
                (PT_FU_BANNER, PT_FU_GROUPS, PT_FU_HEAD, PT_FU_FIRST, f_end, PT_FU, PT_FU_INFO,
                 "MY FOLLOW-UPS   -   call these families, then fill in the gold columns",
                 "WHO TO CALL & WHY", "YOUR UPDATE  (type here)")):
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
        r += [fmt(sid, PT_APPT_FIRST, a_end, 1, 2, num=("DATE", "ddd, mmm d"), bold=True),
              fmt(sid, PT_APPT_FIRST, a_end, 2, 3, num=("TIME", "h:mm am/pm"), bold=True),
              fmt(sid, PT_APPT_FIRST, a_end, 3, 4, bold=True),
              fmt(sid, PT_FU_FIRST, f_end, 1, 2, num=("DATE", "ddd, mmm d")),
              fmt(sid, PT_FU_FIRST, f_end, 2, 3, bold=True),
              lst(sid, PT_APPT_FIRST, a_end, 8, ["Showed", "No-Show", "Rescheduled", "Cancelled"]),
              lst(sid, PT_APPT_FIRST, a_end, 9, list(SOLD_CHOICES)),
              {"setDataValidation": {"range": g(sid, PT_FU_FIRST, f_end, 6, 7), "rule": {"condition": {"type": "BOOLEAN"}}}},
              fmt(sid, PT_FU_FIRST, f_end, 6, 7, align="CENTER"),
              {"setDataValidation": {"range": g(sid, PT_FU_FIRST, f_end, 7, 8), "rule": {"condition": {
                  "type": "DATE_IS_VALID"}, "strict": False, "showCustomUi": True}}},
              lst(sid, PT_FU_FIRST, f_end, 8, list(WHAT_HAPPENED)),
              {"setDataValidation": {"range": g(sid, PT_FU_FIRST, f_end, 9, 10), "rule": {"condition": {
                  "type": "DATE_IS_VALID"}, "strict": False, "showCustomUi": True}}},
              lst(sid, PT_FU_FIRST, f_end, 10, list(TIME_CHOICES)),
              lst(sid, PT_FU_FIRST, f_end, 11, list(HOW_CHOICES)),
              _when(sid, "J%d:J%d" % (PT_APPT_FIRST, a_end), '=$J%d="Yes"' % PT_APPT_FIRST, "#DCEFE3"),
              _when(sid, "I%d:I%d" % (PT_APPT_FIRST, a_end), '=$I%d="Showed"' % PT_APPT_FIRST, "#DCEFE3"),
              _when(sid, "I%d:I%d" % (PT_APPT_FIRST, a_end), '=$I%d="No-Show"' % PT_APPT_FIRST, "#FBE3DF"),
              _when(sid, "B%d:F%d" % (PT_FU_FIRST, f_end),
                    '=AND($A%d<>"",OR($B%d="",$B%d<TODAY()+1))' % (PT_FU_FIRST, PT_FU_FIRST, PT_FU_FIRST),
                    "#FCEBD9")]
        for c, px in enumerate(widths[:W]):
            r.append({"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS",
                                                              "startIndex": c, "endIndex": c + 1},
                                                    "properties": {"pixelSize": px}, "fields": "pixelSize"}})
        r.append({"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": 0,
                                                          "endIndex": 1}, "properties": {"hiddenByUser": True},
                                                "fields": "hiddenByUser"}})
    return r


def ensure_person_tabs(db: Session, prog, client, sid: str) -> bool:
    """Give the person tabs their current look, once per version."""
    pol = json.loads(prog.booking_policy_json or "{}")
    cfg = pol.get("handoff_sheet") or {}
    if cfg.get("person_tabs_v5"):
        return False
    # A layout applied before leaves its row striping and color rules behind:
    # Google refuses to stripe a range twice, so take the old ones off first.
    first: List[Dict] = []
    try:
        meta = client.spreadsheets().get(
            spreadsheetId=sid, fields="sheets(properties.sheetId,bandedRanges.bandedRangeId,conditionalFormats)"
        ).execute()
        for sh in meta.get("sheets") or []:
            sheet_id = (sh.get("properties") or {}).get("sheetId")
            if sheet_id not in PERSON_TAB_IDS:
                continue
            first += [{"deleteBanding": {"bandedRangeId": b["bandedRangeId"]}} for b in sh.get("bandedRanges") or []]
            first += [{"deleteConditionalFormatRule": {"sheetId": sheet_id, "index": 0}}
                      for _ in sh.get("conditionalFormats") or []]
    except Exception:                                    # noqa: BLE001 - a fresh sheet has nothing to remove
        first = []
    client.spreadsheets().batchUpdate(spreadsheetId=sid,
                                      body={"requests": first + person_tab_layout_requests()}).execute()
    cfg["person_tabs_v5"] = True
    pol["handoff_sheet"] = cfg
    prog.booking_policy_json = json.dumps(pol)
    db.commit()
    return True


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
    known = {r.row_id: r for r in db.query(R).filter(R.organization_id == org).all()}
    staff = {tab: read_staff(client, sid, tab) for tab in (APPOINTMENTS, FOLLOW_UPS)}
    # 0. what people typed on their own tabs -> as if typed on the main tabs
    ptabs = _person_tabs(client, sid, team)
    pt = person_tabs_read(client, sid, ptabs, staff, known) if ptabs else {"grids": {}, "edits": 0, "copy": []}
    if pt["copy"]:
        out["person_tab_edits"] = copy_to_main(client, sid, staff, pt["copy"])
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
    load = {t: {n: 0 for n in rotation} for t in (APPOINTMENTS, FOLLOW_UPS)}
    appt_owner_by_lead: Dict[str, str] = {}
    for t in (APPOINTMENTS, FOLLOW_UPS):
        for row in rows[t]:
            rid0 = str(row[0])
            a = (staff[t].get(rid0) or {}).get("assigned") or ""
            if a and _eligible(t, row, today_serial):
                if a in load[t]:
                    load[t][a] += 1
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
    try:
        ensure_person_tabs(db, prog, client, sid)
    except Exception as exc:                             # noqa: BLE001 - the main tabs still sync
        db.rollback()
        log.warning("handoff sheet person tabs layout not applied: %s", X.categorize(exc))
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
