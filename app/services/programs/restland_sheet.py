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
    APPOINTMENTS: ("Assigned To", "Confirmed With Family?", "Remote Call Link / Details", "Outcome", "Team Notes"),
    FOLLOW_UPS: ("Assigned To", "Done?", "Team Notes", "Appointment Date", "Appointment Time", "How",
                 "Home Address"),
}
# Written by the system to the right of the team's Follow-Ups columns: what
# happened when the team filled in an appointment (owner, Nov 22 9:16 PM).
FU_RESULT_HEADER = "Booking Result"
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
EVERY = timedelta(minutes=5)
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
    fu_on = central_serial(conv.next_followup_at) if state == "waiting_future" and conv.next_followup_at else ""
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
TAB_IDS = {"Dashboard": 0, APPOINTMENTS: 1, FOLLOW_UPS: 2, "Person 1": 3, "Person 2": 4, "Team": 5,
           "Person 3": 7, "Person 4": 8, "Person 5": 9}
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
    # Appointments: system A-L, the team's M-Q
    r += [_fmt(A, "A2:Q2000", size=10, fg=TEXT, valign="TOP"),
          _fmt(A, "A1:L1", bold=True, fg="#FFFFFF", bg=GREEN, valign="MIDDLE", wrap=True),
          _fmt(A, "M1:Q1", bold=True, fg="#FFFFFF", bg=BRASS, valign="MIDDLE", wrap=True),
          _fmt(A, "M2:Q2000", bg=GOLD), _fmt(A, "B2:B2000", number="m/d/yyyy h:mm am/pm"),
          _fmt(A, "C2:C2000", number="ddd, mmm d, yyyy"), _fmt(A, "D2:D2000", number="h:mm am/pm"),
          _fmt(A, "L2:L2000", wrap=True), _fmt(A, "O2:O2000", wrap=True), _fmt(A, "Q2:Q2000", wrap=True),
          _borders(A, "A1:Q2000"), _height(A, 1, 40),
          _team_list(A, "M2:M2000"), _checkbox(A, "N2:N2000"),
          _list(A, "P2:P2000", ["Showed", "No-Show", "Rescheduled", "Cancelled"]),
          _when(A, "A2:Q2000", '=AND($C2<>"",$C2=TODAY())', "#DCEFE3"),
          _when(A, "A2:Q2000", '=OR(LEFT($K2,6)="Cancel",$P2="Cancelled")', "#EEEEEE", GREY),
          _when(A, "M2:M2000", '=AND($A2<>"",$M2="",$C2>=TODAY())', "#FBE3DF")]
    r += _widths(A, {"A": 90, "B": 135, "C": 130, "D": 80, "E": 85, "F": 170, "G": 115, "H": 190, "I": 135,
                     "J": 200, "K": 120, "L": 260, "M": 140, "N": 110, "O": 200, "P": 110, "Q": 240})
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
                     "R": 130, "S": 200})
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
APPT_KEYS = ("assigned", "confirmed", "link", "outcome", "notes")
FU_KEYS = ("assigned", "done", "notes", "appt_date", "appt_time", "appt_how", "appt_address",
           "fu_date", "fu_result")
FU_WORK_KEYS = ("fu_date", "fu_result")
APPT_FIELDS = ("appt_date", "appt_time", "appt_how", "appt_address")
KEYS = {APPOINTMENTS: APPT_KEYS, FOLLOW_UPS: FU_KEYS}


def _truthy(v) -> bool:
    return str(v).strip().upper() == "TRUE" or v is True


