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
APPT_COLUMNS = ("Appointment ID", "Booked On (CT)", "Date", "Time (CT)", "Day", "Family", "Phone", "Email",
                "How", "Home Address", "System Status", "What They Said Last", "Family File")
FU_COLUMNS = ("Follow-Up ID", "Added (CT)", "Family", "Phone", "Email", "Why", "Follow Up On",
              "What They Said", "System Status", "Family File")
COLUMNS = {APPOINTMENTS: APPT_COLUMNS, FOLLOW_UPS: FU_COLUMNS}
# The team's own columns, to the right of the system's (written once, as the
# header, by the sheet's setup - never by the sync).
STAFF_COLUMNS = {
    APPOINTMENTS: ("Assigned To", "Confirmed With Family?", "Remote Call Link / Details", "Outcome", "Team Notes"),
    FOLLOW_UPS: ("Assigned To", "Done?", "Team Notes"),
}
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

def _file_link(lead_id: str) -> str:
    import os
    base = (os.environ.get("APP_BASE_URL") or "").strip().rstrip("/")
    return "%s/program/live?f=%s" % (base, lead_id) if base else ""


def _skip(lead) -> bool:
    from app.services.sci_staging_demo import is_demo_lead
    from app.services.test_records import is_owner_test_lead
    return lead is None or bool(getattr(lead, "is_test", False)) or is_demo_lead(lead) or is_owner_test_lead(lead)


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
                    safe_phone(lead.phone), safe(lead.email), HOW.get(b.meeting_type or "office", "At Restland"),
                    safe(b.visit_address if b.meeting_type == "home" else ""), status,
                    safe(_last_family_words(db, lead.id)), safe(_file_link(lead.id))])
    return out


def _conv_row(db, conv, lead) -> List[Any]:
    from app.services.volt.sheets_export import central_serial, safe, safe_phone
    state = conv.state or ""
    if state == "waiting_future":
        why = "Asked us to follow up later" + (" - %s" % conv.followup_reason if conv.followup_reason else "")
    elif state == "callback_requested":
        why = "Asked for a call back" + (" (%s)" % conv.callback_window if conv.callback_window else "")
    else:
        why = re.sub(r"\s*Contact them personally\.?\s*$", "", conv.handoff_reason or "Needs a person")
    status = ("Open" if state in OPEN_STATES else DONE_STATES.get(state, "AI working on it"))
    fu_on = central_serial(conv.next_followup_at) if state == "waiting_future" and conv.next_followup_at else ""
    return ["FU-" + conv.id, central_serial(conv.created_at), safe(_name(lead)), safe_phone(lead.phone),
            safe(lead.email), safe(why[:300]), fu_on, safe(_last_family_words(db, lead.id)), status,
            safe(_file_link(lead.id))]


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
        out.append(["TOLD-" + t["message_id"], central_serial(t["at"]), safe(t["name"]), safe_phone(lead.phone),
                    safe(lead.email), "Told a time - not on the calendar yet. Book it.", "",
                    safe(t["said"][:300]), "Open", safe(_file_link(lead.id))])
    for mid in told_known - seen:
        # No longer unbooked (booked since, or past the look-back): mark it done.
        out.append(["TOLD-" + mid, None, None, None, None, None, None, None, "Booked or handled", None])
    return out


# ── the board's look (applied once by the server) ───────────────────────────
#
# Restland's own colors (#507 design decision): forest green, warm ivory,
# muted brass. Applied the first time the server writes to the sheet, then
# recorded ("formatted": true) so a person's later changes are left alone.

