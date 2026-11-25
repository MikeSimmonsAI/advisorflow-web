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
    # Owner, Nov 24: phone call and video call are separate choices for staff;
    # the old combined "phone or video" choice is kept only for older bookings.
    assert [o["key"] for o in BP.public_options(pol, staff=True)] == [BP.OFFICE, BP.HOME, BP.PHONE, BP.VIDEO]
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
    """A tiny in-memory Sheets values API: get / batchUpdate / update on A1 ranges."""
    def __init__(self, sheet):
        self.sheet, self.writes = sheet, []

    @staticmethod
    def _parse(rng):
        import re
        tab, a1 = rng.rsplit("!", 1)
        tab = tab.strip("'")
        m = re.match(r"([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?$", a1)

        def ci(c):
            n = 0
            for ch in c:
                n = n * 26 + ord(ch) - 64
            return n - 1
        c1, r1 = ci(m.group(1)), int(m.group(2)) - 1
        c2 = ci(m.group(3)) if m.group(3) else c1
        r2 = int(m.group(4)) - 1 if m.group(4) else r1
        return tab, r1, c1, r2, c2

    def get(self, spreadsheetId, range):
        tab, r1, c1, r2, c2 = self._parse(range)
        rows = self.sheet.get(tab, [])
        out = []
        for r in rows[r1:r2 + 1]:
            out.append([("" if v is None else v) for v in r[c1:c2 + 1]])
        while out and not any(str(v) != "" for v in out[-1]):
            out.pop()
        return _Exec({"values": out} if out else {})

    def _put(self, rng, values):
        tab, r1, c1, _r2, _c2 = self._parse(rng)
        rows = self.sheet.setdefault(tab, [])
        for di, vals in enumerate(values):
            while len(rows) <= r1 + di:
                rows.append([])
            cells = rows[r1 + di]
            for j, v in enumerate(vals):
                while len(cells) <= c1 + j:
                    cells.append("")
                cells[c1 + j] = v

    def batchUpdate(self, spreadsheetId, body):
        self.writes += body["data"]
        for d in body["data"]:
            self._put(d["range"], d["values"])
        return _Exec({})

    def update(self, spreadsheetId, range, valueInputOption, body):
        self._put(range, body["values"])
        return _Exec({})

    def batchGet(self, spreadsheetId, ranges):
        return _Exec({"valueRanges": [self.get(spreadsheetId, r).execute() for r in ranges]})


class _Exec:
    def __init__(self, out):
        self.out = out

    def execute(self):
        return self.out


class _Sheets:
    def __init__(self, sheet):
        self.v = _Values(sheet)
        self.sheet = sheet

    def values(self):
        return self.v

    def get(self, spreadsheetId, fields=None):
        titles = self.sheet.get("__titles__") or {}
        return _Exec({"sheets": [{"properties": {"sheetId": k, "title": v}} for k, v in titles.items()]})


class _Client:
    def __init__(self, sheet):
        self.s = _Sheets(sheet)

    def spreadsheets(self):
        return self.s


def _sheet():
    return {RS.APPOINTMENTS: [list(RS.APPT_COLUMNS) + list(RS.STAFF_COLUMNS[RS.APPOINTMENTS])],
            RS.FOLLOW_UPS: [list(RS.FU_COLUMNS) + list(RS.STAFF_COLUMNS[RS.FOLLOW_UPS]) + [RS.FU_RESULT_HEADER]
                            + list(RS.FU_WORK_COLUMNS)]}


def test_the_sync_writes_only_system_columns_and_keeps_the_teams_work():
    sheet = _sheet()
    sheet[RS.APPOINTMENTS].append(["b1", 1, 2, 3, "Mon", "Old Name", "", "", "At Restland", "", "Booked", "",
                                   "Person 1", True, "zoom link", "Showed", "great visit"])
    c = _Client(sheet)
    row = ["b1", 46000.5, 46001, 0.4166, "Tuesday", "Ada Lovelace", "2145550100", "", "Phone / Video Call", "",
           "Confirmed", "see you then"]
    new = ["b2", 46000.6, 46002, 0.5, "Wednesday", "Bo Diddley", "2145550101", "", "At Restland", "", "Booked", ""]
    out = RS.upsert(c, "sid", RS.APPOINTMENTS, [row, new])
    assert out["inserted"] == 1
    r1 = sheet[RS.APPOINTMENTS][1]
    assert r1[5] == "Ada Lovelace" and r1[8] == "Phone / Video Call"
    assert r1[12:17] == ["Person 1", True, "zoom link", "Showed", "great visit"]     # untouched
    assert sheet[RS.APPOINTMENTS][2][0] == "b2"
    assert all(len(w["values"][0]) <= len(RS.APPT_COLUMNS) for w in c.s.v.writes)


def test_a_changed_header_stops_the_sync():
    from app.services.volt.sheets_export import SheetsError
    sheet = _sheet()
    sheet[RS.FOLLOW_UPS][0][0] = "Something else"
    try:
        RS.upsert(_Client(sheet), "sid", RS.FOLLOW_UPS, [["FU-1"] + [""] * 8])
        raise AssertionError("should have refused")
    except SheetsError as e:
        assert e.category == "sheet_layout"


