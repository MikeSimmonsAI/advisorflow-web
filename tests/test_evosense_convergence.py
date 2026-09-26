"""Convergence step 5: an EvoSense owner who is worked becomes a person in
Universal Intake (one matcher, one org contact), and EvoSense points at it.

EvoSense keeps the owner-of-record relationship. It never becomes a second
contact database, and sandbox owners (synthetic data) never enter the real one.
"""
from types import SimpleNamespace

from app.models.intake_models import OrgContact
from app.models.models import Lead
from app.services.evosense import outreach as OUT


def _prop(org, *, is_test=False, street="44 Converge Rd"):
    return SimpleNamespace(id="p-" + street, organization_id=org.id, street_address=street,
                           city="Dallas", state="TX", zip_code="75201", is_test=is_test)


def _person(name="Rita Rowe"):
    return SimpleNamespace(full_name=name, lead_id=None, converged_contact_ref=None)


def _cp(value="+12145550401", kind="phone"):
    return SimpleNamespace(kind=kind, value=value, converged_contact_ref=None)


def test_a_worked_owner_becomes_an_org_contact_and_evosense_points_at_it(
        db_session, sample_org, sample_advisor):
    db = db_session
    person, cp = _person(), _cp()
    lead = OUT.ensure_lead(db, _prop(sample_org), person, cp, user=sample_advisor)
    db.commit()
    contact = db.query(OrgContact).one()
    assert lead.org_contact_id == contact.id and contact.lead_id == lead.id
    assert person.lead_id == lead.id
    assert person.converged_contact_ref == contact.id and cp.converged_contact_ref == contact.id
    assert lead.phone == "12145550401" and lead.source_category == "evosense"
    assert lead.relationship_type == "cold_lead" and lead.assigned_to_id == sample_advisor.id


def test_an_owner_already_in_the_workspace_is_reused_not_duplicated(
        db_session, sample_org, sample_advisor):
    db = db_session
    first = OUT.ensure_lead(db, _prop(sample_org), _person(), _cp(), user=sample_advisor)
    db.commit()
    # The same owner, found again on another property by a different strategy.
    person2 = _person()
    again = OUT.ensure_lead(db, _prop(sample_org, street="46 Converge Rd"), person2, _cp(),
                            user=sample_advisor)
    db.commit()
    assert again.id == first.id and db.query(Lead).count() == 1
    assert db.query(OrgContact).count() == 1 and person2.converged_contact_ref


def test_a_sandbox_owner_never_enters_the_contact_database(db_session, sample_org, sample_advisor):
    db = db_session
    lead = OUT.ensure_lead(db, _prop(sample_org, is_test=True), _person(), _cp(),
                           user=sample_advisor)
    db.commit()
    assert lead.is_test is True and db.query(OrgContact).count() == 0


def test_an_intake_failure_still_gives_the_owner_a_lead(db_session, sample_org, sample_advisor,
                                                       monkeypatch):
    from app.services.intake import capture as CAP
    db = db_session

    def boom(*a, **k):
        raise RuntimeError("simulated failure")
    monkeypatch.setattr(CAP, "capture_one", boom)
    person = _person()
    lead = OUT.ensure_lead(db, _prop(sample_org), person, _cp(), user=sample_advisor)
    db.commit()
    assert lead.last_name == "Rowe" and person.lead_id == lead.id
    assert db.query(OrgContact).count() == 0
