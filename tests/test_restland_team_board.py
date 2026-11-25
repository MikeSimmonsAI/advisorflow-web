"""The team board (owner, Nov 25): the handoff sheet's work inside EvoSys -
everyone sees only their own, the owner sees all, and a change made on the
board takes the same road as a sheet edit (sheet cell, then the app)."""
import json
from datetime import datetime, timedelta

import pytest

from app.models.program_models import HandoffPendingEdit, ProgramTeamMember
from app.services.programs import booking_policy as BP
from app.services.programs import restland_sheet as RS
from app.services.programs import team_board as TB
from tests.test_outreach_program import _lead_for, program  # noqa: F401
from tests.test_restland_handoff_2026_11_22 import _appt, _board, _pad, _prog, _run_team
from tests.test_sci_appointment_setter import setter  # noqa: F401

NOW = datetime(2026, 11, 25, 18, 0)


def _serial(at):
    from app.services.volt.sheets_export import central_serial
    return int(central_serial(at))


def _booking(db, org, sample_advisor, src="L001", first="Tia", day=datetime(2026, 11, 30, 10, 0)):
    from app.models.models import BookingLink
    lead = _lead_for(db, org, sample_advisor, src, first=first)
    b = BookingLink(lead_id=lead.id, user_id=sample_advisor.id, status="booked", booked_time=day,
                    meeting_type=BP.OFFICE, appt_duration=45)
    db.add(b)
    db.commit()
    return lead, b


class _U:
    def __init__(self, uid):
        self.id = uid
        self.full_name = uid


def test_team_tab_people_become_team_members(setter):
    db = setter["db"]
    prog = _prog(setter)
    sheet = _board([("Ann", "TRUE"), ("Bob", "FALSE"), ("Person 3", "TRUE")])
    sheet["Team"][1][2] = "ann@example.com"
    _run_team(db, prog, sheet, [], [], NOW)
    got = {m.name: m for m in db.query(ProgramTeamMember).filter_by(organization_id=prog.organization_id)}
    assert set(got) == {"Ann", "Bob"}                            # placeholders are not people
    assert got["Ann"].email == "ann@example.com" and got["Ann"].active and not got["Bob"].active
    # a name taken off the Team tab is kept, just not active
    sheet["Team"] = sheet["Team"][:2]
    _run_team(db, prog, sheet, [], [], NOW + timedelta(minutes=2))
    db.refresh(got["Bob"])
    assert got["Bob"].active is False