def test_an_update_only_row_never_creates_a_partial_row():
    sheet = _sheet()
    RS.upsert(_Client(sheet), "sid", RS.FOLLOW_UPS,
              [["TOLD-x", None, None, None, None, None, None, None, "Booked or handled"]])
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


def test_the_ai_offers_a_call_when_they_ask_or_cannot_come_in():
    # Owner, Nov 24 (replaces Nov 22's "only when they ask"): a family who is out
    # of town or lives far away is offered a call with real times; someone only
    # busy this week is not.
    p = ai_setter.SYSTEM_PROMPT if hasattr(ai_setter, "SYSTEM_PROMPT") else None
    import inspect
    src = p or inspect.getsource(ai_setter)
    assert "A PHONE OR VIDEO CALL WHEN THEY CAN'T COME IN" in src and "out of town" in src
    assert "Do NOT offer a call to someone who is just busy this week" in src


def test_the_board_uses_the_evosys_login_when_it_is_set(monkeypatch):
    monkeypatch.setenv("VOLT_SHEETS_SERVICE_ACCOUNT_JSON", json.dumps({"client_email": "old@x.iam.gserviceaccount.com"}))
    monkeypatch.delenv("EVOSYS_SHEETS_SERVICE_ACCOUNT_JSON", raising=False)
    assert RS.sync_account_email() == "old@x.iam.gserviceaccount.com"
    monkeypatch.setenv("EVOSYS_SHEETS_SERVICE_ACCOUNT_JSON", json.dumps({"client_email": "evosys-sync@x.iam.gserviceaccount.com"}))
    assert RS.sync_account_email() == "evosys-sync@x.iam.gserviceaccount.com"



# ── the team's side ─────────────────────────────────────────────────────────

def test_no_links_into_the_app_and_no_system_words():
    assert "Family File" not in RS.APPT_COLUMNS and "Family File" not in RS.FU_COLUMNS
    why = RS._plain_why("Needs a human - the AI could not move this to an appointment, so nothing was sent "
                        "(Cathy asked about the meeting format.). Contact them personally.")
    assert why == "Needs a person: Cathy asked about the meeting format." and "AI" not in why


def _c(r, i):
    return r[i] if len(r) > i else ""


def _pad(r, n=17):
    while len(r) < n:
        r.append("")
    return r


def _board(team):
    sheet = _sheet()
    sheet["Team"] = [["Name", "Phone", "Email", "Role", "Active?", "Status"]] + [
        [n, "", "", "", a, ""] for n, a in team]
    return sheet


def _appt(rid, day_serial, status="Booked"):
    return [rid, 46000.0, day_serial, 0.4, "Monday", "Fam " + rid, "", "", "At Restland", "", status, ""]


def _fu(rid, status="Open"):
    return [rid, 46000.0, "Fam " + rid, "", "", "Asked for a call back", "", "", status]


def _prog(setter):
    from app.models.program_models import OutreachProgram
    db, org = setter["db"], setter["org"]
    prog = db.query(OutreachProgram).filter_by(organization_id=org.id).first()
    prog.booking_policy_json = json.dumps({"handoff_sheet": {"id": "x" * 30, "formatted": True}})
    db.commit()
    return prog


def _run_team(db, prog, sheet, appts, fus, now):
    c = _Client(sheet)
    RS.upsert(c, "sid", RS.APPOINTMENTS, appts)
    RS.upsert(c, "sid", RS.FOLLOW_UPS, fus)
    return RS.team_pass(db, prog, c, "sid", {RS.APPOINTMENTS: appts, RS.FOLLOW_UPS: fus}, now=now)


def test_round_robin_skips_placeholders_and_unchecked_people(setter):
    db = setter["db"]
    prog = _prog(setter)
    now = datetime(2026, 11, 23, 15, 0)
    today = int(__import__("app.services.volt.sheets_export", fromlist=["x"]).central_serial(now))
    sheet = _board([("Ann", "TRUE"), ("Person 2", "TRUE"), ("Bob", "TRUE"), ("Cal", "FALSE")])
    appts = [_appt("a1", today + 1), _appt("a2", today + 2), _appt("a3", today + 3), _appt("old", today - 3)]
    fus = [_fu("FU-1"), _fu("FU-2", status="Closed")]
    out = _run_team(db, prog, sheet, appts, fus, now)
    got = {r[0]: _c(r, 12) for r in sheet[RS.APPOINTMENTS][1:]}
    assert [got["a1"], got["a2"], got["a3"]] == ["Ann", "Bob", "Ann"]
    assert got.get("old", "") == ""                                   # past: not assigned
    fu = {r[0]: _c(r, 9) for r in sheet[RS.FOLLOW_UPS][1:]}
    assert fu["FU-1"] == "Ann" and fu.get("FU-2", "") == ""          # follow-ups balance on their own; closed skipped
    assert out["assigned"] == 4
    status = {r[0]: _c(r, 5) for r in sheet["Team"][1:]}
    assert status["Ann"] == "In the rotation" and "name here" in status["Person 2"] and "unchecked" in status["Cal"]
    # the next run carries on from where it left off, and assigns nothing twice
    appts.append(_appt("a4", today + 4))
    out = _run_team(db, prog, sheet, appts, fus, now + timedelta(minutes=5))
    got = {r[0]: _c(r, 12) for r in sheet[RS.APPOINTMENTS][1:]}
    assert out["assigned"] == 1 and got["a4"] == "Bob"             # fewest upcoming appointments (Ann has 2)


