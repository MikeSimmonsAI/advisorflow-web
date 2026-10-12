"""The inbox placement test sends the same first email a family would get."""
from app.models.program_models import CampaignFamily, LocationProfile
from app.services.programs import placement
from tests.test_outreach_program import program  # noqa: F401


def _prof(db, org):
    return (db.query(LocationProfile).filter_by(organization_id=org.id, is_review_bucket=False)
            .order_by(LocationProfile.official_name).first())


def test_edited_family_copy_is_what_the_test_sends(program):
    db, org, prog = program["db"], program["org"], program["prog"]
    fam = db.query(CampaignFamily).filter_by(organization_id=org.id, key="veteran_planning_guide").one()
    fam.email_subject_template = "Following up, {first_name}"
    fam.email_body_template = "Hi {first_name},\n\nIt's {primary_contact_name} at {location_name}.\n\n{primary_contact_name}"
    db.commit()
    prof = _prof(db, org)
    s = placement.build_sample(db, prog, prof, "veteran_planning_guide")
    assert s["message"]["subject"] == "Following up, there"
    assert prof.official_name in s["message"]["body"] and "Kerry Allan" in s["message"]["body"]


def test_default_copy_still_uses_the_playbook(program):
    db, org, prog = program["db"], program["org"], program["prog"]
    from app.services.programs import message_brain as brain
    prof = _prof(db, org)
    s = placement.build_sample(db, prog, prof, "veteran_planning_guide")
    fam = db.query(CampaignFamily).filter_by(organization_id=org.id, key="veteran_planning_guide").one()
    from app.services.programs.email_touches import _family_copy_is_default
    if _family_copy_is_default(fam):
        assert s["message"].get("source") != "family copy"
    assert s["message"]["subject"]
    assert brain  # module importable
