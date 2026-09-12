"""ONE HUMAN IDENTITY, MANY AUTHORIZED CONTEXTS.

THE DEFECT THIS FILE DEFENDS AGAINST
====================================
`customer_provisioning.lookup_identity` refused two legitimate people:
brand-sales staff (`organization_id` IS NULL) and anybody already homed in
another customer. Its stated reason was that customer tenancy is one column
and so cannot hold two — which described the schema BEFORE
`workspace_access` moved tenancy onto `Membership(scope_type=customer_org)`.
`customer_activation.add_existing_user` was migrated then; this path was not.

Every caller of `add_customer_user` inherited the stale rule, including
`POST /god/launch/{id}/send-onboarding`. So the salesperson who sold a deal
could not be given onboarding access to the customer he had just sold, and
the screen told the operator to invent a second email address for one human.

WHAT MUST STAY TRUE, IN ORDER OF HOW BADLY
------------------------------------------
  1. NO SECOND HUMAN. One normalized email is one `users` row, forever.
  2. NOTHING IS MUTATED. A brand-sales identity keeps its platform role, its
     brand-sales membership and its home column. Access is ADDED.
  3. TENANT ISOLATION. Access to customer A grants nothing in customer B.
  4. ROOT IS NOT AN ORDINARY MULTI-CONTEXT CASE. god_admin and super_admin
     are still refused a customer tenancy through this door.
  5. IDEMPOTENT. Sending onboarding twice does not produce two memberships,
     two accounts, or a second live setup link.
  6. CREDENTIALS ARE NOT COLLATERAL. Somebody who already signs in is not
     handed a password-setup link merely because they were onboarded.
"""

import itertools

import pytest

from app.models.models import Organization, Platform, User
from app.models.sales_models import (ROLE_SALES_MANAGER, SCOPE_BRAND_SALES_ORG,
                                     SCOPE_CUSTOMER_ORG, BrandSalesOrg,
                                     Membership)
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


def _h(db, u):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


def _user(db, role="advisor", org_id=None, email=None, name="Person",
          must_change=False):
    u = User(organization_id=org_id,
             email=email or ("mc%d@example.com" % next(_SEQ)),
             password_hash=hash_password("x"), full_name=name, role=role,
             must_change_password=must_change, is_active=True)
    db.add(u)
    db.commit()
    return u


@pytest.fixture()
def world(db_session):
    plat = Platform(name="Brand %d" % next(_SEQ), slug="mc-brand-%d" % next(_SEQ))
    db_session.add(plat)
    db_session.commit()
    bso = BrandSalesOrg(platform_id=plat.id, name="Brand Sales %d" % next(_SEQ),
                        slug="mc-bso-%d" % next(_SEQ))
    db_session.add(bso)
    db_session.commit()
    cust = Organization(name="Customer A %d" % next(_SEQ),
                        slug="mc-cust-a-%d" % next(_SEQ),
                        plan="standard", platform_id=plat.id)
    other = Organization(name="Customer B %d" % next(_SEQ),
                         slug="mc-cust-b-%d" % next(_SEQ),
                         plan="standard", platform_id=plat.id)
    db_session.add_all([cust, other])
    db_session.commit()
    god = _user(db_session, role="god_admin", name="Owner")
    return dict(plat=plat, bso=bso, cust=cust, other=other, god=god)


def _seller(db, world, email=None):
    """Brand-sales staff exactly as production has them: NULL organization_id,
    a platform role of sales_manager, and an active brand-sales membership."""
    u = _user(db, role=ROLE_SALES_MANAGER, org_id=None, name="Joshua Seller",
              email=email or ("seller%d@example.com" % next(_SEQ)))
    db.add(Membership(user_id=u.id, scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id=world["bso"].id, role=ROLE_SALES_MANAGER,
                      is_active=True))
    db.commit()
    return u


def _memberships(db, user_id, scope_type=None, active_only=True):
    q = db.query(Membership).filter(Membership.user_id == user_id)
    if scope_type:
        q = q.filter(Membership.scope_type == scope_type)
    rows = q.all()
    return [m for m in rows if m.is_active] if active_only else rows


