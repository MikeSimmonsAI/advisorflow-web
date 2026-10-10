"""EvoSys Wholesale for named logins only, and the god-only SCI workspace setup."""
import pytest

from app.models.models import Organization, Platform, User
from app.models.sales_models import Membership, SCOPE_CUSTOMER_ORG
from app.services import product_access, sci_provision, workspace_access
from app.services.auth_service import create_access_token, hash_password


def _user(db, org, email, role="org_admin"):
    u = User(organization_id=getattr(org, "id", None), email=email, password_hash=hash_password("TestPass123!"),
             full_name=email.split("@")[0], role=role, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _h(db, u):
    return {"Authorization": "Bearer %s" % create_access_token(u, db)}


@pytest.fixture()
def god(db_session):
    return _user(db_session, None, "owner@evosys.test", role="god_admin")


@pytest.fixture()
def two_wholesalers(db_session):
    a = Organization(name="EvoSys Client", slug="evosys-client", plan="standard", industry="real_estate")
    b = Organization(name="Someone Else", slug="someone-else", plan="standard", industry="real_estate")
    db_session.add_all([a, b])
    db_session.commit()
    return _user(db_session, a, "mike@evosys.test"), _user(db_session, b, "else@other.test")


# ── EvoSys Wholesale: named logins ─────────────────────────────────────────

def test_lock_off_until_a_login_is_named(client, db_session, two_wholesalers):
    mike, other = two_wholesalers
    assert product_access.lock_on(db_session) is False
    for u in (mike, other):     # unchanged behaviour before any grant
        assert client.get("/wholesale/dashboard", headers=_h(db_session, u)).status_code == 200


def test_only_named_logins_and_god_after_the_first_grant(client, db_session, two_wholesalers, god):
    mike, other = two_wholesalers
    r = client.post("/god/access-setup/wholesale/grant", headers=_h(db_session, god), json={"email": "Mike@EvoSys.test"})
    assert r.status_code == 200 and r.json()["action"] == "granted" and r.json()["lock_on"] is True
    assert client.get("/wholesale/dashboard", headers=_h(db_session, mike)).status_code == 200
    refused = client.get("/wholesale/dashboard", headers=_h(db_session, other))
    assert refused.status_code == 403 and "named logins" in refused.json()["detail"]
    assert product_access.may_use(db_session, god) is True


def test_navigation_hides_wholesale_from_everyone_else(db_session, two_wholesalers):
    from app.routers.branding_router import _with_product_lock
    mike, other = two_wholesalers
    product_access.grant(db_session, mike.email)
    assert _with_product_lock(db_session, other, {"offered": {"wholesale": True}})["offered"]["wholesale"] is False
    assert _with_product_lock(db_session, mike, {"offered": {"wholesale": True}})["offered"]["wholesale"] is True


def test_revoking_everyone_never_reopens_it(client, db_session, two_wholesalers, god):
    mike, other = two_wholesalers
    product_access.grant(db_session, mike.email)
    r = client.post("/god/access-setup/wholesale/revoke", headers=_h(db_session, god), json={"email": mike.email})
    assert r.json()["action"] == "revoked" and r.json()["lock_on"] is True
    for u in (mike, other):
        assert client.get("/wholesale/dashboard", headers=_h(db_session, u)).status_code == 403
    assert product_access.may_use(db_session, god) is True


def test_wholesale_routes_are_god_only(client, db_session, two_wholesalers):
    mike, _ = two_wholesalers
    assert client.get("/god/access-setup/wholesale", headers=_h(db_session, mike)).status_code == 403
    assert client.post("/god/access-setup/wholesale/grant", headers=_h(db_session, mike),
                       json={"email": mike.email}).status_code == 403


# ── SCI workspace setup ────────────────────────────────────────────────────

@pytest.fixture()
def evosys_platform(db_session):
    p = db_session.query(Platform).filter(Platform.slug == "evosyspro").first()
    if p is None:
        p = Platform(name="EvoSys Pro", slug="evosyspro")
        db_session.add(p)
        db_session.commit()
    return p


def test_sci_setup_dry_run_then_apply_then_idempotent(client, db_session, god, evosys_platform, two_wholesalers):
    mike, _ = two_wholesalers
    workspace_access.grant_workspace_membership(db_session, mike.id, mike.organization_id, role="org_admin")
    h = _h(db_session, god)
    plan = client.get("/god/access-setup/sci", headers=h, params={"email": mike.email}).json()
    assert plan["ready"] and plan["organization"]["action"] == "create" and plan["locations"] == 39
    assert plan["login"]["role_to_grant"] == "org_admin" and plan["login"]["already_in_sci"] is False
    assert db_session.query(Organization).filter(Organization.name == sci_provision.ORG_NAME).count() == 0

    assert client.post("/god/access-setup/sci", headers=h, json={"email": mike.email}).status_code == 422
    out = client.post("/god/access-setup/sci", headers=h, json={"email": mike.email, "confirm": "SET UP SCI"}).json()
    assert out["organization_created"] is True and out["locations"] == 39 and out["campaigns_on"] == 0
    assert out["toll_free"]["action"] == "create" and out["login"]["role"] == "org_admin"
    names = {w["name"] for w in out["workspaces_now"]}
    assert sci_provision.ORG_NAME in names and "EvoSys Client" in names

    again = client.post("/god/access-setup/sci", headers=h, json={"email": mike.email, "confirm": "SET UP SCI"}).json()
    assert again["organization_created"] is False and again["organization_id"] == out["organization_id"]
    assert db_session.query(Organization).filter(Organization.name == sci_provision.ORG_NAME).count() == 1
    sci = db_session.query(Organization).filter(Organization.name == sci_provision.ORG_NAME).one()
    assert "wholesale_real_estate" not in (sci.enabled_features or "")
    assert db_session.query(Membership).filter(Membership.user_id == mike.id, Membership.scope_type == SCOPE_CUSTOMER_ORG,
                                               Membership.scope_id == sci.id, Membership.is_active.is_(True)).count() == 1


def test_sci_setup_refuses_unknown_login_and_is_god_only(client, db_session, god, evosys_platform, two_wholesalers):
    mike, _ = two_wholesalers
    plan = client.get("/god/access-setup/sci", headers=_h(db_session, god), params={"email": "nobody@x.test"}).json()
    assert plan["ready"] is False and "No login" in plan["blockers"][0]
    r = client.post("/god/access-setup/sci", headers=_h(db_session, god), json={"email": "nobody@x.test", "confirm": "SET UP SCI"})
    assert r.status_code == 409
    assert client.get("/god/access-setup/sci", headers=_h(db_session, mike), params={"email": mike.email}).status_code == 403
