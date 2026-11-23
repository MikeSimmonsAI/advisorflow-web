"""Owner, Nov 22 evening (Restland handoff): a follow-up list that never loses
"after the holidays", remote calls for out-of-town families, and the team's
handoff sheet fed by the system. Synthetic data only."""
import json
from datetime import date, datetime, timedelta
from unittest.mock import patch

from app.services.programs import ai_setter, appointments as A
from app.services.programs import booking_policy as BP
from app.services.programs import holidays as H
from app.services.programs import restland_sheet as RS
from tests.test_sci_appointment_setter import _Fake, _d, _run, setter  # noqa: F401
from tests.test_outreach_program import _lead_for, program  # noqa: F401


# ── holidays ────────────────────────────────────────────────────────────────

def test_follow_up_days_skip_holidays_and_sundays():
    assert H.thanksgiving(2026) == date(2026, 11, 26)
    assert H.thanksgiving(2027) == date(2027, 11, 25)
    assert H.next_working_day(date(2026, 11, 26)) == date(2026, 11, 28)      # Thu + Fri -> Sat
    assert H.next_working_day(date(2026, 12, 24)) == date(2026, 12, 26)
    assert H.next_working_day(date(2026, 12, 31)) == date(2027, 1, 2)
    assert H.next_working_day(date(2026, 11, 29)) == date(2026, 11, 30)      # Sunday -> Monday
    assert H.next_working_day(date(2026, 12, 2)) == date(2026, 12, 2)        # an ordinary day stays


def test_the_ai_follow_up_date_never_lands_on_a_holiday(monkeypatch):
    monkeypatch.setattr(A, "_today_central", lambda: datetime(2026, 11, 22, 19, 0))
    when = ai_setter._parse_follow_up("2026-12-25", datetime(2026, 11, 23, 1, 0))
    assert when == datetime(2026, 12, 26, 15, 0)
    assert ai_setter._parse_follow_up("2026-11-26", None).date() == date(2026, 11, 28)


# ── the follow-up list ──────────────────────────────────────────────────────

