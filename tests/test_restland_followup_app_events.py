"""Owner, Nov 24: the Follow-Ups board shows what happened in the app.

A person's reply sent from the app fills Followed Up On / What Happened; a
later family message moves What They Said and Last Update; a failed send is
not a follow-up; the team's newer entries are never overwritten; replies never
mark a case Done; repeated passes change nothing. Synthetic data only."""
import json
from datetime import datetime, timedelta

from app.models.models import Message
from app.models.program_models import HandoffSheetRow
from app.models.work_models import LeadNote
from app.services import send_source as SS
from app.services.programs import ai_setter
from app.services.programs import restland_sheet as RS
from app.services.volt.sheets_export import central_serial
from tests.test_outreach_program import _lead_for, program  # noqa: F401
from tests.test_restland_handoff_2026_11_22 import _Client, _board, _pad, _prog
from tests.test_sci_appointment_setter import setter  # noqa: F401

T0 = datetime(2026, 11, 24, 19, 0)              # 1:00 PM Central: the case starts
HDR = list(RS.FU_COLUMNS) + list(RS.STAFF_COLUMNS[RS.FOLLOW_UPS]) + [RS.FU_RESULT_HEADER] \
    + list(RS.FU_WORK_COLUMNS) + [RS.LAST_UPDATE_HEADER]
COL = {h: i for i, h in enumerate(HDR)}


SRC = {"Erica": "L001", "Fay": "L002", "Tess": "L003", "Bea": "L001", "Em": "L002"}


def _case(setter, sample_advisor, first="Erica", state="human"):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, SRC[first], first=first)
    conv = ai_setter.conversation_for(db, lead)
    conv.state, conv.created_at = state, T0
    db.commit()
    return db, lead, conv


def _family(db, conv, text, at):
    t = ai_setter._turn(db, conv, "family", text, action="received")
    t.created_at = at
    db.commit()


def _sent(db, lead, sample_advisor, at, *, source=SS.MANUAL, status="delivered", body="Hi"):
    db.add(Message(lead_id=lead.id, sender_id=sample_advisor.id, body=body, send_source=source,
                   sent_at=at, twilio_sid="SM%s" % at.strftime("%H%M%S"), delivery_status=status,
                   twilio_status=status))
    db.commit()


def _sheet(team=("Kit",), conv_ids=()):
    sheet = _board([(n, "TRUE") for n in team])
    sheet[RS.FOLLOW_UPS] = [list(HDR)]
    sheet["__titles__"] = {11: RS.fu_tab_title(team[0])}
    return sheet


def _pass(db, prog, sheet, now):
    c = _Client(sheet)
    fus = RS.follow_up_rows(db, prog.organization_id, RS.known_ids(c, "sid", RS.FOLLOW_UPS))
    RS.upsert(c, "sid", RS.FOLLOW_UPS, fus)
    out = RS.team_pass(db, prog, c, "sid", {RS.APPOINTMENTS: [], RS.FOLLOW_UPS: [r for r in fus if r[1] is not None]},
                       now=now)
    return out, c


def _row(sheet, conv):
    return _pad(next(r for r in sheet[RS.FOLLOW_UPS][1:] if r[0] == "FU-" + conv.id), len(HDR))


