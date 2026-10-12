"""The owner's production load (POST /god/programs/{org}/load-source): the
server-side version of scripts/program_setup.py --stage --hold-reviews
--promote. Dry run by default; apply stages, holds, promotes - and never
records consent, enrols or sends."""
from app.models.models import Lead, User
from app.models.program_models import ProgramSourceRecord
from app.services.auth_service import create_access_token, hash_password
from app.services.programs import setup

HEADER = ("Lead ID,Lead Owner: Manager,Lead Owner,First Name,Last Name,Email,Lead Status,"
          "Last Activity,Last Activity Date,Phone,Primary Campaign,Campaign Channel,Create Date,"
          "Location Friendly Name\n")


def _row(lid, first, last, phone, loc, status="Qualified"):
    return "%s,Mgr,Owner,%s,%s,%s@example.com,%s,,1/5/2026,%s,Direct Mail> Survey> General,Direct Mail,12/27/2025,%s\n" % (
        lid, first, last, lid.lower(), status, phone, loc)


CSV = (HEADER
       + _row("P001", "Ann", "Clean", "2145550201", "Eastern Gate Memorial Gardens")
       + _row("P002", "Bob", "Clean", "2145550202", "Striffler-Hamby Mortuary")
       + _row("P003", "Kay", "Declined", "2145550203", "Eastern Gate Memorial Gardens", status="Not Interested")
       + _row("P004", "Cat", "Dq", "2145550204", "Striffler-Hamby Mortuary", status="Disqualified")
       + _row("P005", "Dan", "Elsewhere", "2145550205", "National Cremation and Burial Society"))


def _god(db):
    u = User(email="owner-load@example.com", password_hash=hash_password("Pass12345!"), full_name="Owner",
             role="god_admin", is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return {"Authorization": "Bearer %s" % create_access_token(u, db)}


def _program(db, org):
    admin = User(organization_id=org.id, email="load-admin@example.com", password_hash=hash_password("x"),
                 full_name="Admin", role="org_admin", is_active=True, must_change_password=False)
    db.add(admin)
    db.commit()
    setup.ensure_program(db, org, name="Service Corporation International", primary_contact_name="Kerry Allan")
    setup.ensure_locations(db, org, admin, ["Eastern Gate Memorial Gardens", "Striffler-Hamby Mortuary"])
    setup.ensure_campaign_families(db, org)
    db.commit()
    return admin


def _post(client, h, org_id, apply):
    return client.post("/god/programs/%s/load-source" % org_id, headers=h,
                       files={"file": ("sci.csv", CSV.encode(), "text/csv")},
                       data={"apply": "true" if apply else "false"})


def test_dry_run_writes_nothing(client, db_session, sample_org):
    _program(db_session, sample_org)
    r = _post(client, _god(db_session), sample_org.id, apply=False)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["apply"] is False and body["stage"]["staged"] == 0
    assert db_session.query(ProgramSourceRecord).filter_by(organization_id=sample_org.id).count() == 0
    assert db_session.query(Lead).filter_by(organization_id=sample_org.id).count() == 0


def test_apply_stages_holds_and_promotes_only_the_clean(client, db_session, sample_org):
    _program(db_session, sample_org)
    before = {lead.id for lead in db_session.query(Lead).filter_by(organization_id=sample_org.id)}
    h = _god(db_session)
    body = _post(client, h, sample_org.id, apply=True).json()
    recs = {r.source_lead_id: r for r in db_session.query(ProgramSourceRecord).filter_by(organization_id=sample_org.id)}
    assert set(recs) == {"P001", "P002", "P003", "P004", "P005"}
    assert recs["P003"].on_hold and "SOURCE STATUS: Not Interested" in recs["P003"].hold_reason
    assert recs["P004"].on_hold and "Disqualified" in recs["P004"].hold_reason
    assert recs["P005"].on_hold and "LOCATION REVIEW" in recs["P005"].hold_reason
    assert not recs["P001"].on_hold and not recs["P002"].on_hold
    assert body["promotion"]["created"] == 2
    new = [lead for lead in db_session.query(Lead).filter_by(organization_id=sample_org.id) if lead.id not in before]
    assert sorted(lead.first_name for lead in new) == ["Ann", "Bob"]
    assert all(lead.sms_consent is False for lead in new)          # never consents
    # A second load is idempotent: nothing new.
    again = _post(client, h, sample_org.id, apply=True).json()
    assert again["promotion"]["created"] == 0


def test_owner_only(client, db_session, sample_org, auth_headers):
    _program(db_session, sample_org)
    assert _post(client, auth_headers, sample_org.id, apply=False).status_code in (401, 403)
