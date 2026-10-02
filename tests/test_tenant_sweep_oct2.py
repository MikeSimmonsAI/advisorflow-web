"""SECURITY SWEEP (Oct 2) - the cross-tenant gaps a route-by-route audit found.

Every other id-taking route traced clean; these four did not:
  1. a brand's sales manager could read ANY user's name and email by id;
  2. a new deal could be owned by someone who does not sell the brand;
  3. a support ticket could carry another tenant's diagnostic run as evidence;
  4. (provisioning) an implementation owner could be any customer's user.
"""
from app.models.models import Organization, User
from app.services import support_diagnostics
from app.services.auth_service import hash_password
from tests.test_sales_workspace import (  # noqa: F401  (fixtures)
    ROLE_SALES_REP, _add_membership, _make_sales_user, brand, brand_b,
    manager, manager_headers, platform, rep,
)
from tests.test_support_security import (  # noqa: F401  (fixtures)
    brand_a, headers_a, org_a, org_b, user_a, user_b,
)
from tests.test_support_security import brand_b as support_brand_b  # noqa: F401


def _outsider(db):
    org = Organization(name="Some Customer", slug="some-customer-x", plan="standard")
    db.add(org)
    db.commit()
    u = User(organization_id=org.id, email="private.person@customer.test",
             password_hash=hash_password("x"), full_name="Private Person",
             role="advisor", must_change_password=False)
    db.add(u)
    db.commit()
    return u


def test_a_manager_cannot_read_a_non_member_by_id(client, db_session, brand, manager_headers):
    outsider = _outsider(db_session)
    r = client.get("/sales/manager/reps/%s" % outsider.id,
                   params={"brand_sales_org_id": brand.id}, headers=manager_headers)
    assert r.status_code == 404
    assert "private.person" not in r.text and "Private Person" not in r.text
    r = client.get("/sales/manager/reps/not-a-user", params={"brand_sales_org_id": brand.id},
                   headers=manager_headers)
    assert r.status_code == 404


def test_a_manager_still_reads_their_own_rep(client, db_session, brand, manager_headers, rep):
    _add_membership(db_session, rep, brand, ROLE_SALES_REP)
    r = client.get("/sales/manager/reps/%s" % rep.id,
                   params={"brand_sales_org_id": brand.id}, headers=manager_headers)
    assert r.status_code == 200, r.text


def test_a_new_deal_cannot_be_owned_by_someone_outside_the_brand(client, db_session, brand, brand_b,
                                                                 manager_headers):
    other_brand_rep = _make_sales_user(db_session)
    _add_membership(db_session, other_brand_rep, brand_b, ROLE_SALES_REP)
    for owner in (other_brand_rep, _outsider(db_session)):
        r = client.post("/sales/opportunities", headers=manager_headers,
                        json={"company_name": "X Funeral", "brand_sales_org_id": brand.id,
                              "owner_user_id": owner.id})
        assert r.status_code == 400, r.text
    mine = _make_sales_user(db_session)
    _add_membership(db_session, mine, brand, ROLE_SALES_REP)
    r = client.post("/sales/opportunities", headers=manager_headers,
                    json={"company_name": "Y Funeral", "brand_sales_org_id": brand.id,
                          "owner_user_id": mine.id})
    assert r.status_code == 201, r.text


def test_a_ticket_cannot_borrow_another_tenants_diagnostic_run(client, db_session, org_a, user_a,
                                                               org_b, user_b, headers_a):
    from app.models.support_models import SupportTicket
    summary = support_diagnostics.run_checks(db_session, org=org_b, user=user_b, keys=None, is_god=False)
    theirs = support_diagnostics.persist_run(db_session, summary, org=org_b, requested_by=user_b.id,
                                             requested_by_kind="customer")
    db_session.commit()
    r = client.post("/support/tickets", headers=headers_a,
                    json={"subject": "Help", "body": "Something broke",
                          "diagnostic_run_id": theirs.id})
    assert r.status_code == 201, r.text
    t = db_session.query(SupportTicket).filter(SupportTicket.organization_id == org_a.id).one()
    assert t.diagnostic_run_id != theirs.id