def test_a_persons_reply_then_the_familys_reply_show_on_the_board_and_the_case_stays_open(setter, sample_advisor):
    db, lead, conv = _case(setter, sample_advisor)
    prog = _prog(setter)
    sheet = _sheet()
    _family(db, conv, "Do I need to come in person?", T0 + timedelta(minutes=1))
    _pass(db, prog, sheet, T0 + timedelta(minutes=2))                 # assigned to Kit
    row = _row(sheet, conv)
    assert row[COL["Assigned To"]] == "Kit" and row[COL["System Status"]] == "Open"
    # Mike replies from the app at 4:26 PM ("Send text" - the AI takes the next reply)
    reply_at = datetime(2026, 11, 24, 22, 26)
    _sent(db, lead, sample_advisor, reply_at)
    conv.state = "ai_active"
    db.commit()
    _pass(db, prog, sheet, reply_at + timedelta(minutes=2))
    row = _row(sheet, conv)
    assert row[COL["Followed Up On"]] == "11/24/2026 4:26 PM"
    assert row[COL["What Happened"]] == "Replied by text (4:26 PM)"
    assert row[COL[RS.LAST_UPDATE_HEADER]] == central_serial(reply_at)
    assert row[COL["System Status"]] == "In progress"                    # not Done, not closed
    assert row[COL["Done?"]] in ("", False, "FALSE")
    # the family answers at 4:34 PM: What They Said + Last Update move; the reply stays recorded
    fam_at = datetime(2026, 11, 24, 22, 34)
    _family(db, conv, "Neither of those times are convenient for me.", fam_at)
    _pass(db, prog, sheet, fam_at + timedelta(minutes=2))
    row = _row(sheet, conv)
    assert row[COL["What They Said"]] == "Neither of those times are convenient for me."
    assert row[COL[RS.LAST_UPDATE_HEADER]] == central_serial(fam_at)
    assert row[COL["What Happened"]] == "Replied by text (4:26 PM)"
    assert row[COL["System Status"]] == "Open"                           # the family is waiting on us
    # her own Follow-Ups tab shows the same
    mine = sheet[RS.fu_tab_title("Kit")][RS.FT_FIRST - 1]
    C = RS._FU_COL
    assert mine[0] == "FU-" + conv.id and mine[C["fu_date"]] == "11/24/2026 4:26 PM"
    assert mine[C["fu_result"]] == "Replied by text (4:26 PM)"
    assert mine[C["updated"]] == central_serial(fam_at)                 # Last Update, next to the dates
    # a repeat pass: nothing is read back as a team edit, nothing is written again
    notes = db.query(LeadNote).filter_by(lead_id=lead.id).count()
    out, c = _pass(db, prog, sheet, fam_at + timedelta(minutes=4))
    assert out["changes"] == 0 and out["app_followups"] == {"followed_up": 0, "last_update": 0}
    assert db.query(LeadNote).filter_by(lead_id=lead.id).count() == notes
    assert sum(1 for r in sheet[RS.FOLLOW_UPS][1:] if r[0] == "FU-" + conv.id) == 1   # one row, by its ID


def test_a_failed_send_is_not_a_follow_up(setter, sample_advisor):
    db, lead, conv = _case(setter, sample_advisor, first="Fay")
    prog = _prog(setter)
    sheet = _sheet()
    _family(db, conv, "Call me", T0 + timedelta(minutes=1))
    _sent(db, lead, sample_advisor, T0 + timedelta(minutes=5), status="failed")
    _sent(db, lead, sample_advisor, T0 + timedelta(minutes=6), source=SS.SCI_SETTER)   # the AI is not a person
    _pass(db, prog, sheet, T0 + timedelta(minutes=8))
    row = _row(sheet, conv)
    assert row[COL["Followed Up On"]] == "" and row[COL["What Happened"]] == ""


def test_the_teams_newer_entry_stays_and_a_still_newer_reply_replaces_it(setter, sample_advisor):
    db, lead, conv = _case(setter, sample_advisor, first="Tess")
    prog = _prog(setter)
    sheet = _sheet()
    _family(db, conv, "Call me", T0 + timedelta(minutes=1))
    _sent(db, lead, sample_advisor, T0 + timedelta(minutes=10))
    _pass(db, prog, sheet, T0 + timedelta(minutes=12))
    row = _row(sheet, conv)
    assert row[COL["What Happened"]].startswith("Replied by text")
    # Kit typed her own follow-up afterwards (a call), and a note
    row[COL["Followed Up On"]], row[COL["What Happened"]], row[COL["Team Notes"]] = "11/24/2026", "Left voicemail", "vm"
    _pass(db, prog, sheet, T0 + timedelta(minutes=20))
    # an older app event (a retry of the same reply) never overwrites her entry
    _pass(db, prog, sheet, T0 + timedelta(minutes=22))
    row = _row(sheet, conv)
    assert (row[COL["Followed Up On"]], row[COL["What Happened"]], row[COL["Team Notes"]]) == \
        ("11/24/2026", "Left voicemail", "vm")
    # a new reply after her entry is the newest follow-up; her note stays
    later = T0 + timedelta(minutes=40)
    _sent(db, lead, sample_advisor, later)
    _pass(db, prog, sheet, later + timedelta(minutes=2))
    row = _row(sheet, conv)
    assert row[COL["What Happened"]].startswith("Replied by text") and row[COL["Team Notes"]] == "vm"