TAB_IDS = {"Dashboard": 0, APPOINTMENTS: 1, FOLLOW_UPS: 2, "Person 1": 3, "Person 2": 4, "Team": 5,
           "How It Works": 6}
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
    A, F, D, T, H = TAB_IDS[APPOINTMENTS], TAB_IDS[FOLLOW_UPS], TAB_IDS["Dashboard"], TAB_IDS["Team"], \
        TAB_IDS["How It Works"]
    r: List[Dict] = []
    tab_color = {D: GREEN, A: GREEN, F: BRASS, 3: GOLD, 4: GOLD, T: IVORY, H: IVORY}
    for sid, color in tab_color.items():
        r.append({"updateSheetProperties": {"properties": {"sheetId": sid, "tabColorStyle": {"rgbColor": _rgb(color)}},
                                            "fields": "tabColorStyle"}})
    # Appointments
    r += [_fmt(A, "A2:R2000", size=10, fg=TEXT, valign="TOP"),
          _fmt(A, "A1:M1", bold=True, fg="#FFFFFF", bg=GREEN, valign="MIDDLE", wrap=True),
          _fmt(A, "N1:R1", bold=True, fg="#FFFFFF", bg=BRASS, valign="MIDDLE", wrap=True),
          _fmt(A, "N2:R2000", bg=GOLD), _fmt(A, "B2:B2000", number="m/d/yyyy h:mm am/pm"),
          _fmt(A, "C2:C2000", number="ddd, mmm d, yyyy"), _fmt(A, "D2:D2000", number="h:mm am/pm"),
          _fmt(A, "L2:L2000", wrap=True), _fmt(A, "P2:P2000", wrap=True), _fmt(A, "R2:R2000", wrap=True),
          _borders(A, "A1:R2000"), _height(A, 1, 40),
          _team_list(A, "N2:N2000"), _checkbox(A, "O2:O2000"),
          _list(A, "Q2:Q2000", ["Showed", "No-Show", "Rescheduled", "Cancelled"]),
          _when(A, "A2:R2000", '=AND($C2<>"",$C2=TODAY())', "#DCEFE3"),
          _when(A, "A2:R2000", '=OR(LEFT($K2,6)="Cancel",$Q2="Cancelled")', "#EEEEEE", GREY),
          _when(A, "N2:N2000", '=AND($A2<>"",$N2="",$C2>=TODAY())', "#FBE3DF")]
    r += _widths(A, {"A": 90, "B": 135, "C": 130, "D": 80, "E": 85, "F": 170, "G": 115, "H": 190, "I": 135,
                     "J": 200, "K": 120, "L": 260, "M": 110, "N": 140, "O": 110, "P": 200, "Q": 110, "R": 240})
    # Follow-Ups
    r += [_fmt(F, "A2:M2000", size=10, fg=TEXT, valign="TOP"),
          _fmt(F, "A1:J1", bold=True, fg="#FFFFFF", bg=GREEN, valign="MIDDLE", wrap=True),
          _fmt(F, "K1:M1", bold=True, fg="#FFFFFF", bg=BRASS, valign="MIDDLE", wrap=True),
          _fmt(F, "K2:M2000", bg=GOLD), _fmt(F, "B2:B2000", number="m/d/yyyy h:mm am/pm"),
          _fmt(F, "G2:G2000", number="ddd, mmm d, yyyy"),
          _fmt(F, "F2:F2000", wrap=True), _fmt(F, "H2:H2000", wrap=True), _fmt(F, "M2:M2000", wrap=True),
          _borders(F, "A1:M2000"), _height(F, 1, 40),
          _team_list(F, "K2:K2000"), _checkbox(F, "L2:L2000"),
          _when(F, "A2:M2000", '=OR($L2=TRUE,$I2<>"Open")', "#EEEEEE", GREY),
          _when(F, "A2:M2000", '=AND($I2="Open",$L2<>TRUE,OR($G2="",$G2<TODAY()+1))', "#FBE3DF")]
    r += _widths(F, {"A": 90, "B": 135, "C": 170, "D": 115, "E": 190, "F": 260, "G": 130, "H": 280, "I": 110,
                     "J": 110, "K": 140, "L": 70, "M": 260})
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
    for sid in (TAB_IDS["Person 1"], TAB_IDS["Person 2"]):
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
    # Team + How It Works
    r += [_fmt(T, "A1:D1", bold=True, fg="#FFFFFF", bg=GREEN), _fmt(T, "A2:D30", size=10, bg=GOLD),
          _borders(T, "A1:D30")] + _widths(T, {"A": 180, "B": 130, "C": 220, "D": 240})
    r += [_fmt(H, "A1:B22", size=10, fg=TEXT, wrap=True, valign="TOP"),
          _fmt(H, "A1", bold=True, size=16, fg=GREEN),
          _fmt(H, "A2:B2", bold=True, fg="#FFFFFF", bg=GREEN), _fmt(H, "A3:A22", bold=True),
          _fmt(H, "A13:B13", bold=True, fg="#FFFFFF", bg=GREEN), _fmt(H, "A19:B19", bold=True, fg="#FFFFFF", bg=GREEN),
          _fmt(H, "A15:B15", bg=GOLD)] + _widths(H, {"A": 190, "B": 640})
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


def sync(db: Session, prog, client=None) -> Dict[str, Any]:
    from app.services.volt import sheets_export as X
    sid = sheet_id(prog)
    if not sid:
        return {"skipped": "no handoff sheet"}
    client = client or X.api()
    org = prog.organization_id
    try:
        ensure_layout(db, prog, client, sid)
    except Exception as exc:                             # noqa: BLE001 - the data still goes in
        db.rollback()
        log.warning("handoff sheet layout not applied: %s", X.categorize(exc))
    a = upsert(client, sid, APPOINTMENTS, appointment_rows(db, org))
    f = upsert(client, sid, FOLLOW_UPS, follow_up_rows(db, org, known_ids(client, sid, FOLLOW_UPS)))
    return {"appointments": a, "follow_ups": f}


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
            out[prog.organization_id] = {"error": X.categorize(exc)}
            log.warning("handoff sheet sync failed for %s: %s", prog.organization_id, X.categorize(exc))
    return out