def _lookup(client, db, world, email, org=None):
    org = org or world["cust"]
    return client.get("/god/customers/%s/identity-lookup?email=%s"
                      % (org.id, email),
                      headers=_h(db, world["god"])).json()


def _add(client, db, world, email, role="org_admin", name="", org=None):
    org = org or world["cust"]
    return client.post("/god/customers/%s/users" % org.id,
                       json={"email": email, "full_name": name, "role": role},
                       headers=_h(db, world["god"]))


# ═════════════════════════════════════════════════════════════════════════════
# 1. The lookup no longer refuses a legitimate human
# ═════════════════════════════════════════════════════════════════════════════

def test_a_new_address_is_a_create(client, db_session, world):
    r = _lookup(client, db_session, world, "brand.new%d@example.com" % next(_SEQ))
    assert r["exists"] is False and r["can_add"] is True
    assert r["action"] == "create"


def test_brand_sales_staff_can_now_be_added(client, db_session, world):
    """The exact refusal from production: organization_id IS NULL."""
    seller = _seller(db_session, world)
    r = _lookup(client, db_session, world, seller.email)
    assert r["exists"] is True
    assert r["can_add"] is True, r["reason"]
    assert r["action"] == "add_context"
    # The old message told the operator to go and invent another address.
    assert "different address" not in (r["reason"] or "")
    assert r["user"]["brand_sales_memberships"] == 1


def test_a_member_of_another_customer_can_be_added(client, db_session, world):
    homed = _user(db_session, role="org_admin", org_id=world["other"].id,
                  name="Elsewhere")
    r = _lookup(client, db_session, world, homed.email)
    assert r["can_add"] is True
    assert r["action"] == "add_context"


def test_somebody_already_here_is_a_reuse(client, db_session, world):
    here = _user(db_session, role="advisor", org_id=world["cust"].id)
    r = _lookup(client, db_session, world, here.email)
    assert r["action"] == "reuse" and r["can_add"] is True


# ═════════════════════════════════════════════════════════════════════════════
# 2. Adding is ADDITIVE — nothing existing is mutated
# ═════════════════════════════════════════════════════════════════════════════

def test_adding_a_seller_preserves_every_existing_context(client, db_session, world):
    seller = _seller(db_session, world)
    before_role = seller.role
    before_org = seller.organization_id
    before_brand = len(_memberships(db_session, seller.id, SCOPE_BRAND_SALES_ORG))

    r = _add(client, db_session, world, seller.email, role="org_admin")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["created_identity"] is False, "a second human was created"

    db_session.expire_all()
    again = db_session.query(User).filter(User.id == seller.id).first()

    # PLATFORM ROLE UNCHANGED. He is not "less brand sales" for administering
    # a customer.
    assert again.role == before_role
    # BRAND-SALES MEMBERSHIP UNTOUCHED.
    assert len(_memberships(db_session, seller.id, SCOPE_BRAND_SALES_ORG)) == before_brand
    # THE NEW ACCESS IS A MEMBERSHIP, scoped to this customer, with the
    # workspace role that was asked for.
    ws = _memberships(db_session, seller.id, SCOPE_CUSTOMER_ORG)
    assert [(m.scope_id, m.role) for m in ws] == [(world["cust"].id, "org_admin")]
    # The legacy column was EMPTY, so it is seeded rather than left null —
    # hundreds of routes still read it. It is never repointed (below).
    assert again.organization_id in (before_org, world["cust"].id)


def test_a_home_column_pointing_elsewhere_is_never_repointed(client, db_session,
                                                             world):
    """Seeding an empty column is not the same as moving somebody."""
    homed = _user(db_session, role="advisor", org_id=world["other"].id)
    r = _add(client, db_session, world, homed.email, role="org_admin")
    assert r.status_code == 201, r.text

    db_session.expire_all()
    again = db_session.query(User).filter(User.id == homed.id).first()
    assert again.organization_id == world["other"].id, "the person was moved"
    # Their platform role describes the tenant they are homed in, so it is not
    # rewritten by a grant in a different customer.
    assert again.role == "advisor"
    ws = {m.scope_id: m.role for m in _memberships(db_session, homed.id,
                                                   SCOPE_CUSTOMER_ORG)}
    assert ws.get(world["cust"].id) == "org_admin"


