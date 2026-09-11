"""ADD A PERSON. One human, one identity, both halves of the estate.

THE DEFECT THIS DEFENDS AGAINST, IN ORDER OF HOW BADLY

  1. A SECOND DLO. Provisioning somebody who already exists must reuse their
     identity. A duplicate is not a worse version of the right answer; it is a
     different human as far as every foreign key is concerned, and there is no
     way back from it.

  2. NOT BOTH. A person may legitimately sell for a brand AND administer a
     customer. One deliberate act must be able to give them both, without one
     grant quietly implying the other.

  3. A SECOND PROVISIONING ENGINE. Brand-sales seats go through `sales_staff`,
     which already owns them — including refusing a reporting manager who does
     not manage that brand. If this surface wrote the Membership row itself,
     that rule would exist in one place and be missing from the other.
"""

import itertools

import pytest

from app.models.models import Organization, Platform, User
from app.models.sales_models import (ROLE_SALES_MANAGER, ROLE_SALES_REP,
                                     SCOPE_BRAND_SALES_ORG, SCOPE_CUSTOMER_ORG,
                                     BrandSalesOrg, Membership)
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


def _h(db, u):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


def _user(db, role="advisor", org_id=None, name="Person", email=None):
    u = User(organization_id=org_id,
             email=email or ("p%d@example.com" % next(_SEQ)),
             password_hash=hash_password("x"), full_name=name, role=role,
             must_change_password=False, is_active=True)
    db.add(u)
    db.commit()
    return u


@pytest.fixture()
def world(db_session):
    plat = Platform(name="EvoSys Pro", slug="evo-ap-%d" % next(_SEQ))
    db_session.add(plat)
    db_session.commit()
    bso = BrandSalesOrg(platform_id=plat.id, name="EvoSys Pro Sales",
                        slug="evo-aps-%d" % next(_SEQ))
    other = BrandSalesOrg(platform_id=plat.id, name="Other Brand Sales",
                          slug="oth-aps-%d" % next(_SEQ))
    db_session.add_all([bso, other])
    db_session.commit()
    cust = Organization(name="Fiber Cartel", slug="fiber-%d" % next(_SEQ),
                        plan="standard", platform_id=plat.id)
    db_session.add(cust)
    db_session.commit()
    god = _user(db_session, role="god_admin", name="Owner")
    return dict(plat=plat, bso=bso, other=other, cust=cust, god=god)


def _dlo(db, world):
    """Somebody who already exists, exactly as the production report describes:
    a customer-org membership, org_admin, active, has signed in before."""
    from datetime import datetime
    u = _user(db, role="org_admin", org_id=world["cust"].id, name="Dlo",
              email="dlo%d@example.com" % next(_SEQ))
    u.last_login_at = datetime.utcnow()
    db.add(Membership(user_id=u.id, scope_type=SCOPE_CUSTOMER_ORG,
                      scope_id=world["cust"].id, role="org_admin",
                      is_active=True))
    db.commit()
    return u


# ═════════════════════════════════════════════════════════════════════════════
# 1. Lookup first
# ═════════════════════════════════════════════════════════════════════════════

def test_an_unknown_address_says_so(client, db_session, world):
    r = client.get("/god/access/identity-lookup?email=nobody@example.com",
                   headers=_h(db_session, world["god"]))
    assert r.status_code == 200
    assert r.json()["exists"] is False


def test_a_known_address_returns_everything_they_already_hold(client, db_session,
                                                              world):
    """Adding a seat to somebody who already sells elsewhere is a real
    decision. The operator sees it before they make it, not afterwards."""
    dlo = _dlo(db_session, world)
    r = client.get("/god/access/identity-lookup?email=" + dlo.email,
                   headers=_h(db_session, world["god"]))
    body = r.json()
    assert body["exists"] is True
    assert body["user_id"] == dlo.id
    assert [w["name"] for w in body["workspaces"]] == ["Fiber Cartel"]
    assert "reused" in body["note"]


def test_the_lookup_writes_nothing(client, db_session, world):
    before = db_session.query(User).count()
    client.get("/god/access/identity-lookup?email=brand.new@example.com",
               headers=_h(db_session, world["god"]))
    assert db_session.query(User).count() == before


