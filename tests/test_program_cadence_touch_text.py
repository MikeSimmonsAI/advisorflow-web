"""A program cadence (industry "program") gives each follow-up touch its own words."""
from app.models.models import CadenceTemplate, CadenceTemplateTouch
from app.services.programs import identity
from tests.test_outreach_program import program, _lead_for  # noqa: F401


def _cadence(db, org, industry="program", default=True, touches=None):
    tpl = CadenceTemplate(organization_id=org.id, name="SCI 10-Day Aggressive", industry=industry,
                          is_default=default, is_active=True)
    db.add(tpl)
    db.flush()
    for n, d, text in (touches or [
            (1, 0, "unused for touch 1"),
            (2, 1, "Hi {first_name}, {primary_contact_name} with {location_name} again. {reply_instructions}"),
            (3, 3, "{first_name}, a quick question from {location_name}. {reply_instructions}")]):
        db.add(CadenceTemplateTouch(template_id=tpl.id, touch_number=n, day_offset=d, send_hour=10,
                                    channel="sms", message_template=text, is_active=True))
    db.commit()
    return tpl


def _no_default(db, org):
    for t in db.query(CadenceTemplate).filter_by(organization_id=org.id).all():
        t.is_default = False
    db.commit()


def test_follow_ups_use_their_own_words(program, sample_advisor):
    from app.services.cadence_service import render_cadence_message
    db, org = program["db"], program["org"]
    _no_default(db, org)
    _cadence(db, org)
    lead = _lead_for(db, org, sample_advisor, "L001")
    t1 = render_cadence_message(db, lead, sample_advisor, 1, "")
    t2 = render_cadence_message(db, lead, sample_advisor, 2, "")
    t3 = render_cadence_message(db, lead, sample_advisor, 3, "")
    assert "Veteran Benefits Guide" in t1                       # touch 1 = the contact's own family
    assert "again" in t2 and "Eastern Gate Memorial Gardens" in t2 and "{" not in t2
    assert "quick question" in t3 and "Eastern Gate Memorial Gardens" in t3
    # a touch the cadence has no text for falls back to the re-engagement family
    t4 = render_cadence_message(db, lead, sample_advisor, 4, "")
    assert "checking in" in t4


def test_platform_cadence_never_supplies_program_text(program, sample_advisor):
    db, org = program["db"], program["org"]
    _no_default(db, org)
    _cadence(db, org, industry="funeral")
    assert identity.program_touch_text(db, org.id, 2) is None


def test_draft_cadence_is_not_used(program, sample_advisor):
    db, org = program["db"], program["org"]
    _no_default(db, org)
    _cadence(db, org, default=False)
    assert identity.program_touch_text(db, org.id, 2) is None