def test_no_second_user_row_for_the_same_email(client, db_session, world):
    seller = _seller(db_session, world)
    before = db_session.query(User).filter(User.email == seller.email).count()
    _add(client, db_session, world, seller.email, role="org_admin")
    _add(client, db_session, world, seller.email.upper(), role="advisor")
    after = db_session.query(User).filter(User.email == seller.email).count()
    assert before == after == 1


def test_repeat_grants_do_not_duplicate_membership(client, db_session, world):
    seller = _seller(db_session, world)
    _add(client, db_session, world, seller.email, role="org_admin")
    _add(client, db_session, world, seller.email, role="advisor")
    rows = _memberships(db_session, seller.id, SCOPE_CUSTOMER_ORG,
                        active_only=False)
    here = [m for m in rows if m.scope_id == world["cust"].id]
    assert len(here) == 1, "a re-role produced a second membership"
    assert here[0].role == "advisor"


# ═════════════════════════════════════════════════════════════════════════════
# 3. Tenant isolation
# ═════════════════════════════════════════════════════════════════════════════

def test_access_to_one_customer_grants_nothing_in_another(client, db_session,
                                                          world):
    seller = _seller(db_session, world)
    _add(client, db_session, world, seller.email, role="org_admin")

    from app.services import workspace_access
    db_session.expire_all()
    again = db_session.query(User).filter(User.id == seller.id).first()
    ids = workspace_access.workspace_org_ids(again, db_session)
    assert ids == [world["cust"].id]
    assert workspace_access.has_workspace(again, db_session,
                                          world["other"].id) is False
    assert workspace_access.workspace_role(again, db_session,
                                           world["cust"].id) == "org_admin"
    assert workspace_access.workspace_role(again, db_session,
                                           world["other"].id) is None


def test_the_grant_carries_no_platform_or_god_authority(client, db_session, world):
    seller = _seller(db_session, world)
    _add(client, db_session, world, seller.email, role="org_admin")
    db_session.expire_all()
    again = db_session.query(User).filter(User.id == seller.id).first()
    assert again.role != "god_admin" and again.role != "super_admin"
    h = _h(db_session, again)
    # A customer workspace role is not a door into the control plane.
    assert client.get("/god/customers", headers=h).status_code in (401, 403, 404)


@pytest.mark.parametrize("role", ["advisor", "org_admin", "viewer"])
def test_the_requested_role_must_be_a_customer_role(client, db_session, world, role):
    seller = _seller(db_session, world)
    r = _add(client, db_session, world, seller.email, role=role)
    assert r.status_code == 201, r.text


@pytest.mark.parametrize("role", ["god_admin", "super_admin", "sales_manager"])
def test_a_control_plane_role_is_not_expressible(client, db_session, world, role):
    seller = _seller(db_session, world)
    r = _add(client, db_session, world, seller.email, role=role)
    assert r.status_code in (400, 422), r.text


# ═════════════════════════════════════════════════════════════════════════════
# 4. Root safety — unchanged
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("role", ["god_admin", "super_admin"])
def test_a_control_plane_account_is_still_refused_a_tenancy(client, db_session,
                                                            world, role):
    operator = _user(db_session, role=role, org_id=None, name="Operator")
    look = _lookup(client, db_session, world, operator.email)
    assert look["can_add"] is False and look["action"] == "refuse"

    r = _add(client, db_session, world, operator.email, role="org_admin")
    assert r.status_code == 409, r.text
    assert _memberships(db_session, operator.id, SCOPE_CUSTOMER_ORG) == []
    db_session.expire_all()
    again = db_session.query(User).filter(User.id == operator.id).first()
    assert again.role == role, "a root account was downgraded"
    assert again.organization_id is None