@pytest.mark.parametrize("role", ["advisor", "org_admin", "super_admin"])
def test_provisioning_is_god_only(client, db_session, world, role):
    actor = _user(db_session, role=role, org_id=world["cust"].id)
    for method, path, body in (
            ("get", "/god/access/identity-lookup?email=a@b.com", None),
            ("post", "/god/access/provision", {"email": "a@b.com"}),
    ):
        fn = getattr(client, method)
        r = fn(path, headers=_h(db_session, actor)) if body is None else \
            fn(path, json=body, headers=_h(db_session, actor))
        assert r.status_code == 403


# ═════════════════════════════════════════════════════════════════════════════
# 2. Creating somebody new
# ═════════════════════════════════════════════════════════════════════════════

def test_a_new_salesperson_is_created_seated_and_invited(client, db_session,
                                                         world):
    r = client.post("/god/access/provision", json={
        "email": "New.Rep@Example.com",
        "full_name": "New Rep",
        "operations": [{"op": "add_membership",
                        "scope_type": SCOPE_BRAND_SALES_ORG,
                        "scope_id": world["bso"].id, "role": ROLE_SALES_REP}],
    }, headers=_h(db_session, world["god"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created_identity"] is True
    # NORMALISED. "New.Rep@Example.com" and "new.rep@example.com" are one human.
    assert body["email"] == "new.rep@example.com"

    u = db_session.query(User).filter(User.id == body["user_id"]).first()
    # A brand-sales identity belongs to no customer tenant, by positive
    # assertion — not by accident.
    assert u.organization_id is None
    assert u.role == "advisor"          # users.role stays the baseline
    assert u.must_change_password is True

    m = (db_session.query(Membership)
         .filter(Membership.user_id == u.id,
                 Membership.scope_type == SCOPE_BRAND_SALES_ORG).first())
    assert m.role == ROLE_SALES_REP and m.is_active

    # A one-time link, and never a password.
    assert body["activation"]["setup_url"]
    blob = r.text.lower()
    assert "password" not in blob or "no password" in blob

    # And the screen can say what they can now reach.
    assert body["contexts"]["has_back_office"] is True


def test_creating_without_a_name_is_refused_rather_than_guessed(client,
                                                               db_session,
                                                               world):
    r = client.post("/god/access/provision",
                    json={"email": "no.name@example.com"},
                    headers=_h(db_session, world["god"]))
    assert r.status_code == 400
    assert "full name" in r.json()["detail"].lower()


def test_the_setup_link_can_be_declined(client, db_session, world):
    r = client.post("/god/access/provision", json={
        "email": "quiet@example.com", "full_name": "Quiet Start",
        "send_setup_link": False,
    }, headers=_h(db_session, world["god"]))
    assert r.status_code == 200
    assert r.json()["activation"] is None


# ═════════════════════════════════════════════════════════════════════════════
# 3. THE DLO CASE — reuse, never recreate
# ═════════════════════════════════════════════════════════════════════════════

def test_an_existing_person_gains_a_seat_without_being_recreated(
        client, db_session, world):
    dlo = _dlo(db_session, world)
    before_users = db_session.query(User).count()

    r = client.post("/god/access/provision", json={
        "email": dlo.email.upper(),          # and the case must not matter
        "operations": [{"op": "add_membership",
                        "scope_type": SCOPE_BRAND_SALES_ORG,
                        "scope_id": world["bso"].id, "role": ROLE_SALES_REP}],
    }, headers=_h(db_session, world["god"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created_identity"] is False
    assert body["user_id"] == dlo.id
    assert db_session.query(User).count() == before_users

    db_session.expire_all()
    # BOTH, side by side. The customer membership he already had is untouched.
    scopes = {(m.scope_type, m.role) for m in
              db_session.query(Membership).filter(
                  Membership.user_id == dlo.id,
                  Membership.is_active.is_(True)).all()}
    assert (SCOPE_CUSTOMER_ORG, "org_admin") in scopes
    assert (SCOPE_BRAND_SALES_ORG, ROLE_SALES_REP) in scopes

    fp = body["footprint"]
    assert [w["name"] for w in fp["workspaces"] if w["is_active"]] == ["Fiber Cartel"]
    assert [b["name"] for b in fp["back_office"] if b["is_active"]] == ["EvoSys Pro Sales"]


def test_a_returning_person_gets_a_reset_link_not_a_setup_link(client,
                                                               db_session,
                                                               world):
    """Somebody who has signed in before is being reset, not set up. The two
    produce different wording for the recipient."""
    dlo = _dlo(db_session, world)
    r = client.post("/god/access/provision",
                    json={"email": dlo.email},
                    headers=_h(db_session, world["god"]))
    assert r.status_code == 200
    assert r.json()["activation"]["purpose"] == "reset"


def test_a_deactivated_person_is_refused_rather_than_silently_reactivated(
        client, db_session, world):
    dlo = _dlo(db_session, world)
    dlo.is_active = False
    db_session.commit()
    r = client.post("/god/access/provision", json={
        "email": dlo.email,
        "operations": [{"op": "add_membership",
                        "scope_type": SCOPE_BRAND_SALES_ORG,
                        "scope_id": world["bso"].id, "role": ROLE_SALES_REP}],
    }, headers=_h(db_session, world["god"]))
    assert r.status_code == 409
    assert "deactivated" in r.json()["detail"].lower()


def test_provisioning_the_same_person_twice_does_not_stack_memberships(
        client, db_session, world):
    plan = {"email": "twice@example.com", "full_name": "Twice Over",
            "operations": [{"op": "add_membership",
                            "scope_type": SCOPE_BRAND_SALES_ORG,
                            "scope_id": world["bso"].id,
                            "role": ROLE_SALES_REP}]}
    h = _h(db_session, world["god"])
    a = client.post("/god/access/provision", json=plan, headers=h)
    b = client.post("/god/access/provision", json=plan, headers=h)
    assert a.status_code == 200 and b.status_code == 200
    assert a.json()["user_id"] == b.json()["user_id"]
    assert db_session.query(User).filter(
        User.email == "twice@example.com").count() == 1
    assert (db_session.query(Membership)
            .filter(Membership.user_id == a.json()["user_id"],
                    Membership.scope_type == SCOPE_BRAND_SALES_ORG)
            .count()) == 1


# ═════════════════════════════════════════════════════════════════════════════
# 4. BOTH CONTEXTS AT ONCE
# ═════════════════════════════════════════════════════════════════════════════

def test_one_act_can_grant_a_sales_seat_and_a_workspace_membership(
        client, db_session, world):
    r = client.post("/god/access/provision", json={
        "email": "both@example.com", "full_name": "Both Contexts",
        "operations": [
            {"op": "add_membership", "scope_type": SCOPE_BRAND_SALES_ORG,
             "scope_id": world["bso"].id, "role": ROLE_SALES_MANAGER},
            {"op": "add_membership", "scope_type": SCOPE_CUSTOMER_ORG,
             "scope_id": world["cust"].id, "role": "org_admin"},
        ],
    }, headers=_h(db_session, world["god"]))
    assert r.status_code == 200, r.text
    fp = r.json()["footprint"]
    assert [b["role_label"] for b in fp["back_office"] if b["is_active"]] \
        == ["Sales Manager"]
    assert [w["role_label"] for w in fp["workspaces"] if w["is_active"]] \
        == ["Workspace Admin"]

    ctx = r.json()["contexts"]
    assert ctx["has_back_office"] is True
    assert ctx["workspace_count"] == 1
    # AND NEITHER IMPLIED THE OTHER: both were asked for explicitly.
    assert len(r.json()["applied"]) == 2


# ═════════════════════════════════════════════════════════════════════════════
# 5. It goes through sales_staff, so sales_staff's rules apply
# ═════════════════════════════════════════════════════════════════════════════

def test_a_reporting_manager_from_another_brand_is_refused(client, db_session,
                                                           world):
    """`sales_staff.assert_manager_ok` refuses a manager who does not manage
    this brand. If this surface wrote the row itself, that rule would be
    missing here — which is the whole reason it does not."""
    mgr = _user(db_session, name="Wrong Brand Manager")
    db_session.add(Membership(user_id=mgr.id, scope_type=SCOPE_BRAND_SALES_ORG,
                              scope_id=world["other"].id,
                              role=ROLE_SALES_MANAGER, is_active=True))
    db_session.commit()

    r = client.post("/god/access/provision", json={
        "email": "rep2@example.com", "full_name": "Rep Two",
        "operations": [{"op": "add_membership",
                        "scope_type": SCOPE_BRAND_SALES_ORG,
                        "scope_id": world["bso"].id, "role": ROLE_SALES_REP,
                        "reports_to_user_id": mgr.id}],
    }, headers=_h(db_session, world["god"]))
    assert r.status_code == 400
    assert "sales manager in this brand" in r.json()["detail"].lower()


def test_a_reporting_manager_from_this_brand_is_recorded(client, db_session,
                                                         world):
    mgr = _user(db_session, name="Right Manager")
    db_session.add(Membership(user_id=mgr.id, scope_type=SCOPE_BRAND_SALES_ORG,
                              scope_id=world["bso"].id,
                              role=ROLE_SALES_MANAGER, is_active=True))
    db_session.commit()

    r = client.post("/god/access/provision", json={
        "email": "rep3@example.com", "full_name": "Rep Three",
        "operations": [{"op": "add_membership",
                        "scope_type": SCOPE_BRAND_SALES_ORG,
                        "scope_id": world["bso"].id, "role": ROLE_SALES_REP,
                        "reports_to_user_id": mgr.id}],
    }, headers=_h(db_session, world["god"]))
    assert r.status_code == 200, r.text
    m = (db_session.query(Membership)
         .filter(Membership.user_id == r.json()["user_id"],
                 Membership.scope_type == SCOPE_BRAND_SALES_ORG).first())
    assert m.reports_to_user_id == mgr.id


def test_the_manager_picker_offers_only_managers_of_that_brand(client,
                                                               db_session,
                                                               world):
    right = _user(db_session, name="Right Manager")
    wrong = _user(db_session, name="Wrong Manager")
    rep = _user(db_session, name="A Rep")
    db_session.add_all([
        Membership(user_id=right.id, scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id=world["bso"].id, role=ROLE_SALES_MANAGER,
                   is_active=True),
        Membership(user_id=wrong.id, scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id=world["other"].id, role=ROLE_SALES_MANAGER,
                   is_active=True),
        Membership(user_id=rep.id, scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id=world["bso"].id, role=ROLE_SALES_REP,
                   is_active=True),
    ])
    db_session.commit()
    r = client.get("/god/access/sales-managers/" + world["bso"].id,
                   headers=_h(db_session, world["god"]))
    assert [m["name"] for m in r.json()["managers"]] == ["Right Manager"]


# ═════════════════════════════════════════════════════════════════════════════
# 6. Re-inviting
# ═════════════════════════════════════════════════════════════════════════════

def test_a_link_can_be_re_sent_to_somebody_who_never_used_theirs(client,
                                                                db_session,
                                                                world):
    created = client.post("/god/access/provision", json={
        "email": "never.used@example.com", "full_name": "Never Used"},
        headers=_h(db_session, world["god"])).json()
    r = client.post("/god/access/users/%s/invite" % created["user_id"],
                    json={}, headers=_h(db_session, world["god"]))
    assert r.status_code == 200
    assert r.json()["setup_url"]
    assert r.json()["purpose"] == "setup"     # they have never signed in
    assert "not recoverable" in r.json()["warning"]


def test_a_deactivated_account_is_not_sent_a_link(client, db_session, world):
    dlo = _dlo(db_session, world)
    dlo.is_active = False
    db_session.commit()
    r = client.post("/god/access/users/%s/invite" % dlo.id, json={},
                    headers=_h(db_session, world["god"]))
    assert r.status_code == 409


@pytest.mark.parametrize("role", ["advisor", "org_admin", "super_admin"])
def test_only_god_may_send_a_link(client, db_session, world, role):
    dlo = _dlo(db_session, world)
    actor = _user(db_session, role=role, org_id=world["cust"].id)
    r = client.post("/god/access/users/%s/invite" % dlo.id, json={},
                    headers=_h(db_session, actor))
    assert r.status_code == 403