def test_done_and_booked_cases_follow_their_own_rules(setter, sample_advisor):
    db, lead, conv = _case(setter, sample_advisor, first="Bea")
    prog = _prog(setter)
    sheet = _sheet()
    _family(db, conv, "Call me", T0 + timedelta(minutes=1))
    _pass(db, prog, sheet, T0 + timedelta(minutes=2))
    # the team ticks Done -> closed in the app; a later reply records the follow-up, never reopens it
    row = _row(sheet, conv)
    row[COL["Done?"]] = "TRUE"
    _pass(db, prog, sheet, T0 + timedelta(minutes=4))
    db.refresh(conv)
    assert conv.state == "closed"
    _sent(db, lead, sample_advisor, T0 + timedelta(minutes=30))
    _pass(db, prog, sheet, T0 + timedelta(minutes=32))
    row = _row(sheet, conv)
    assert row[COL["System Status"]] == "Closed" and row[COL["What Happened"]].startswith("Replied by text")
    db.refresh(conv)
    assert conv.state == "closed"
    # booked: the case shows Booked; a family message afterwards does not reopen it
    conv.state = "booked"
    db.commit()
    _family(db, conv, "See you then", T0 + timedelta(minutes=40))
    _pass(db, prog, sheet, T0 + timedelta(minutes=42))
    assert _row(sheet, conv)[COL["System Status"]] == "Booked"


def test_an_email_reply_counts_and_a_queued_one_does_not(setter, sample_advisor):
    from app.models.models import EmailMessage
    db, lead, conv = _case(setter, sample_advisor, first="Em")
    prog = _prog(setter)
    sheet = _sheet()
    _family(db, conv, "Email me", T0 + timedelta(minutes=1))
    db.add(EmailMessage(lead_id=lead.id, sender_id=sample_advisor.id, subject="Re: x", body_html="<p>x</p>",
                        send_source=SS.MANUAL, status="queued", sent_at=T0 + timedelta(minutes=5)))
    db.commit()
    _pass(db, prog, sheet, T0 + timedelta(minutes=6))
    assert _row(sheet, conv)[COL["What Happened"]] == ""
    db.add(EmailMessage(lead_id=lead.id, sender_id=sample_advisor.id, subject="Re: x", body_html="<p>x</p>",
                        send_source=SS.MANUAL, status="sent", sent_at=T0 + timedelta(minutes=7)))
    db.commit()
    _pass(db, prog, sheet, T0 + timedelta(minutes=8))
    assert _row(sheet, conv)[COL["What Happened"]] == "Replied by email (1:07 PM)"


def test_due_now_first_and_dates_up_front(setter, sample_advisor):
    db, lead, conv = _case(setter, sample_advisor, first="Erica")
    db2, lead2, conv2 = _case(setter, sample_advisor, first="Fay", state="waiting_future")
    conv2.next_followup_at = T0 + timedelta(days=20)               # a later date
    db.commit()
    prog = _prog(setter)
    sheet = _sheet()
    _family(db, conv, "Call me", T0 + timedelta(minutes=1))
    _family(db, conv2, "After the holidays", T0 + timedelta(minutes=2))
    _pass(db, prog, sheet, T0 + timedelta(minutes=3))
    tab = sheet[RS.fu_tab_title("Kit")]
    rows = [r for r in tab[RS.FT_FIRST - 1:] if r and r[0]]
    assert [r[0] for r in rows] == ["FU-" + conv.id, "FU-" + conv2.id]      # due now on top
    head = tab[RS.FT_HEAD - 1]
    assert head[1:4] == ["Follow Up On", "Last Update", "Family"]


def test_date_formats_are_put_back_every_ten_minutes():
    calls = []

    class C:
        def spreadsheets(self):
            return self

        def get(self, spreadsheetId, fields=None):
            class E:
                def execute(self_inner):
                    return {"sheets": [{"properties": {"sheetId": i}} for i in (2, 11, 12)]}
            return E()

        def batchUpdate(self, spreadsheetId, body):
            calls.append(body["requests"])

            class E:
                def execute(self_inner):
                    return {}
            return E()
    RS._last_heal.clear()
    assert RS.heal_date_formats(C(), "sid", now=T0) is True
    reqs = [q for q in calls[0] if "repeatCell" in q]
    titles = [q["updateCells"] for q in calls[0] if "updateCells" in q]
    assert all(set(q["repeatCell"]["fields"].split(",")) == {"userEnteredFormat.numberFormat"} for q in reqs)
    assert {q["repeatCell"]["range"]["sheetId"] for q in reqs} == {2, 11, 12}        # only tabs that exist
    # each tab's title points at its own Team row (Nov 25: Johnny's tab said Neoso)
    assert {t["range"]["sheetId"] for t in titles} == {11, 12}
    assert all(t["fields"] == "userEnteredValue" for t in titles)
    assert "Team!A3" in [t for t in titles if t["range"]["sheetId"] == 12][0]["rows"][0]["values"][0][
        "userEnteredValue"]["formulaValue"]
    assert RS.heal_date_formats(C(), "sid", now=T0 + timedelta(minutes=5)) is False
    assert RS.heal_date_formats(C(), "sid", now=T0 + timedelta(minutes=11)) is True


