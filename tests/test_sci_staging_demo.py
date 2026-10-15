"""SCI staging demo families: staging-only, fills SCI Command, rebuilt not
doubled, never texted, never built outside the SCI staging database."""
import pytest

from app.models.models import BookingLink, Lead
from app.models.program_models import ProgramResponse
from app.services import environment, sci_staging_bootstrap, sci_staging_demo as demo
from tests.test_outreach_program import _h, program  # noqa: F401 (fixture)


@pytest.fixture
def staging(monkeypatch, program):
    monkeypatch.setattr(environment, "current", lambda: environment.ENV_STAGING)
    monkeypatch.setattr(sci_staging_bootstrap, "database_is_sci_staging", lambda url=None: True)
    program["org"].name = demo.ORG_NAME
    program["db"].commit()
    return program


def test_refuses_outside_sci_staging(program, monkeypatch):
    monkeypatch.setattr(environment, "current", lambda: environment.ENV_PRODUCTION)
    assert demo.ensure(program["db"])["reason"] == "not_staging_environment"
    monkeypatch.setattr(environment, "current", lambda: environment.ENV_STAGING)
    monkeypatch.setattr(sci_staging_bootstrap, "database_is_sci_staging", lambda url=None: False)
    assert demo.ensure(program["db"])["reason"] == "database_not_sci_staging"
    assert program["db"].query(Lead).filter(Lead.source == demo.DEMO_SOURCE).count() == 0


def test_fills_command_and_rebuilds_without_doubling(staging, client):
    db, org = staging["db"], staging["org"]
    out = demo.ensure(db)
    assert out["ok"] and out["families"] == len(demo.FAMILIES) + len(demo.QUIET)
    assert demo.ensure(db)["skipped"] == "already_today"
    again = demo.ensure(db, force=True)
    assert again["replaced"] == out["families"]
    assert db.query(Lead).filter(Lead.source == demo.DEMO_SOURCE).count() == out["families"]
    # nothing can page anyone: no SLA clock, no reminders pending
    assert all(r.sla_due_at is None for r in db.query(ProgramResponse).all())
    assert all(b.reminder_24hr_sent and b.reminder_1hr_sent for b in db.query(BookingLink).all())
    r = client.get("/program/command", headers=_h(db, staging["admin"])).json()
    assert len(r["inbox"]) >= 15 and r["counts"]["needs_attention"] >= 5
    assert r["counts"]["replies_today"] >= 10 and r["counts"]["contacted_today"] >= 20
    fam = client.get("/program/command/family/%s" % r["inbox"][0]["lead_id"],
                     headers=_h(db, staging["admin"])).json()
    assert fam["thread"] and fam["location"]


def test_demo_family_is_never_texted(staging):
    from app.services import sms_service
    db = staging["db"]
    demo.ensure(db)
    lead = db.query(Lead).filter(Lead.source == demo.DEMO_SOURCE).first()
    with pytest.raises(sms_service.SciSmsBlocked) as e:
        sms_service.send_sms(db, staging["admin"], lead, "hello", include_booking_link=False)
    assert e.value.reasons == ["DEMO_NUMBER"]


def test_demo_family_is_never_emailed(staging):
    from app.services import email_service
    db = staging["db"]
    demo.ensure(db)
    lead = db.query(Lead).filter(Lead.source == demo.DEMO_SOURCE, Lead.email.isnot(None)).first()
    with pytest.raises(ValueError) as e:
        email_service.send_email_to_lead(db, staging["admin"], lead, "Hi", "Hello")
    assert "DEMO_FAMILY" in str(e.value)