# ═════════════════════════════════════════════════════════════════════════════
# 5. Launch onboarding — the screen the defect was found on
# ═════════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def launched(db_session, world):
    """The target customer with an implementation, so /god/launch resolves."""
    from app.services import implementation_service as impl_svc
    started = impl_svc.start_for_organization(
        db_session, world["cust"], world["god"], reason="test", commit=True)
    return started["implementation"]


def _send(client, db, world, email, role="org_admin", name=""):
    return client.post("/god/launch/%s/send-onboarding" % world["cust"].id,
                       json={"email": email, "confirm_email": email,
                             "full_name": name, "role": role,
                             "base_url": "https://example.test"},
                       headers=_h(db, world["god"]))


def test_onboarding_a_new_person_issues_a_setup_link(client, db_session, world,
                                                     launched):
    r = _send(client, db_session, world, "fresh%d@example.com" % next(_SEQ),
              name="Fresh Person")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["identity_created"] is True
    assert body["access_path"] == "setup_link"
    assert body["onboarding_url_is_one_time"] is True
    assert "/activate?token=" in body["onboarding_url"]


def test_onboarding_an_existing_seller_is_the_live_failure(client, db_session,
                                                           world, launched):
    """Joshua's case, end to end, through the real endpoint."""
    seller = _seller(db_session, world)
    r = _send(client, db_session, world, seller.email, role="org_admin")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["identity_created"] is False
    # The role reported is the one IN THIS CUSTOMER.
    assert body["recipient"]["role"] == "org_admin"
    assert body["recipient"]["platform_role"] == ROLE_SALES_MANAGER
    # No password setup link for somebody who already signs in.
    assert body["access_path"] == "existing_login"
    assert body["onboarding_url_is_one_time"] is False
    assert body["onboarding_url"].endswith("/launch")
    assert "token=" not in body["onboarding_url"]


def test_onboarding_does_not_touch_an_existing_password_or_links(
        client, db_session, world, launched):
    from app.models.staff_models import StaffActivation

    seller = _seller(db_session, world)
    before_hash = seller.password_hash
    before_flag = seller.must_change_password

    _send(client, db_session, world, seller.email, role="org_admin")

    db_session.expire_all()
    again = db_session.query(User).filter(User.id == seller.id).first()
    assert again.password_hash == before_hash
    assert again.must_change_password == before_flag
    # Nothing was minted against their identity, so nothing of theirs was
    # superseded either.
    assert db_session.query(StaffActivation).filter(
        StaffActivation.user_id == seller.id).count() == 0


def test_the_recipient_appears_on_the_customer_after_onboarding(
        client, db_session, world, launched):
    """`status` and the People list both used to read the legacy column only,
    so the person onboarding had just been sent to was invisible."""
    seller = _seller(db_session, world)
    _send(client, db_session, world, seller.email, role="org_admin")

    detail = client.get("/god/customers/%s" % world["cust"].id,
                        headers=_h(db_session, world["god"])).json()
    emails = [u["email"] for u in detail["users"]]
    assert seller.email in emails
    row = next(u for u in detail["users"] if u["email"] == seller.email)
    assert row["workspace_role"] == "org_admin"
    assert row["is_seconded"] is True
    assert row["platform_role"] == ROLE_SALES_MANAGER

    # The activation blocker reads the same union, so a workspace with a
    # seconded administrator is not reported as having nobody who can log in.
    blockers = detail["readiness"]["blockers"]
    assert not any("nobody can log in" in b for b in blockers), blockers

    recipient = client.get("/god/launch/%s/onboarding-recipient" % world["cust"].id,
                           headers=_h(db_session, world["god"])).json()
    assert seller.email in [p["email"] for p in recipient["existing_people"]]


def test_repeat_onboarding_is_idempotent(client, db_session, world, launched):
    seller = _seller(db_session, world)
    first = _send(client, db_session, world, seller.email, role="org_admin")
    second = _send(client, db_session, world, seller.email, role="org_admin")
    assert first.status_code == 200 and second.status_code == 200, second.text

    assert db_session.query(User).filter(User.email == seller.email).count() == 1
    rows = _memberships(db_session, seller.id, SCOPE_CUSTOMER_ORG,
                        active_only=False)
    assert len([m for m in rows if m.scope_id == world["cust"].id]) == 1