def test_what_the_team_types_goes_into_the_app(setter, sample_advisor):
    from app.models.models import BookingLink
    from app.models.work_models import LeadNote
    db, org = setter["db"], setter["org"]
    prog = _prog(setter)
    lead = _lead_for(db, org, sample_advisor, "L001", first="Hal")
    b = BookingLink(lead_id=lead.id, user_id=sample_advisor.id, status="booked",
                    booked_time=datetime(2026, 11, 20, 10, 0), meeting_type=BP.OFFICE, appt_duration=45)
    db.add(b)
    db.commit()
    now = datetime(2026, 11, 23, 15, 0)
    sheet = _board([("Ann", "TRUE")])
    appts = [_appt(b.id, 46000)]
    _run_team(db, prog, sheet, appts, [], now)
    # the team fills in their columns
    row = _pad(sheet[RS.APPOINTMENTS][1])
    row[12:16] = ["Ann", "", "Showed", "Met with Hal and his wife; bought a marker."]
    out = _run_team(db, prog, sheet, appts, [], now + timedelta(minutes=5))
    assert out["changes"] == 1
    db.refresh(b)
    assert b.status == "completed"
    notes = [n.body for n in db.query(LeadNote).filter_by(lead_id=lead.id).all()]
    assert any("Showed" in n and "bought a marker" in n and "Ann" in n for n in notes)


