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


def test_the_ai_never_suggests_a_call_and_books_one_only_on_request():
    p = ai_setter.SYSTEM_PROMPT if hasattr(ai_setter, "SYSTEM_PROMPT") else None
    import inspect
    src = p or inspect.getsource(ai_setter)
    assert "ONLY WHEN THEY ASK" in src and "never suggest one yourself" in src
    assert "lock in a call instead of waiting" not in src


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
    assert fu["FU-1"] == "Bob" and fu.get("FU-2", "") == ""          # continues the turn; closed skipped
    assert out["assigned"] == 4
    status = {r[0]: _c(r, 5) for r in sheet["Team"][1:]}
    assert status["Ann"] == "In the rotation" and "name here" in status["Person 2"] and "unchecked" in status["Cal"]
    # the next run carries on from where it left off, and assigns nothing twice
    appts.append(_appt("a4", today + 4))
    out = _run_team(db, prog, sheet, appts, fus, now + timedelta(minutes=5))
    got = {r[0]: _c(r, 12) for r in sheet[RS.APPOINTMENTS][1:]}
    assert out["assigned"] == 1 and got["a4"] == "Ann"


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
    row[12:17] = ["Ann", "TRUE", "", "Showed", "Met with Hal and his wife; bought a marker."]
    out = _run_team(db, prog, sheet, appts, [], now + timedelta(minutes=5))
    assert out["changes"] == 1
    db.refresh(b)
    assert b.status == "completed" and b.confirmed_at is not None
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
    row_b[16] = "Left a voicemail"                                     # Bob kept working; Ann did nothing
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
    row_a[16] = "Called, no answer"                                    # Ann is back
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
