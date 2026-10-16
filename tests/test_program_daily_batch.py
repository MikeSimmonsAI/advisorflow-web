"""Daily batch: a manager switches it on once (typed DAILY <n>), then every
sending day the program approves the next n people in that manager's name,
once per day, not before the start time, only on the listed weekdays."""
from datetime import datetime

from app.models.program_models import OutreachProgram
from app.services.programs import daily_batch, send_approval
from tests.test_program_load_source import _program
from tests.test_switch_import_booking import _h
from tests.test_outreach_program import program  # noqa: F401 (fixture)

# 2026-10-14 is a Wednesday. 15:00 UTC = 10:00 AM Central (CDT).
WED_10AM = datetime(2026, 10, 14, 15, 0)
WED_8AM = datetime(2026, 10, 14, 13, 0)
SAT_10AM = datetime(2026, 10, 17, 15, 0)


def _prog(db, org):
    return db.query(OutreachProgram).filter_by(organization_id=org.id).one()


def test_turning_on_needs_typed_confirmation(client, db_session, sample_org):
    admin = _program(db_session, sample_org)
    h = _h(db_session, admin)
    assert client.post("/program/daily-batch", headers=h, json={"count": 66}).status_code == 422
    r = client.post("/program/daily-batch", headers=h, json={"count": 66, "confirm": "DAILY 66", "spacing_minutes": 8})
    assert r.status_code == 200, r.text
    assert r.json()["on"] and r.json()["count"] == 66
    p = _prog(db_session, sample_org)
    assert p.daily_batch_by == admin.id
    # Off needs no confirmation.
    assert client.post("/program/daily-batch", headers=h, json={"count": 0}).json()["on"] is False


def test_runs_once_a_day_after_start_on_weekdays(db_session, sample_org, monkeypatch):
    admin = _program(db_session, sample_org)
    p = _prog(db_session, sample_org)
    p.daily_batch_count, p.daily_batch_by, p.daily_batch_spacing = 3, admin.id, 8
    db_session.commit()
    calls = []
    monkeypatch.setattr(daily_batch, "pick", lambda db, prog, n: ["a", "b", "c"][:n])
    monkeypatch.setattr(send_approval, "approve",
                        lambda db, prog, user, ids, **kw: calls.append((user.id, ids, kw)) or {"approved": len(ids)})
    assert daily_batch.run_for(db_session, p, WED_8AM) is None          # before 9:00 Central
    assert daily_batch.run_for(db_session, p, SAT_10AM) is None         # not a sending day
    res = daily_batch.run_for(db_session, p, WED_10AM)
    assert res == {"approved": 3, "picked": 3, "date": "2026-10-14"}
    assert calls[0][0] == admin.id and calls[0][1] == ["a", "b", "c"]
    assert calls[0][2]["spacing_minutes"] == 8 and calls[0][2]["start_at"] > WED_10AM
    assert daily_batch.run_for(db_session, p, WED_10AM) is None         # once per day
    assert len(calls) == 1


def test_off_or_no_owner_does_nothing(db_session, sample_org, monkeypatch):
    admin = _program(db_session, sample_org)
    p = _prog(db_session, sample_org)
    monkeypatch.setattr(send_approval, "approve", lambda *a, **k: (_ for _ in ()).throw(AssertionError("sent")))
    p.daily_batch_count = 0
    assert daily_batch.run_for(db_session, p, WED_10AM) is None
    p.daily_batch_count, p.daily_batch_by = 5, None
    assert daily_batch.run_for(db_session, p, WED_10AM) is None


THU_957PM = datetime(2026, 10, 16, 2, 57)        # Thu Oct 15, 9:57 PM Central


def test_no_batch_is_approved_after_hours(db_session, sample_org, monkeypatch):
    admin = _program(db_session, sample_org)
    p = _prog(db_session, sample_org)
    p.daily_batch_count, p.daily_batch_by = 40, admin.id
    db_session.commit()
    monkeypatch.setattr(send_approval, "approve", lambda *a, **k: (_ for _ in ()).throw(AssertionError("approved")))
    assert daily_batch.run_for(db_session, p, THU_957PM) is None
    assert p.daily_batch_last_date is None                 # Friday's batch still runs at 9:00


def test_repace_spreads_an_after_hours_batch_over_the_next_morning(program, sample_advisor, client):
    from app.models.models import CadenceState
    from app.models.program_models import ProgramSourceRecord
    from tests.test_outreach_program import _h as h2, _lead_for
    db, org, prog = program["db"], program["org"], program["prog"]
    prog.daily_batch_count, prog.daily_batch_by, prog.daily_batch_spacing = 40, program["admin"].id, 10
    leads = [_lead_for(db, org, sample_advisor, "L00%d" % i, phone="21455501%02d" % i, first="F%d" % i,
                       email="f%d@example.com" % i) for i in (1, 2, 3)]
    when = datetime.utcnow()
    for l in leads:
        r = db.query(ProgramSourceRecord).filter_by(lead_id=l.id).one()
        r.approved_to_send_at, r.scheduled_start_at = when, when
    db.commit()
    hh = h2(db, program["admin"])
    assert client.post("/program/daily-batch/repace", headers=hh,
                       json={"approved_since": when.isoformat()}).status_code == 422
    res = client.post("/program/daily-batch/repace", headers=hh,
                      json={"approved_since": (when.replace(microsecond=0)).isoformat(), "confirm": "REPACE"}).json()
    assert res["moved"] == 3
    starts = sorted(datetime.fromisoformat(i["starts_at"][:-1]) for i in res["items"])
    assert (starts[1] - starts[0]).total_seconds() == 600 and (starts[2] - starts[1]).total_seconds() == 600
    st = db.query(CadenceState).filter_by(lead_id=leads[0].id).one()
    db.refresh(st)
    assert st.next_touch_due_at in starts
    db.refresh(prog)
    assert prog.daily_batch_last_date == res["counts_as_batch_for"]


def test_the_batch_only_picks_families_with_sms_consent(db_session, sample_org, monkeypatch):
    """Oct 16: the quota is never filled with families the SCI gate would refuse."""
    admin = _program(db_session, sample_org)
    p = _prog(db_session, sample_org)
    rows = [{"lead_id": x, "approved": False, "blocked": None, "line_type": "mobile", "campaign_key": "general_survey",
             "last_text_at": None, "last_email_at": None, "status": "new", "location_id": "L1", "phone": ph}
            for x, ph in (("a", "12145550001"), ("b", "12145550002"), ("c", "12145550003"))]
    monkeypatch.setattr(send_approval, "list_contacts", lambda *a, **k: {"items": rows})
    from app.models.program_models import CampaignFamily
    fam = db_session.query(CampaignFamily).filter_by(organization_id=sample_org.id, key="general_survey").first()
    if fam is None:
        db_session.add(CampaignFamily(organization_id=sample_org.id, key="general_survey", name="General", is_active=True))
    else:
        fam.is_active = True
    db_session.commit()
    from app.services import sms_programs
    monkeypatch.setattr(sms_programs, "is_sci_org", lambda db, org: True)
    monkeypatch.setattr(sms_programs, "consented_numbers", lambda db, org: {"+12145550002"})
    assert daily_batch.pick(db_session, p, 40) == ["b"]
    monkeypatch.setattr(sms_programs, "consented_numbers", lambda db, org: set())
    assert daily_batch.pick(db_session, p, 40) == []
