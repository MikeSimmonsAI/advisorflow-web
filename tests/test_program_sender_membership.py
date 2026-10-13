"""The program sender is found for a workspace member whose login lives in
another organization (Kerry is SCI's by membership). Before this, every
approved slow-drip contact was skipped: no email sender, no text advisor."""
from app.models.models import Lead, Organization, User
from app.models.sales_models import Membership, SCOPE_CUSTOMER_ORG
from app.services.auth_service import hash_password
from app.services.programs import email_touches as et
from tests.test_outreach_program import program  # noqa: F401


def _seconded(db, home, org, name, role="advisor", member_role="org_admin"):
    u = User(organization_id=home.id, email="%s@example.com" % name.split()[0].lower(),
             password_hash=hash_password("x"), full_name=name, role=role, is_active=True,
             must_change_password=False)
    db.add(u)
    db.commit()
    db.add(Membership(user_id=u.id, scope_type=SCOPE_CUSTOMER_ORG, scope_id=org.id,
                      role=member_role, is_active=True))
    db.commit()
    return u


def test_member_primary_contact_is_the_sender(program):
    db, org, prog = program["db"], program["org"], program["prog"]
    home = Organization(name="Home", slug="home-org-x", plan="standard")
    db.add(home)
    db.commit()
    mike = _seconded(db, home, org, "Aaron Wholesale")
    kerry = _seconded(db, home, org, "Kerry Allan")
    lead = Lead(organization_id=org.id, first_name="Pat", status="new")
    db.add(lead)
    db.commit()
    # No primary contact user set: the admin whose name is the primary contact.
    prog.primary_contact_user_id = None
    db.commit()
    assert et._sender(db, prog, lead).id == kerry.id
    # Set explicitly: that member, even though their login lives elsewhere.
    prog.primary_contact_user_id = mike.id
    db.commit()
    assert et._sender(db, prog, lead).id == mike.id


def test_a_user_with_no_tie_to_the_workspace_is_never_the_sender(program):
    db, org, prog = program["db"], program["org"], program["prog"]
    stranger_org = Organization(name="Elsewhere", slug="elsewhere-x", plan="standard")
    db.add(stranger_org)
    db.commit()
    stranger = User(organization_id=stranger_org.id, email="s@example.com", password_hash=hash_password("x"),
                    full_name="Kerry Allan", role="org_admin", is_active=True, must_change_password=False)
    db.add(stranger)
    db.commit()
    prog.primary_contact_user_id = stranger.id
    db.commit()
    lead = Lead(organization_id=org.id, first_name="Pat", status="new")
    db.add(lead)
    db.commit()
    s = et._sender(db, prog, lead)
    assert s is None or s.id != stranger.id