def read_staff(client, sid: str, tab: str) -> Dict[str, Dict[str, Any]]:
    """{row id: {"row": n, keys...}} - the team's columns as they are now."""
    from app.services.volt.sheets_export import column
    first = len(COLUMNS[tab])
    width = first + len(STAFF_COLUMNS[tab])
    last = FU_WORK_FIRST + len(FU_WORK_COLUMNS) if tab == FOLLOW_UPS else width
    got = client.spreadsheets().values().get(
        spreadsheetId=sid, range=_rng(tab, "A2:%s%d" % (column(last), MAX_ROWS + 1))).execute()
    out: Dict[str, Dict[str, Any]] = {}
    for i, cells in enumerate(got.get("values") or []):
        rid = str(cells[0]).strip() if cells else ""
        if not rid or rid in out:
            continue
        cells = list(cells) + [""] * (last - len(cells))
        vals = [str(c).strip() for c in cells[first:width]]
        if tab == FOLLOW_UPS:
            vals += [str(c).strip() for c in cells[FU_WORK_FIRST:last]]
        out[rid] = dict(zip(KEYS[tab], vals), row=i + 2)
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
        if "confirmed" in changed and _truthy(new.get("confirmed")):
            said.append("confirmed with the family")
            if b is not None and b.confirmed_at is None:
                b.confirmed_at = now
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
    booking_results: Dict[str, str] = {}
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
            if old != vals:
                if _apply(db, prog, tab, rid, rec.lead_id, old, vals, now, results=booking_results):
                    out["changes"] += 1
                rec.staff_json = json.dumps(vals)
                rec.staff_changed_at = now
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
    # 3. round robin: every row that needs a person and has nobody
    last_name = cfg.get("rr_last")
    idx = rotation.index(last_name) if last_name in rotation else -1
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
                              if r.tab == FOLLOW_UPS and r.lead_id == rec0.lead_id and r.assigned_to), None)
            if owner:
                name = owner
            elif rotation:
                idx = (idx + 1) % len(rotation)
                name = rotation[idx]
            else:
                continue
            writes.append({"range": _rng(tab, "%s%d" % (column(first_staff), cur["row"])), "values": [[name]]})
            rec = known[rid]
            vals = json.loads(rec.staff_json or "{}")
            vals["assigned"] = name
            rec.staff_json, rec.assigned_to, rec.auto_assigned, rec.assigned_at = json.dumps(vals), name, True, now
            _note(db, org, rec.lead_id, "Restland sheet: assigned to %s (%s)"
                  % (name, "booked from their follow-up" if owner else "automatic, in turn"))
            if not owner:
                cfg["rr_last"] = name
            out["assigned"] += 1
    # what happened to appointments the team filled in on Follow-Ups
    res_col = column(len(COLUMNS[FOLLOW_UPS]) + len(STAFF_COLUMNS[FOLLOW_UPS]) + 1)
    for rid, text in booking_results.items():
        cur = staff[FOLLOW_UPS].get(rid)
        if cur is not None:
            writes.append({"range": _rng(FOLLOW_UPS, "%s%d" % (res_col, cur["row"])), "values": [[text]]})
    out["booked"] = sum(1 for t in booking_results.values() if t.startswith("Booked"))
    if writes:
        client.spreadsheets().values().batchUpdate(
            spreadsheetId=sid, body={"valueInputOption": "RAW", "data": writes}).execute()
    # 4. the Team tab's Status column
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
    appts = appointment_rows(db, org)
    fus = follow_up_rows(db, org, known_ids(client, sid, FOLLOW_UPS))
    a = upsert(client, sid, APPOINTMENTS, appts)
    f = upsert(client, sid, FOLLOW_UPS, fus)
    t = team_pass(db, prog, client, sid, {APPOINTMENTS: appts, FOLLOW_UPS: [r for r in fus if r[1] is not None]})
    return {"appointments": a, "follow_ups": f, "team": t}


def run(db: Session, now: Optional[datetime] = None, force: bool = False) -> Dict[str, Any]:
    """Every program with a handoff sheet, at most every five minutes."""
    from app.models.program_models import OutreachProgram
    from app.services.volt import sheets_export as X
    now = now or datetime.utcnow()
    out: Dict[str, Any] = {}
    for prog in db.query(OutreachProgram).all():
        if not sheet_id(prog):
            continue
        last = _last_run.get(prog.id)
        if not force and last is not None and now - last < EVERY:
            continue
        _last_run[prog.id] = now
        try:
            out[prog.organization_id] = sync(db, prog)
        except Exception as exc:                         # noqa: BLE001 - category only, never cell text
            db.rollback()                                # nothing half-done carries into the next job
            out[prog.organization_id] = {"error": X.categorize(exc)}
            log.warning("handoff sheet sync failed for %s: %s", prog.organization_id, X.categorize(exc))
    return out