def test_after_the_holidays_comes_back_on_its_date_and_offers_times(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Eve")
    lead.is_test = True                                   # simulated delivery, nobody paged
    db.commit()
    soon = (A._today_central().date() + timedelta(days=12)).isoformat()
    fake = _Fake([_d("follow_up_later", "Of course, I'll check back after the holidays.",
                     follow_up_date=soon, follow_up_about="after the holidays")])
    _, conv = _run(db, lead, "Please reach back out after the holidays", fake)
    assert conv.state == "waiting_future" and conv.next_followup_at is not None
    due = conv.next_followup_at
    # The family wrote last ("after the holidays") - that must not cancel it.
    fu = _Fake([_d("offer_times", "Hi Eve, checking back as promised. Would {TIMES} work for a visit?")])
    with patch.object(ai_setter, "_get_client", return_value=fu):
        out = ai_setter.followup_sweep(db, now=due + timedelta(minutes=1))
    assert len(fu.calls) == 1 and out["sent"] + out["drafted"] == 1
    db.refresh(conv)
    assert conv.state == "offered"


def test_a_follow_up_that_is_not_due_yet_waits(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Fay")
    soon = (A._today_central().date() + timedelta(days=20)).isoformat()
    _, conv = _run(db, lead, "Call me in a few weeks", _Fake([_d("follow_up_later", "Sure thing.",
                                                                    follow_up_date=soon)]))
    fu = _Fake([_d("offer_times", "Would {TIMES} work?")])
    with patch.object(ai_setter, "_get_client", return_value=fu):
        ai_setter.followup_sweep(db, now=datetime.utcnow() + timedelta(days=2))
    assert fu.calls == []
    db.refresh(conv)
    assert conv.state == "waiting_future"


# ── remote calls ────────────────────────────────────────────────────────────

def test_remote_call_is_a_booking_choice_with_its_own_wording():
    pol = BP.parse(json.dumps(BP.RESTLAND_POLICY))
    # Only when the family asks: staff can book it, the family's page never shows it.
    assert [o["key"] for o in BP.public_options(pol)] == [BP.OFFICE, BP.HOME]
    assert [o["key"] for o in BP.public_options(pol, staff=True)] == [BP.OFFICE, BP.HOME, BP.REMOTE]
    choice = BP.meeting_choice(pol, BP.REMOTE, None)
    assert choice["key"] == BP.REMOTE and "phone or video" in choice["appt_label"]

    class B:
        meeting_type, visit_address = BP.REMOTE, None
    assert "phone or video call" in BP.place_text(B, pol)


def test_remote_reminders_never_point_to_the_office():
    from app.services.programs import appointment_notifications as AN
    f = {"remote": True, "home": False, "advisor_first": "Mike", "address": BP.RESTLAND_ADDRESS, "advisor": "Mike S"}
    assert "phone or video call" in AN._where(f) and "Greenville" not in AN._where(f)


# ── the sheet ───────────────────────────────────────────────────────────────

class _Values:
    def __init__(self, sheet):
        self.sheet, self.writes = sheet, []

    def get(self, spreadsheetId, range):
        tab, a1 = range.split("!")
        tab = tab.strip("'")
        rows = self.sheet[tab]
        if a1.startswith("A1:"):
            end = a1.split(":")[1].rstrip("0123456789")
            return _Exec({"values": [rows[0][:ord(end) - 64]]})
        return _Exec({"values": [[r[0]] for r in rows[1:]]})

    def batchUpdate(self, spreadsheetId, body):
        self.writes += body["data"]
        for d in body["data"]:
            tab, a1 = d["range"].split("!")
            tab = tab.strip("'")
            import re
            m = re.match(r"([A-Z]+)(\d+)", a1)
            col = ord(m.group(1)[0]) - 65
            row = int(m.group(2)) - 1
            rows = self.sheet[tab]
            while len(rows) <= row:
                rows.append([""] * 20)
            for j, v in enumerate(d["values"][0]):
                cells = rows[row]
                while len(cells) <= col + j:
                    cells.append("")
                cells[col + j] = v
        return _Exec({})


class _Exec:
    def __init__(self, out):
        self.out = out

    def execute(self):
        return self.out


class _Sheets:
    def __init__(self, sheet):
        self.v = _Values(sheet)

    def values(self):
        return self.v


class _Client:
    def __init__(self, sheet):
        self.s = _Sheets(sheet)

    def spreadsheets(self):
        return self.s


def _sheet():
    return {RS.APPOINTMENTS: [list(RS.APPT_COLUMNS) + list(RS.STAFF_COLUMNS[RS.APPOINTMENTS])],
            RS.FOLLOW_UPS: [list(RS.FU_COLUMNS) + list(RS.STAFF_COLUMNS[RS.FOLLOW_UPS])]}


def test_the_sync_writes_only_system_columns_and_keeps_the_teams_work():
    sheet = _sheet()
    sheet[RS.APPOINTMENTS].append(["b1", 1, 2, 3, "Mon", "Old Name", "", "", "At Restland", "", "Booked", "", "",
                                   "Person 1", True, "zoom link", "Showed", "great visit"])
    c = _Client(sheet)
    row = ["b1", 46000.5, 46001, 0.4166, "Tuesday", "Ada Lovelace", "2145550100", "", "Phone / Video Call", "",
           "Confirmed", "see you then", ""]
    new = ["b2", 46000.6, 46002, 0.5, "Wednesday", "Bo Diddley", "2145550101", "", "At Restland", "", "Booked",
           "", ""]
    out = RS.upsert(c, "sid", RS.APPOINTMENTS, [row, new])
    assert out["inserted"] == 1
    r1 = sheet[RS.APPOINTMENTS][1]
    assert r1[5] == "Ada Lovelace" and r1[8] == "Phone / Video Call"
    assert r1[13:18] == ["Person 1", True, "zoom link", "Showed", "great visit"]     # untouched
    assert sheet[RS.APPOINTMENTS][2][0] == "b2"
    assert all(len(w["values"][0]) <= len(RS.APPT_COLUMNS) for w in c.s.v.writes)


def test_a_changed_header_stops_the_sync():
    from app.services.volt.sheets_export import SheetsError
    sheet = _sheet()
    sheet[RS.FOLLOW_UPS][0][0] = "Something else"
    try:
        RS.upsert(_Client(sheet), "sid", RS.FOLLOW_UPS, [["FU-1"] + [""] * 9])
        raise AssertionError("should have refused")
    except SheetsError as e:
        assert e.category == "sheet_layout"


def test_an_update_only_row_never_creates_a_partial_row():
    sheet = _sheet()
    RS.upsert(_Client(sheet), "sid", RS.FOLLOW_UPS,
              [["TOLD-x", None, None, None, None, None, None, None, "Booked or handled", None]])
    assert len(sheet[RS.FOLLOW_UPS]) == 1


def test_sheet_id_is_read_from_the_program_and_validated():
    class P:
        booking_policy_json = json.dumps({"handoff_sheet": {"id": "1o8gPHz0-tLcrN_IxSQsSQE3Mt6IUX0bQ1U4jlmDfPgo"}})
    assert RS.sheet_id(P) == "1o8gPHz0-tLcrN_IxSQsSQE3Mt6IUX0bQ1U4jlmDfPgo"
    P.booking_policy_json = json.dumps({"handoff_sheet": {"id": "../etc"}})
    assert RS.sheet_id(P) is None


def test_layout_requests_cover_every_tab_and_use_valid_ranges():
    reqs = RS.layout_requests()
    sheets = set()
    for r in reqs:
        body = next(iter(r.values()))
        rng = body.get("range") if isinstance(body, dict) else None
        if isinstance(rng, dict) and "sheetId" in rng:
            sheets.add(rng["sheetId"])
            if "startRowIndex" in rng:
                assert rng["endRowIndex"] > rng["startRowIndex"] >= 0
                assert rng["endColumnIndex"] > rng["startColumnIndex"] >= 0
    assert sheets >= set(RS.TAB_IDS.values())
    assert sum(1 for r in reqs if "addChart" in r) == 1


def test_rows_are_built_from_the_system(setter, sample_advisor):
    from app.models.models import BookingLink
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Gus")
    soon = (A._today_central().date() + timedelta(days=9)).isoformat()
    _, conv = _run(db, lead, "After the holidays please", _Fake([_d("follow_up_later", "Sure.", follow_up_date=soon,
                                                                       follow_up_about="after the holidays")]))
    rows = RS.follow_up_rows(db, org.id)
    mine = [r for r in rows if r[0] == "FU-" + conv.id]
    assert mine and mine[0][5].startswith("Asked us to follow up later") and mine[0][8] == "Open"
    assert mine[0][6] != ""                                                   # the Follow Up On date
    assert "After the holidays please" in mine[0][7]
    b = BookingLink(lead_id=lead.id, user_id=sample_advisor.id, status="booked",
                    booked_time=datetime(2026, 12, 1, 10, 0), meeting_type=BP.REMOTE)
    db.add(b)
    db.commit()
    appt = [r for r in RS.appointment_rows(db, org.id) if r[0] == b.id][0]
    assert len(appt) == len(RS.APPT_COLUMNS)
    assert appt[4] == "Tuesday" and appt[8] == "Phone / Video Call" and appt[10] == "Booked"
    assert abs(appt[3] - 10 / 24.0) < 1e-6
    # Once booked, the follow-up row stays and its status changes.
    conv.state = "booked"
    db.commit()
    again = [r for r in RS.follow_up_rows(db, org.id, ["FU-" + conv.id]) if r[0] == "FU-" + conv.id]
    assert again and again[0][8] == "Booked"


def test_phone_numbers_read_like_phone_numbers():
    assert RS._phone("12142403434") == "(214) 240-3434"
    assert RS._phone("+1 (214) 240-3434") == "(214) 240-3434"
    assert RS._phone("2142403434") == "(214) 240-3434"
    assert RS._phone(None) == ""


def test_the_ai_never_suggests_a_call_and_books_one_only_on_request():
    p = ai_setter.SYSTEM_PROMPT if hasattr(ai_setter, "SYSTEM_PROMPT") else None
    import inspect
    src = p or inspect.getsource(ai_setter)
    assert "ONLY WHEN THEY ASK" in src and "never suggest one yourself" in src
    assert "lock in a call instead of waiting" not in src