def test_new_person_gets_an_appointments_tab_and_fresh_tabs_title_their_own_row():
    """Owner, Nov 25: Johnny (Team row 5) got his Follow-Ups tab, but his
    appointments tab stayed hidden as "Person 4"."""
    import json as _json
    calls = []
    props = [{"sheetId": 3, "title": "Neoso Walker", "index": 4}, {"sheetId": 11, "title": "Neoso Walker \u2013 Follow-Ups", "index": 5},
             {"sheetId": 8, "title": "Person 4", "hidden": True, "index": 10},
             {"sheetId": 9, "title": "Person 5", "hidden": True, "index": 11},
             {"sheetId": 14, "title": "Johnny Weon \u2013 Follow-Ups", "index": 12}]

    class C:
        def spreadsheets(self):
            return self

        def get(self, spreadsheetId, fields=None, ranges=None):
            class E:
                def execute(self_inner):
                    return {"sheets": [{"properties": p} for p in props]}
            return E()

        def batchUpdate(self, spreadsheetId, body):
            calls.append(body["requests"])

            class E:
                def execute(self_inner):
                    return {}
            return E()

    class P:
        booking_policy_json = _json.dumps({"handoff_sheet": {"person_tabs_v6": True}})

    class D:
        def commit(self):
            pass
    team = [{"name": "Neoso Walker", "row": 2, "active": True, "placeholder": False},
            {"name": "Johnny Weon", "row": 5, "active": True, "placeholder": False},
            {"name": "Someone New", "row": 7, "active": True, "placeholder": False}]
    prog = P()
    out = RS.ensure_person_tabs(D(), prog, C(), "sid", team)
    first = calls[0]
    ren = [q["updateSheetProperties"]["properties"] for q in first if "updateSheetProperties" in q]
    assert {"sheetId": 8, "title": "Johnny Weon", "hidden": False} in ren
    assert {"sheetId": 9, "title": "Person 5", "hidden": True} not in ren          # already right
    added = [q["addSheet"]["properties"] for q in first if "addSheet" in q]
    assert [a["sheetId"] for a in added] == [RS.PERSON_TAB_IDS[5]] and added[0]["title"] == "Someone New"
    assert out["created"] == [RS.PERSON_TAB_IDS[5]] and out["laid_out"] == [RS.PERSON_TAB_IDS[5]]
    assert "person_tabs_v6_ids" in _json.loads(prog.booking_policy_json)["handoff_sheet"]
    assert RS.team_row_of(RS.PERSON_TAB_IDS[5]) == 7 and RS.team_row_of(14) == 5


def test_a_note_a_person_writes_on_the_family_shows_on_the_follow_up(setter, sample_advisor):
    """Owner, Nov 25: 'book me in about two weeks' - the note typed in the app
    has to reach whoever works the follow-up on the sheet."""
    db, lead, conv = _case(setter, sample_advisor, state="waiting_future")
    db.add(LeadNote(organization_id=lead.organization_id, lead_id=lead.id, kind="internal",
                    body="Restland sheet: assigned to Kit (automatic)"))          # the system's own - left out
    db.add(LeadNote(organization_id=lead.organization_id, lead_id=lead.id, author_user_id=sample_advisor.id,
                    body="Wants to book in about two weeks - call first part of December"))
    db.commit()
    rows = {r[0]: r for r in RS.follow_up_rows(db, lead.organization_id)}
    why = rows["FU-" + conv.id][5]
    assert "NOTE " in why and "call first part of December" in why and "assigned to Kit" not in why
    ev = RS.followup_events(db, [conv])[conv.id]
    assert ev["note_at"] is not None
