"""Universal Intake single-record capture (web forms, webhooks).

A form submission runs through the SAME engine as a file import - one
matcher, one contact record, one rollback trail - and a person who reached out
is never dropped at the plan limit (the lead is HELD).
"""
from app.models.import_models import ImportBatch
from app.models.intake_models import OrgContact
from app.models.models import Lead, Organization
from app.services import lead_capacity
from app.services.intake.capture import capture_one


def _org(db):
    org = Organization(name="Capture Co", slug="capture-co", plan="standard", industry="real_estate")
    db.add(org)
    db.commit()
    return org


P = {"first_name": "Ana", "last_name": "Reyes", "email": "ana@example.com", "phone": "(214) 555-0101",
     "street_address": "1 Elm St", "city": "Dallas", "state": "TX", "zip_code": "75201"}


def test_a_capture_creates_one_contact_and_one_lead_in_the_platform_phone_format(db_session):
    org = _org(db_session)
    r = capture_one(db_session, org, dict(P))
    assert r.match == "new" and r.lead_created and not r.held
    assert db_session.query(OrgContact).count() == 1
    lead = db_session.query(Lead).one()
    assert lead.phone == "12145550101"          # what the inbound webhook and suppression compare
    assert lead.relationship_type == "warm_lead" and lead.sms_consent is False
    assert lead.org_contact_id == r.contact_id and lead.import_batch_id == r.batch_id
    assert db_session.query(ImportBatch).filter_by(id=r.batch_id).one().source_system == "website"


def test_the_same_person_again_reuses_the_lead(db_session):
    org = _org(db_session)
    a = capture_one(db_session, org, dict(P))
    b = capture_one(db_session, org, dict(P, phone="+1 214 555 0101", street_address="9 Oak St"))
    assert b.match == "existing" and b.lead.id == a.lead.id and not b.lead_created
    assert db_session.query(Lead).count() == 1


def test_an_existing_platform_lead_is_matched_by_phone(db_session):
    org = _org(db_session)
    old = Lead(organization_id=org.id, first_name="Ana", last_name="Reyes", phone="12145550101")
    db_session.add(old)
    db_session.commit()
    r = capture_one(db_session, org, dict(P, email=None))
    assert r.lead.id == old.id and db_session.query(Lead).count() == 1


def test_a_possible_match_is_kept_separate_but_the_person_still_gets_a_lead(db_session):
    org = _org(db_session)
    capture_one(db_session, org, dict(P))
    r = capture_one(db_session, org, dict(P, first_name="Bob", last_name="Other", email="bob@example.com"))
    assert db_session.query(OrgContact).count() == 2
    assert r.lead is not None and db_session.query(Lead).count() == 2


def test_at_the_plan_limit_the_person_is_held_not_dropped(db_session, monkeypatch):
    from app.services import plan_limits
    real = plan_limits.counter_for_org_id

    def full(db, org_id, key):
        c = real(db, org_id, key)
        c.has_room = lambda n=1: False
        return c
    monkeypatch.setattr(plan_limits, "counter_for_org_id", full)
    org = _org(db_session)
    r = capture_one(db_session, org, dict(P))
    assert r.lead is not None and r.held and lead_capacity.is_held(r.lead)


def test_the_inbound_sms_path_finds_a_captured_person(db_session):
    from app.routers.sms_router import process_inbound_sms
    from app.models.models import Reply
    org = _org(db_session)
    r = capture_one(db_session, org, dict(P))
    process_inbound_sms(db_session, org_id=org.id, advisor=None, From="+12145550101",
                        Body="Is this about my house?", MessageSid="SMcap0001")
    assert db_session.query(Reply).filter_by(lead_id=r.lead.id).count() == 1