def test_onboarding_is_still_god_only(client, db_session, world, launched):
    seller = _seller(db_session, world)
    h = _h(db_session, seller)
    r = client.post("/god/launch/%s/send-onboarding" % world["cust"].id,
                    json={"email": "x%d@example.com" % next(_SEQ),
                          "confirm_email": "x@example.com", "role": "org_admin"},
                    headers=h)
    assert r.status_code in (401, 403, 404)


def test_the_confirmation_field_still_guards_the_send(client, db_session, world,
                                                      launched):
    r = client.post("/god/launch/%s/send-onboarding" % world["cust"].id,
                    json={"email": "a%d@example.com" % next(_SEQ),
                          "confirm_email": "different@example.com",
                          "role": "org_admin"},
                    headers=_h(db_session, world["god"]))
    assert r.status_code == 400


# ═════════════════════════════════════════════════════════════════════════════
# 6. The authorized-context list is how the new access is reached
# ═════════════════════════════════════════════════════════════════════════════

def test_the_new_context_appears_in_the_supported_context_list(
        client, db_session, world, launched):
    seller = _seller(db_session, world)
    _send(client, db_session, world, seller.email, role="org_admin")

    db_session.expire_all()
    again = db_session.query(User).filter(User.id == seller.id).first()
    ctx = client.get("/auth/my-contexts", headers=_h(db_session, again)).json()

    ws = ctx.get("workspace_contexts") or []
    assert world["cust"].id in [w["organization_id"] for w in ws]
    assert world["other"].id not in [w["organization_id"] for w in ws]
    # Both contexts at once: the workspace AND the back office he already had.
    assert ctx.get("has_back_office") is True


def test_the_onboarded_person_reaches_THIS_customers_launch_pad(
        client, db_session, world, launched):
    """Test case 9, end to end and through the real guard.

    `require_tenant_user` lets a NULL-column identity through when they have
    SELECTED a workspace they hold a membership in, and `/launch` resolves the
    customer from that selection rather than from anything in the URL. So the
    proof is: with Atlantis selected he gets Atlantis's launch, and with the
    other customer selected he is refused — one membership, one workspace.
    """
    from app.services.workspace_access import WORKSPACE_HEADER

    seller = _seller(db_session, world)
    _send(client, db_session, world, seller.email, role="org_admin")

    db_session.expire_all()
    again = db_session.query(User).filter(User.id == seller.id).first()
    h = _h(db_session, again)

    mine = client.get("/launch/me",
                      headers={**h, WORKSPACE_HEADER: world["cust"].id})
    assert mine.status_code == 200, mine.text
    body = mine.json()
    # Whatever shape the payload has, it must be THIS customer's.
    assert world["other"].id not in mine.text
    assert (body.get("organization_id") == world["cust"].id
            or world["cust"].id in mine.text)

    # A workspace he holds no membership in is not selectable, so the same
    # request for the other customer does not become that customer's launch.
    theirs = client.get("/launch/me",
                        headers={**h, WORKSPACE_HEADER: world["other"].id})
    assert theirs.status_code in (403, 404, 409), theirs.text
    assert world["other"].id not in theirs.text


def test_without_selecting_a_workspace_he_is_still_brand_sales(
        client, db_session, world, launched):
    """The grant does not silently become his default tenancy.

    His `users.organization_id` is deliberately left NULL, so a request that
    names no workspace is still a brand-sales request — which is what he is
    when he has not chosen to be inside a customer.
    """
    seller = _seller(db_session, world)
    _send(client, db_session, world, seller.email, role="org_admin")

    db_session.expire_all()
    again = db_session.query(User).filter(User.id == seller.id).first()
    assert again.organization_id is None, \
        "a customer became this salesperson's default tenancy"

    r = client.get("/launch/me", headers=_h(db_session, again))
    assert r.status_code in (403, 404, 409)
