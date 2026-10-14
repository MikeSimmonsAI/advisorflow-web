"""Daily batch: a manager switches it on once (typed DAILY <n>), then every
sending day the program approves the next n people in that manager's name,
once per day, not before the start time, only on the listed weekdays."""
from datetime import datetime

from app.models.program_models import OutreachProgram
from app.services.programs import daily_batch, send_approval
from tests.test_program_load_source import _program
from tests.test_switch_import_booking import _h

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