def test_done_on_a_follow_up_closes_it_in_the_app(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    prog = _prog(setter)
    lead = _lead_for(db, org, sample_advisor, "L001", first="Ivy")
    _, conv = _run(db, lead, "call me please", _Fake([_d("callback", "Sure, I'll call you.", callback_window="today")]))
    assert conv.state == "callback_requested"
    now = datetime(2026, 11, 23, 15, 0)
    sheet = _board([("Ann", "TRUE")])
    fus = [_fu("FU-" + conv.id)]
    _run_team(db, prog, sheet, [], fus, now)
    row = sheet[RS.FOLLOW_UPS][1]
    while len(row) < 12:
        row.append("")
    row[10], row[11] = "TRUE", "Called her back, booking next week."
    _run_team(db, prog, sheet, [], fus, now + timedelta(minutes=5))
    db.refresh(conv)
    assert conv.state == "closed" and conv.next_followup_at is None


def test_a_week_without_updates_pauses_the_person_and_alerts_once(setter, monkeypatch):
    db = setter["db"]
    prog = _prog(setter)
    pages = []
    monkeypatch.setattr(RS, "_pause_alert", lambda db_, prog_, name, since, now: pages.append(name))
    now = datetime(2026, 11, 23, 15, 0)
    today = int(__import__("app.services.volt.sheets_export", fromlist=["x"]).central_serial(now))
    sheet = _board([("Ann", "TRUE"), ("Bob", "TRUE")])
    appts = [_appt("a1", today + 20), _appt("a2", today + 20)]
    _run_team(db, prog, sheet, appts, [], now)                         # a1 -> Ann, a2 -> Bob
    later = now + timedelta(days=8)
    row_b = _pad([r for r in sheet[RS.APPOINTMENTS][1:] if r[0] == "a2"][0])
    row_b[15] = "Left a voicemail"                                     # Bob kept working; Ann did nothing
    appts.append(_appt("a3", today + 20))
    out = _run_team(db, prog, sheet, appts, [], later)
    assert pages == ["Ann"] and out["paused"] == ["Ann"]
    got = {r[0]: _c(r, 12) for r in sheet[RS.APPOINTMENTS][1:]}
    assert got["a3"] == "Bob"                                          # paused people get nobody new
    status = {r[0]: _c(r, 5) for r in sheet["Team"][1:]}
    assert status["Ann"].startswith("Paused")
    _run_team(db, prog, sheet, appts, [], later + timedelta(minutes=5))
    assert pages == ["Ann"]                                            # one alert per pause
    row_a = _pad([r for r in sheet[RS.APPOINTMENTS][1:] if r[0] == "a1"][0])
    row_a[15] = "Called, no answer"                                    # Ann is back
    _run_team(db, prog, sheet, appts, [], later + timedelta(minutes=10))
    status = {r[0]: _c(r, 5) for r in sheet["Team"][1:]}
    assert status["Ann"] == "In the rotation"


# ── booking from a follow-up (owner, Nov 22 9:16 PM) ────────────────────────

def test_the_team_books_an_appointment_from_a_follow_up(setter, sample_advisor):
    from app.models.models import BookingLink
    from tests.test_sci_appointment_setter import SLOTS
    db, org = setter["db"], setter["org"]
    prog = _prog(setter)
    prog.booking_user_id = sample_advisor.id
    db.commit()
    lead = _lead_for(db, org, sample_advisor, "L001", first="Joy")
    _, conv = _run(db, lead, "call me please", _Fake([_d("callback", "Sure, I'll call you.", callback_window="today")]))
    now = datetime(2026, 11, 23, 15, 0)
    sheet = _board([("Ann", "TRUE")])
    fus = [_fu("FU-" + conv.id)]
    _run_team(db, prog, sheet, [], fus, now)
    row = _pad(sheet[RS.FOLLOW_UPS][1], 18)
    assert row[9] == "Ann"
    row[12], row[13], row[14] = "1/8/2030", "10:00 AM", "At Restland"
    with patch("app.services.programs.alert_ledger.page", return_value=[]):
        out = _run_team(db, prog, sheet, [], fus, now + timedelta(minutes=5))
    assert out["booked"] == 1
    assert row[16].startswith("Booked for Tue Jan 8")
    b = db.query(BookingLink).filter_by(lead_id=lead.id).one()
    assert b.booked_time == SLOTS[0] and b.status in ("booked", "confirmed")
    db.refresh(conv)
    assert conv.state == "booked"
    # the appointment then goes to the same person, not the next in line
    appts = [[b.id, 46000.0, 50000.0, 0.4, "Tuesday", "Joy", "", "", "At Restland", "", "Booked", ""]]
    sheet["Team"].append(["Bob", "", "", "", "TRUE", ""])
    _run_team(db, prog, sheet, appts, fus, now + timedelta(minutes=10))
    got = {r[0]: _c(r, 12) for r in sheet[RS.APPOINTMENTS][1:]}
    assert got[b.id] == "Ann"


def test_a_bad_booking_says_why_on_the_sheet(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    prog = _prog(setter)
    prog.booking_user_id = sample_advisor.id
    db.commit()
    lead = _lead_for(db, org, sample_advisor, "L001", first="Kay")
    _, conv = _run(db, lead, "call me please", _Fake([_d("callback", "Sure.", callback_window="today")]))
    now = datetime(2026, 11, 23, 15, 0)
    sheet = _board([("Ann", "TRUE")])
    fus = [_fu("FU-" + conv.id)]
    _run_team(db, prog, sheet, [], fus, now)
    row = _pad(sheet[RS.FOLLOW_UPS][1], 18)
    row[12], row[13] = "1/8/2030", "3:15 PM"                      # not an open time
    _run_team(db, prog, sheet, [], fus, now + timedelta(minutes=5))
    assert row[16].startswith("Not booked: that time is already taken")
    row[12], row[13] = "11/1/2026", "10:00 AM"                    # in the past
    _run_team(db, prog, sheet, [], fus, now + timedelta(minutes=10))
    assert row[16] == "Not booked: that date and time has already passed"


# ── Owner, Nov 23: any time a family asks for is bookable when free ─────────

def test_requested_off_grid_time_is_open_when_free():
    from datetime import date, datetime, timedelta
    from types import SimpleNamespace
    from app.services.programs import booking_policy as BP

    class Q:
        def __init__(self, rows): self.rows = rows
        def filter(self, *a, **k): return self
        def all(self): return self.rows

    class DB:
        def __init__(self, rows): self.rows = rows
        def query(self, *a): return Q(self.rows)

    pol = BP.parse({"starts": ["10:00", "11:30", "13:00", "14:30", "16:00"], "duration": 45, "gap_after": 45, "days": "0123456"})
    from app.services import no_contact_days as _ncd
    day = date.today() + timedelta(days=2)
    while _ncd.quiet_on(day):                     # not a closed holiday (e.g. Thanksgiving)
        day += timedelta(days=1)
    pol = dict(pol, days="0123456")
    adv = SimpleNamespace(id="adv")
    free = lambda *a, **k: ([], None)
    # Set starts only: 3:00 PM is not offered.
    res = BP.day_slots(DB([]), adv, day, pol, busy_reader=free)
    assert "%sT15:00:00" % day.isoformat() not in res["slots"]
    # A requested time: 3:00 PM is open when nothing is booked around it.
    assert BP.is_open(DB([]), adv, datetime(day.year, day.month, day.day, 15, 0), pol, busy_reader=free)
    # But not when it would run into a 2:30 appointment (45 min + 45 gap).
    held = SimpleNamespace(booked_time=datetime(day.year, day.month, day.day, 14, 30), appt_duration=45, id="b1")
    assert not BP.is_open(DB([held]), adv, datetime(day.year, day.month, day.day, 15, 0), pol, busy_reader=free)
    # Never outside the day's first/last start.
    assert not BP.is_open(DB([]), adv, datetime(day.year, day.month, day.day, 18, 0), pol, busy_reader=free)


# ── Owner, Nov 23 1:34 PM: the team logs each follow-up on the sheet ────────

def test_the_follow_up_log_goes_into_the_app_and_asks_for_a_date(setter, sample_advisor):
    from app.models.work_models import LeadNote
    db, org = setter["db"], setter["org"]
    prog = _prog(setter)
    lead = _lead_for(db, org, sample_advisor, "L001", first="Lou")
    _, conv = _run(db, lead, "call me please", _Fake([_d("callback", "Sure.", callback_window="today")]))
    now = datetime(2026, 11, 23, 15, 0)
    sheet = _board([("Ann", "TRUE")])
    fus = [_fu("FU-" + conv.id)]
    _run_team(db, prog, sheet, [], fus, now)
    row = _pad(sheet[RS.FOLLOW_UPS][1], 19)
    row[17], row[18] = "11/23/2026", "Left voicemail"
    out = _run_team(db, prog, sheet, [], fus, now + timedelta(minutes=5))
    assert out["changes"] == 1
    notes = [n.body for n in db.query(LeadNote).filter_by(lead_id=lead.id).all()]
    assert any("followed up on 11/23/2026: Left voicemail" in n and "Ann" in n for n in notes)
    # "booked" without a date and time: the sheet says what's missing
    row[18] = RS.WHAT_HAPPENED[0]
    _run_team(db, prog, sheet, [], fus, now + timedelta(minutes=10))
    assert row[16].startswith("Not booked yet: fill in Appointment Date")


def test_follow_up_log_columns_sit_right_of_the_booking_result():
    assert RS.FU_WORK_FIRST == 17                                  # column R
    reqs = json.dumps(RS.layout_requests())
    assert "Talked - booked appointment" in reqs


# ── Owner, Nov 23 2:33 PM: the sheet follows a booking within seconds ───────

def test_the_sheet_is_written_right_after_a_change_not_on_a_timer(setter, monkeypatch):
    db = setter["db"]
    prog = _prog(setter)
    calls = []
    monkeypatch.setattr(RS, "sync", lambda db, p, client=None: calls.append(1) or {"ok": 1})
    RS._last_run.clear(); RS._last_seen.clear()
    t0 = datetime(2026, 11, 23, 20, 0)
    RS.run(db, now=t0)
    assert len(calls) == 1
    # nothing changed: no write until the 2-minute read-back
    assert not RS.pending(db, now=t0 + timedelta(seconds=30))
    RS.run(db, now=t0 + timedelta(seconds=30))
    assert len(calls) == 1
    # something changed (a booking writes notifications / the conversation moves)
    monkeypatch.setattr(RS, "fingerprint", lambda db, org: ("changed",))
    assert not RS.pending(db, now=t0 + timedelta(seconds=10))     # never twice inside 20 seconds
    assert RS.pending(db, now=t0 + timedelta(seconds=45))
    RS.run(db, now=t0 + timedelta(seconds=45))
    assert len(calls) == 2
    # and the team's edits are read back at least every two minutes
    assert RS.pending(db, now=t0 + timedelta(seconds=45) + RS.EVERY)


def test_the_fingerprint_moves_when_a_booking_is_made(setter, sample_advisor):
    from app.models.reset_models import AppointmentNotification
    db, org = setter["db"], setter["org"]
    before = RS.fingerprint(db, org.id)
    db.add(AppointmentNotification(organization_id=org.id, lead_id="x", booking_id="b", channel="sms",
                                   purpose="confirmation", status="scheduled", due_at=datetime.utcnow(),
                                   version="v"))
    db.commit()
    assert RS.fingerprint(db, org.id) != before


# ── Owner, Nov 23 3:45 PM: Permission to Access names -> tagged Restland contacts ─

def _nf_board():
    sheet = _board([("Ann", "TRUE"), ("Bob", "TRUE")])
    sheet[RS.NEW_FAMILIES] = [list(RS.NF_COLUMNS)]
    return sheet


def test_new_families_become_tagged_contacts_kept_by_whoever_entered_them(setter, sample_advisor):
    from app.models.models import BookingLink, Lead
    from app.models.program_models import HandoffSheetRow
    from app.models.work_models import LeadNote
    db, org = setter["db"], setter["org"]
    prog = _prog(setter)
    src = _lead_for(db, org, sample_advisor, "L001", first="Hal")
    src.last_name = "Moore"
    db.add(BookingLink(lead_id=src.id, user_id=sample_advisor.id, status="booked",
                       booked_time=datetime(2026, 11, 24, 10, 0), meeting_type=BP.OFFICE, appt_duration=45))
    db.commit()
    sheet = _nf_board()
    sheet[RS.NEW_FAMILIES] += [
        ["Bob", "Hal Moore", "Jo", "Moore", "1 Elm St", "Dallas", "TX", "75238", "(214) 555-0161", "jo@x.com",
         "Daughter", "", "", "", ""],
        ["", "", "Kim", "", "", "", "", "", "", "", "", "", "", "", ""],                     # incomplete
    ]
    c = _Client(sheet)
    out = RS.new_families_pass(db, prog, c, "sid", ["Ann", "Bob"], now=datetime(2026, 11, 23, 22, 0))
    assert out["added"] == 1 and out["incomplete"] == 1, (out, sheet[RS.NEW_FAMILIES])
    row1, row2 = sheet[RS.NEW_FAMILIES][1], sheet[RS.NEW_FAMILIES][2]
    assert row1[12].startswith("Added") and row1[14].startswith("NF-")
    assert row2[12].startswith("Missing:") and "Entered By" in row2[12] and "Last Name" in row2[12]
    lead = db.query(Lead).filter(Lead.first_name == "Jo").one()
    assert (lead.status, lead.source, lead.phone, lead.city) == ("referral", "permission_to_access",
                                                                  "12145550161", "Dallas")
    assert "Hal Moore" in lead.source_detail and "Bob" in lead.source_detail
    rec = db.query(HandoffSheetRow).filter_by(row_id=row1[14]).one()
    assert rec.assigned_to == "Bob" and rec.tab == RS.NEW_FAMILIES       # kept by who entered it
    assert any("named Jo Moore" in n.body for n in db.query(LeadNote).filter_by(lead_id=src.id))
    # the next pass never adds the same row twice; the same phone on a new row is a duplicate
    sheet[RS.NEW_FAMILIES].append(["Ann", "", "Joanne", "Moore", "", "", "", "", "214-555-0161", "", "", "", "",
                                   "", ""])
    out = RS.new_families_pass(db, prog, c, "sid", ["Ann", "Bob"], now=datetime(2026, 11, 23, 22, 5))
    assert out["added"] == 0 and out["duplicates"] == 1
    assert db.query(Lead).filter(Lead.last_name == "Moore", Lead.first_name.in_(["Jo", "Joanne"])).count() == 1
    assert sheet[RS.NEW_FAMILIES][3][12].startswith("Already in the system")
    # an edit to an added row updates the contact
    row1[7] = "75240"
    RS.new_families_pass(db, prog, c, "sid", ["Ann", "Bob"], now=datetime(2026, 11, 23, 22, 10))
    db.refresh(lead)
    assert lead.zip_code == "75240"


def test_a_referral_is_never_in_the_daily_batch_pool(setter, sample_advisor):
    from app.models.models import Lead
    from app.services.programs import daily_batch
    db, org = setter["db"], setter["org"]
    prog = _prog(setter)
    sheet = _nf_board()
    sheet[RS.NEW_FAMILIES].append(["Ann", "", "Lee", "Park", "", "", "", "", "(214) 555-0199", "", "", "", "", "",
                                   ""])
    RS.new_families_pass(db, prog, _Client(sheet), "sid", ["Ann"], now=datetime(2026, 11, 23, 22, 0))
    lead = db.query(Lead).filter(Lead.first_name == "Lee").one()
    assert lead.id not in [e["lead_id"] for e in daily_batch.pool(db, prog)["entries"]]


def test_a_follow_up_for_an_entered_family_goes_to_whoever_entered_it(setter, sample_advisor):
    db = setter["db"]
    prog = _prog(setter)
    from app.models.models import Lead
    sheet = _nf_board()
    sheet[RS.NEW_FAMILIES].append(["Bob", "", "Max", "Ray", "", "", "", "", "(214) 555-0177", "", "", "", "", "",
                                   ""])
    RS.new_families_pass(db, prog, _Client(sheet), "sid", ["Ann", "Bob"], now=datetime(2026, 11, 23, 22, 0))
    lead = db.query(Lead).filter(Lead.first_name == "Max").one()
    _, conv = _run(db, lead, "call me please", _Fake([_d("callback", "Sure.", callback_window="today")]))
    now = datetime(2026, 11, 23, 22, 5)
    fus = [_fu("FU-" + conv.id), _fu("FU-other")]
    _run_team(db, prog, sheet, [], fus, now)
    got = {r[0]: _c(r, 9) for r in sheet[RS.FOLLOW_UPS][1:]}
    assert got["FU-" + conv.id] == "Bob"            # not the rotation's next person


# ── Owner, Nov 23 4:41 PM: "Confirmed With Family?" removed; columns found by header ─

def test_team_columns_are_read_by_header_not_position():
    sheet = _sheet()
    assert "Confirmed With Family?" not in RS.STAFF_COLUMNS[RS.APPOINTMENTS]
    # an old sheet that still has the column (or any extra column) reads right
    sheet[RS.APPOINTMENTS][0] = list(RS.APPT_COLUMNS) + ["Assigned To", "Confirmed With Family?",
                                                           "Remote Call Link / Details", "Outcome", "Team Notes"]
    sheet[RS.APPOINTMENTS].append(["b1"] + [""] * 11 + ["Ann", "TRUE", "zoom.us/j/1", "Showed", "good visit"])
    got = RS.read_staff(_Client(sheet), "sid", RS.APPOINTMENTS)["b1"]
    assert (got["assigned"], got["link"], got["outcome"], got["notes"]) == ("Ann", "zoom.us/j/1", "Showed", "good visit")
    # and the sheet without it
    sheet[RS.APPOINTMENTS] = [list(RS.APPT_COLUMNS) + list(RS.STAFF_COLUMNS[RS.APPOINTMENTS]),
                              ["b1"] + [""] * 11 + ["Ann", "zoom.us/j/1", "Showed", "good visit"]]
    got = RS.read_staff(_Client(sheet), "sid", RS.APPOINTMENTS)["b1"]
    assert (got["assigned"], got["link"], got["outcome"], got["notes"]) == ("Ann", "zoom.us/j/1", "Showed", "good visit")


# ── Owner, Nov 23 4:55 PM: Sold / What Was Sold, and when the team updated a row ─

def test_sold_goes_into_the_app_and_every_team_change_is_time_stamped(setter, sample_advisor):
    from app.models.models import BookingLink
    from app.models.work_models import LeadNote
    from app.services.volt.sheets_export import central_serial
    db, org = setter["db"], setter["org"]
    prog = _prog(setter)
    lead = _lead_for(db, org, sample_advisor, "L001", first="Sal")
    b = BookingLink(lead_id=lead.id, user_id=sample_advisor.id, status="booked",
                    booked_time=datetime(2026, 11, 20, 10, 0), meeting_type=BP.OFFICE, appt_duration=45)
    db.add(b)
    db.commit()
    sheet = _board([("Ann", "TRUE")])
    sheet[RS.APPOINTMENTS][0] = sheet[RS.APPOINTMENTS][0] + [RS.LAST_UPDATE_HEADER]
    appts = [_appt(b.id, 46000)]
    now = datetime(2026, 11, 23, 23, 0)
    _run_team(db, prog, sheet, appts, [], now)
    row = _pad(sheet[RS.APPOINTMENTS][1], 19)
    assert row[18] == ""                                         # nothing typed yet: no stamp
    row[12], row[16], row[17] = "Ann", "Yes", "Two-space lawn crypt + markers"
    out = _run_team(db, prog, sheet, appts, [], now + timedelta(minutes=2))
    assert out["stamped"] == 1 and row[18] == central_serial(now + timedelta(minutes=2))
    notes = [n.body for n in db.query(LeadNote).filter_by(lead_id=lead.id).all()]
    assert any("SOLD: Two-space lawn crypt + markers" in n for n in notes)



# ── Owner, Nov 23 4:55 PM: each person updates from their own tab ──────────

def test_a_person_tab_lists_their_work_and_their_edits_reach_the_main_tab_and_the_app(setter, sample_advisor):
    from app.models.models import BookingLink
    from app.models.work_models import LeadNote
    db, org = setter["db"], setter["org"]
    prog = _prog(setter)
    lead = _lead_for(db, org, sample_advisor, "L001", first="Tia")
    b = BookingLink(lead_id=lead.id, user_id=sample_advisor.id, status="booked",
                    booked_time=datetime(2026, 11, 30, 10, 0), meeting_type=BP.OFFICE, appt_duration=45)
    db.add(b)
    db.commit()
    now = datetime(2026, 11, 23, 23, 0)
    today = int(__import__("app.services.volt.sheets_export", fromlist=["x"]).central_serial(now))
    sheet = _board([("Ann", "TRUE"), ("Bob", "TRUE")])
    fut = RS.fu_tab_title("Ann")
    sheet["__titles__"] = {3: "Ann", 4: "Bob", 11: fut, 12: RS.fu_tab_title("Bob")}
    appts = [_appt(b.id, today + 7)]
    fus = [_fu("FU-x1")]
    _run_team(db, prog, sheet, appts, fus, now)                   # appointment -> Ann, follow-up -> Ann
    ann = sheet["Ann"]
    a0, f0 = RS.PT_APPT_FIRST - 1, RS.FT_FIRST - 1
    assert ann[a0][0] == b.id and ann[a0][3] == "Fam " + b.id        # MY APPOINTMENTS on her tab
    assert len(ann) <= RS.PT_APPT_LAST_ROW                           # and nothing below them
    assert sheet[fut][f0][0] == "FU-x1"                              # MY FOLLOW-UPS on her own Follow-Ups tab
    # Owner, Nov 24: what she types on her Follow-Ups tab reaches the main tab
    fu_row = _pad(sheet[fut][f0], 15)
    fu_row[13] = "Left her a voicemail"
    out = _run_team(db, prog, sheet, appts, fus, now + timedelta(minutes=1))
    assert out["person_tab_edits"] == 1
    main_fu = sheet[RS.FOLLOW_UPS][1]
    assert "Left her a voicemail" in [str(v) for v in main_fu]
    # Ann types on HER tab: outcome, sold, what was sold, notes
    row = _pad(ann[a0], 14)
    row[8], row[9], row[10], row[12] = "Showed", "Yes", "Lawn crypt", "Great visit"
    out = _run_team(db, prog, sheet, appts, fus, now + timedelta(minutes=2))
    assert out["person_tab_edits"] == 4
    main = _pad(sheet[RS.APPOINTMENTS][1], 19)
    assert (main[14], main[16], main[17], main[15]) == ("Showed", "Yes", "Lawn crypt", "Great visit")
    notes = " ".join(n.body for n in db.query(LeadNote).filter_by(lead_id=lead.id))
    assert "SOLD: Lawn crypt" in notes and "Great visit" in notes
    # a quiet pass keeps her entries
    _run_team(db, prog, sheet, appts, fus, now + timedelta(minutes=4))
    assert sheet["Ann"][a0][10] == "Lawn crypt"


def test_person_tab_layout_is_valid_and_applied_once(setter):
    db = setter["db"]
    prog = _prog(setter)
    reqs = RS.person_tab_layout_requests()
    assert reqs and all(len(r) == 1 for r in reqs)
    calls = []

    class C:
        def spreadsheets(self):
            return self

        def batchUpdate(self, spreadsheetId, body):
            calls.append(body["requests"])
            return _Exec({})

        def get(self, spreadsheetId, fields=None):
            return _Exec({"sheets": [{"properties": {"sheetId": 3}, "bandedRanges": [{"bandedRangeId": 77}],
                                      "conditionalFormats": [{}, {}]},
                                     {"properties": {"sheetId": 1}, "bandedRanges": [{"bandedRangeId": 5}]}]})
    assert RS.ensure_person_tabs(db, prog, C(), "sid") is True
    assert calls[0][0] == {"deleteBanding": {"bandedRangeId": 77}}               # only the person tab's
    assert calls[0][1] == calls[0][2] == {"deleteConditionalFormatRule": {"sheetId": 3, "index": 0}}
    assert RS.ensure_person_tabs(db, prog, C(), "sid") is False and len(calls) == 1



def test_new_families_tab_is_found_by_id_after_a_rename(setter, sample_advisor):
    from app.models.models import Lead
    db = setter["db"]
    prog = _prog(setter)
    sheet = _board([("Ann", "TRUE")])
    sheet["6 Block"] = [list(RS.NF_COLUMNS),
                        ["Ann", "", "Rae", "Lin", "", "", "", "", "(214) 555-0188", "", "", "", "", "", ""]]
    sheet["__titles__"] = {RS.TAB_IDS[RS.NEW_FAMILIES]: "6 Block"}
    out = RS.new_families_pass(db, prog, _Client(sheet), "sid", ["Ann"], now=datetime(2026, 11, 23, 23, 0))
    assert out["added"] == 1 and sheet["6 Block"][1][12].startswith("Added")
    assert db.query(Lead).filter(Lead.first_name == "Rae").count() == 1



# ── Owner, Nov 23 5:53 PM: "Kitty Arnold is sitting appointmentless" ───────

def test_a_newcomer_gets_new_appointments_until_caught_up_and_follow_ups_balance_separately(setter):
    db = setter["db"]
    prog = _prog(setter)
    now = datetime(2026, 11, 23, 23, 0)
    today = int(__import__("app.services.volt.sheets_export", fromlist=["x"]).central_serial(now))
    sheet = _board([("Ann", "TRUE"), ("Bob", "TRUE")])
    appts = [_appt("a%d" % i, today + 1) for i in range(4)]
    fus = [_fu("FU-%d" % i) for i in range(4)]
    _run_team(db, prog, sheet, appts, fus, now)                   # 2 + 2 each
    # Kit joins; three new appointments and two new follow-ups come in
    sheet["Team"].append(["Kit", "", "", "", "TRUE", ""])
    appts += [_appt("n%d" % i, today + 2) for i in range(3)]
    fus += [_fu("FU-n%d" % i) for i in range(2)]
    _run_team(db, prog, sheet, appts, fus, now + timedelta(minutes=2))
    got = {r[0]: _c(r, 12) for r in sheet[RS.APPOINTMENTS][1:]}
    assert [got["n0"], got["n1"]] == ["Kit", "Kit"]                  # catches up first
    assert got["n2"] in ("Ann", "Bob", "Kit")
    fu = {r[0]: _c(r, 9) for r in sheet[RS.FOLLOW_UPS][1:]}
    assert [fu["FU-n0"], fu["FU-n1"]] == ["Kit", "Kit"]



def test_person_tab_header_rows_heal_themselves(setter):
    db = setter["db"]
    prog = _prog(setter)
    now = datetime(2026, 11, 23, 23, 0)
    sheet = _board([("Ann", "TRUE")])
    fut = RS.fu_tab_title("Ann")
    sheet["__titles__"] = {3: "Ann", 11: fut}
    _run_team(db, prog, sheet, [], [], now)
    ann, afu = sheet["Ann"], sheet[fut]
    assert ann[RS.PT_APPT_HEAD - 1][3] == "Family" and afu[RS.FT_BANNER - 1][0].startswith("MY FOLLOW-UPS")
    afu[RS.FT_HEAD - 1][2] = "junk"                                  # something overwrote a header
    out = _run_team(db, prog, sheet, [], [], now + timedelta(minutes=2))
    assert out["person_tab_headers_fixed"] == 1 and sheet[fut][RS.FT_HEAD - 1][2] == "Family"


def test_each_person_gets_their_own_follow_ups_tab(setter):
    """Owner, Nov 24: 'Kitty Arnold - Follow-Ups' made next to her tab, renamed
    with the Team name, hidden for a placeholder; laid out once."""
    db = setter["db"]
    prog = _prog(setter)
    calls = []
    props = {3: {"sheetId": 3, "title": "Ann", "index": 4}, 4: {"sheetId": 4, "title": "Person 2", "index": 5},
             12: {"sheetId": 12, "title": "Old - Follow-Ups", "index": 6}}

    class C:
        def spreadsheets(self):
            return self

        def batchUpdate(self, spreadsheetId, body):
            calls.append(body["requests"])
            return _Exec({})

        def get(self, spreadsheetId, fields=None):
            return _Exec({"sheets": [{"properties": p} for p in props.values()]})
    team = [{"name": "Ann", "row": 2, "active": True, "placeholder": False},
            {"name": "Person 2", "row": 3, "active": True, "placeholder": True}]
    out = RS.ensure_fu_tabs(db, prog, C(), "sid", team)
    first = calls[0]
    assert {"addSheet": {"properties": {"sheetId": 11, "title": RS.fu_tab_title("Ann"), "hidden": False,
                                        "index": 5}}} in first
    assert {"updateSheetProperties": {"properties": {"sheetId": 12, "title": RS.fu_tab_title("Person 2"),
                                                     "hidden": True}, "fields": "title,hidden"}} in first
    assert out["created"] == [11] and sorted(out["laid_out"]) == [11, 12]
    calls.clear()
    props[11] = {"sheetId": 11, "title": RS.fu_tab_title("Ann"), "index": 5}
    props[12] = {"sheetId": 12, "title": RS.fu_tab_title("Person 2"), "index": 7, "hidden": True}
    out = RS.ensure_fu_tabs(db, prog, C(), "sid", team)
    assert calls == [] and out["laid_out"] == []                     # nothing to do the second time
