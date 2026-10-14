"""A record marked "don't mention the location" is still contacted, but no
text, email, sender name or link names the location it is routed to."""
from app.models.program_models import ProgramSourceRecord
from app.services.programs import identity
from tests.test_outreach_program import _fake_provider, _touch_ready, program  # noqa: F401 (fixture)


def _hide(db, lead):
    for r in db.query(ProgramSourceRecord).filter_by(lead_id=lead.id).all():
        r.hide_location = True
    db.commit()


def test_hidden_location_never_named(program, sample_advisor, monkeypatch):
    db, org, lead, et = _touch_ready(program, sample_advisor, monkeypatch)
    real = identity.location_profile_for_lead(db, lead)
    name = real.official_name
    _hide(db, lead)

    prof = identity.location_profile_for_lead(db, lead)
    assert identity.hides_location(prof)
    assert prof.location_id == real.location_id                 # routing unchanged
    assert prof.official_name == identity.NEUTRAL_LOCATION_NAME and prof.facility_phone is None

    ctx = identity.context_for(db, lead, "sms")
    assert ctx["ok"] and name not in ctx["display_name"] and name not in ctx["sms_signoff"]
    assert ctx["fields"]["location_name"] == identity.NEUTRAL_LOCATION_NAME
    signed = identity.apply_sms_signoff(db, lead, "Hi there.")
    assert name not in signed and identity.NEUTRAL_LOCATION_NAME in signed

    sent = []
    with _fake_provider(sent):
        r = et.run(db, org.id, force_hours=True)
    assert r["sent"] == 1 and len(sent) == 1
    assert name not in sent[0]["subject"] and name not in sent[0]["body_html"]


def test_unhidden_record_still_names_location(program, sample_advisor, monkeypatch):
    db, org, lead, et = _touch_ready(program, sample_advisor, monkeypatch)
    prof = identity.location_profile_for_lead(db, lead)
    assert not identity.hides_location(prof)
    assert prof.official_name in identity.context_for(db, lead, "sms")["display_name"]