def test_a_member_sees_only_their_own_and_only_once_the_board_is_open(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    prog = _prog(setter)
    _, b1 = _booking(db, org, sample_advisor, "L001", "Tia")
    _, b2 = _booking(db, org, sample_advisor, "L002", "Uma", day=datetime(2026, 12, 1, 10, 0))
    sheet = _board([("Ann", "TRUE"), ("Bob", "TRUE")])
    t = _serial(NOW)
    appts = [_appt(b1.id, t + 5), _appt(b2.id, t + 6)]
    _run_team(db, prog, sheet, appts, [], NOW)                   # one each
    ann = db.query(ProgramTeamMember).filter_by(organization_id=org.id, name="Ann").one()
    ann.user_id = "u-ann"
    db.commit()
    # not open to the team yet: Ann's own login is refused, the owner is not
    with pytest.raises(TB.BoardError) as e:
        TB.viewer(db, prog, _U("u-ann"), manager=False)
    assert e.value.status == 403
    assert TB.viewer(db, prog, _U("owner"), manager=True)["manager"] is True
    pol = json.loads(prog.booking_policy_json)
    pol["handoff_sheet"]["team_board_live"] = True
    prog.booking_policy_json = json.dumps(pol)
    db.commit()
    who = TB.viewer(db, prog, _U("u-ann"), manager=False)
    assert who == {"manager": False, "name": "Ann"}
    with pytest.raises(TB.BoardError):
        TB.viewer(db, prog, _U("stranger"), manager=False)        # no member linked to this login
    everyone = TB.work(db, prog)["appointments"]
    mine = TB.work(db, prog, person="Ann")["appointments"]
    assert len(everyone) == 2 and len(mine) == 1 and mine[0]["assigned"] == "Ann"
    other = next(a for a in everyone if a["assigned"] == "Bob")
    with pytest.raises(TB.BoardError) as e:
        TB.save(db, prog, who, "u-ann", RS.APPOINTMENTS, other["id"], {"outcome": "Showed"})
    assert e.value.status == 403                                 # not hers
    with pytest.raises(TB.BoardError) as e:
        TB.save(db, prog, who, "u-ann", RS.APPOINTMENTS, mine[0]["id"], {"assigned": "Bob"})
    assert e.value.status == 403                                 # only the owner reassigns


def test_a_board_change_reaches_the_sheet_and_the_app(setter, sample_advisor):
    from app.models.work_models import LeadNote
    db, org = setter["db"], setter["org"]
    prog = _prog(setter)
    lead, b = _booking(db, org, sample_advisor)
    sheet = _board([("Ann", "TRUE")])
    appts = [_appt(b.id, _serial(NOW) + 5)]
    _run_team(db, prog, sheet, appts, [], NOW)
    who = {"manager": True, "name": None}
    out = TB.save(db, prog, who, "owner", RS.APPOINTMENTS, b.id,
                  {"outcome": "Showed", "sold": "Yes", "sold_what": "Lawn crypt", "notes": "Great visit"})
    assert out["waiting"] and set(out["saved"]) == {"outcome", "sold", "sold_what", "notes"}
    shown = TB.work(db, prog)["appointments"][0]
    assert shown["outcome"] == "Showed" and shown["saving"]      # shows at once, marked as saving
    res = _run_team(db, prog, sheet, appts, [], NOW + timedelta(minutes=2))
    assert res["board_edits"] == 4
    main = _pad(sheet[RS.APPOINTMENTS][1], 19)
    assert (main[14], main[15], main[16], main[17]) == ("Showed", "Great visit", "Yes", "Lawn crypt")
    assert db.query(HandoffPendingEdit).filter(HandoffPendingEdit.applied_at.is_(None)).count() == 0
    notes = " ".join(n.body for n in db.query(LeadNote).filter_by(lead_id=lead.id))
    assert "SOLD: Lawn crypt" in notes
    assert TB.work(db, prog)["appointments"][0]["saving"] is False
    # saving the same again changes nothing
    assert TB.save(db, prog, who, "owner", RS.APPOINTMENTS, b.id, {"outcome": "Showed"})["waiting"] is False


def test_values_are_checked_and_written_the_sheet_s_way():
    names = ["Ann"]
    got = TB.clean(RS.FOLLOW_UPS, {"done": True, "appt_date": "2026-12-03", "appt_time": "5:30 PM",
                                   "appt_how": "Phone Call", "fu_result": "Left voicemail"}, False, names)
    assert got == {"done": "TRUE", "appt_date": "12/3/2026", "appt_time": "5:30 PM", "appt_how": "Phone Call",
                   "fu_result": "Left voicemail"}
    for bad in ({"appt_time": "7:15 PM"}, {"appt_how": "Zoom"}, {"fu_result": "Maybe"}, {"appt_date": "soon"}):
        with pytest.raises(TB.BoardError):
            TB.clean(RS.FOLLOW_UPS, bad, False, names)
    with pytest.raises(TB.BoardError):
        TB.clean(RS.APPOINTMENTS, {"outcome": "Great"}, True, names)
    with pytest.raises(TB.BoardError):
        TB.clean(RS.APPOINTMENTS, {"assigned": "Zed"}, True, names)  # not on the team
    assert TB.clean(RS.APPOINTMENTS, {"assigned": "Ann"}, True, names) == {"assigned": "Ann"}


def test_the_numbers_by_person():
    data = {"appointments": [
        {"assigned": "Ann", "upcoming": True, "date": "2026-11-25", "status": "Booked", "outcome": "", "sold": "",
         "last_update": None},
        {"assigned": "Ann", "upcoming": False, "date": "2026-11-20", "status": "Showed", "outcome": "Showed",
         "sold": "Yes", "last_update": "2026-11-20T15:00"}],
        "follow_ups": [
        {"assigned": "Bob", "open": True, "due_now": True, "fu_date": "11/24/2026",
         "fu_result": "Talked - booked appointment", "last_update": "2026-11-24T10:00"}]}
    s = TB.summary(None, None, data, today=datetime(2026, 11, 25).date())
    by = {p["name"]: p for p in s["by_person"]}
    assert by["Ann"]["appointments_upcoming"] == 1 and by["Ann"]["appointments_today"] == 1
    assert by["Ann"]["showed"] == 1 and by["Ann"]["sold"] == 1
    assert by["Bob"]["follow_ups_due_now"] == 1 and by["Bob"]["followed_up_7d"] == 1
    assert by["Bob"]["booked_from_follow_ups"] == 1 and s["total"]["follow_ups_open"] == 1
