"""WHICH CUSTOMER'S ENTITLEMENT A FEATURE GATE EVALUATES.

THE DEFECT THIS FILE DEFENDS AGAINST
====================================
`require_feature` resolved the tenant from `users.organization_id` alone.
`require_tenant_user` does not: since the context switcher it also admits a
caller who has SELECTED a workspace they hold an active customer_org
membership in. So one request got two different answers to "which tenant are
you in" — the route let a seconded person on, and the feature gate then told
them their account had no customer organization.

That is the follow-up to 84a6032, which made customer access additive. A
person onboarded into a customer could select it and reach the Launch Pad,
and then be refused any feature-gated route in the same workspace.

THE RULE, WHICH IS NOT A NEW ONE
--------------------------------
Both gates now resolve through `lead_scope.active_workspace_org_id`, the same
seam every lead query uses. Its order is:

  1. a workspace the caller SELECTED and holds an ACTIVE membership in
  2. `users.organization_id`

A selected id with no membership behind it is discarded by `workspace_access`
before this ever sees it, so nothing here can be widened by asserting a
header, and a single-context customer user — who selects nothing — resolves
exactly as they did before.
"""

import itertools
import json

import pytest

from app.models.models import Organization, Platform, User
from app.models.sales_models import (ROLE_SALES_MANAGER, SCOPE_BRAND_SALES_ORG,
                                     SCOPE_CUSTOMER_ORG, BrandSalesOrg,
                                     Membership)
from app.services.auth_service import create_access_token, hash_password
from app.services.workspace_access import WORKSPACE_HEADER

_SEQ = itertools.count(1)

# A feature-gated customer route that exists and is cheap to call. Campaigns is
# mounted with Depends(require_feature("campaigns")) in app/main.py.
GATED = "/campaigns"
GATED_FEATURE = "campaigns"


def _h(db, u, workspace=None):
    h = {"Authorization": "Bearer " + create_access_token(u, db)}
    if workspace:
        h[WORKSPACE_HEADER] = workspace
    return h


def _user(db, role="advisor", org_id=None, email=None, name="Person"):
    u = User(organization_id=org_id,
             email=email or ("fg%d@example.com" % next(_SEQ)),
             password_hash=hash_password("x"), full_name=name, role=role,
             must_change_password=False, is_active=True)
    db.add(u)
    db.commit()
    return u


def _org(db, plat, features=None, name=None):
    """`enabled_features` None means the legacy 'everything' mode."""
    o = Organization(name=name or ("FG Customer %d" % next(_SEQ)),
                     slug="fg-cust-%d" % next(_SEQ), plan="standard",
                     platform_id=plat.id,
                     enabled_features=(None if features is None
                                       else json.dumps(features)))
    db.add(o)
    db.commit()
    return o


@pytest.fixture()
def world(db_session):
    plat = Platform(name="FG Brand %d" % next(_SEQ), slug="fg-brand-%d" % next(_SEQ))
    db_session.add(plat)
    db_session.commit()
    bso = BrandSalesOrg(platform_id=plat.id, name="FG Sales %d" % next(_SEQ),
                        slug="fg-bso-%d" % next(_SEQ))
    db_session.add(bso)
    db_session.commit()
    # A: the feature is enabled. B: it is not.
    a = _org(db_session, plat, features=[GATED_FEATURE, "leads"])
    b = _org(db_session, plat, features=["leads"])
    return dict(plat=plat, bso=bso, a=a, b=b)


def _member(db, user, org, role="org_admin"):
    db.add(Membership(user_id=user.id, scope_type=SCOPE_CUSTOMER_ORG,
                      scope_id=org.id, role=role, is_active=True))
    db.commit()
    return user


def _seller(db, world):
    """Brand-sales identity: NULL organization_id, real brand membership."""
    u = _user(db, role=ROLE_SALES_MANAGER, org_id=None, name="Seconded Seller")
    db.add(Membership(user_id=u.id, scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id=world["bso"].id, role=ROLE_SALES_MANAGER,
                      is_active=True))
    db.commit()
    return u


def _allowed(response):
    """The gate passed if we did not get its two refusals.

    The route beyond the gate may answer anything — 200, 404, 422. What must
    not come back is the gate's own 403 ("no customer organization") or its
    402 (not entitled).
    """
    return response.status_code not in (402, 403)


# ═════════════════════════════════════════════════════════════════════════════
# 1. Legacy single-context behaviour is untouched
# ═════════════════════════════════════════════════════════════════════════════

def test_a_normal_customer_user_still_passes_an_enabled_gate(client, db_session,
                                                             world):
    u = _member(db_session, _user(db_session, role="org_admin",
                                  org_id=world["a"].id), world["a"])
    r = client.get(GATED, headers=_h(db_session, u))
    assert _allowed(r), r.text


def test_a_normal_customer_user_is_still_refused_a_disabled_gate(client,
                                                                 db_session,
                                                                 world):
    u = _member(db_session, _user(db_session, role="org_admin",
                                  org_id=world["b"].id), world["b"])
    r = client.get(GATED, headers=_h(db_session, u))
    assert r.status_code == 402, r.text


def test_the_legacy_column_still_answers_when_nothing_is_selected(client,
                                                                  db_session,
                                                                  world):
    """No X-Workspace-Id at all: the column is the answer, exactly as before."""
    u = _user(db_session, role="advisor", org_id=world["a"].id)
    r = client.get(GATED, headers=_h(db_session, u))
    assert _allowed(r), r.text


# ═════════════════════════════════════════════════════════════════════════════
# 2. The seconded identity — the case that was broken
# ═════════════════════════════════════════════════════════════════════════════

def test_a_seconded_identity_passes_the_gate_of_the_workspace_it_selected(
        client, db_session, world):
    seller = _member(db_session, _seller(db_session, world), world["a"])
    assert seller.organization_id is None

    r = client.get(GATED, headers=_h(db_session, seller,
                                     workspace=world["a"].id))
    assert _allowed(r), (
        "a member of this workspace was refused its own feature: %s" % r.text)


def test_a_seconded_identity_is_refused_when_that_workspace_lacks_the_feature(
        client, db_session, world):
    """Entitlement is still the customer's, not the person's."""
    seller = _member(db_session, _seller(db_session, world), world["b"])
    r = client.get(GATED, headers=_h(db_session, seller,
                                     workspace=world["b"].id))
    assert r.status_code == 402, r.text


def test_a_seconded_identity_with_no_selection_is_still_brand_sales(
        client, db_session, world):
    """The membership does not become a default tenancy. No selection, no
    tenant — the same refusal a brand-sales identity has always had."""
    seller = _member(db_session, _seller(db_session, world), world["a"])
    r = client.get(GATED, headers=_h(db_session, seller))
    assert r.status_code == 403, r.text


# ═════════════════════════════════════════════════════════════════════════════
# 3. Isolation — a selection is not a grant
# ═════════════════════════════════════════════════════════════════════════════

def test_selecting_a_workspace_you_do_not_hold_grants_nothing(client,
                                                              db_session,
                                                              world):
    """A brand-sales identity naming a customer they have no membership in.

    The selection is discarded, there is no column to fall back to, and the
    answer is the same refusal as naming nothing at all.
    """
    seller = _seller(db_session, world)
    r = client.get(GATED, headers=_h(db_session, seller,
                                     workspace=world["a"].id))
    assert r.status_code == 403, r.text


def test_a_member_of_one_customer_cannot_borrow_anothers_entitlement(
        client, db_session, world):
    """Membership in B (feature OFF), header asserting A (feature ON).

    The security property is that A's entitlement is never evaluated for this
    caller. The unauthorized selection is discarded by `workspace_access`, so
    the request resolves to the caller's own tenant and is refused on B's
    allow-list — it does not become an A request.
    """
    u = _member(db_session, _user(db_session, role="org_admin",
                                  org_id=world["b"].id), world["b"])
    r = client.get(GATED, headers=_h(db_session, u, workspace=world["a"].id))
    assert r.status_code == 402, (
        "an unauthorized selection reached another customer's entitlement: %s"
        % r.text)


def test_a_seconded_identity_cannot_reach_an_unheld_workspaces_feature(
        client, db_session, world):
    """Holds A (feature ON), asserts B (feature OFF) — and vice versa is the
    test above. Neither direction may resolve to the workspace not held."""
    seller = _member(db_session, _seller(db_session, world), world["a"])
    r = client.get(GATED, headers=_h(db_session, seller,
                                     workspace=world["b"].id))
    # The B selection is discarded; with no column, there is no tenant at all.
    assert r.status_code == 403, r.text


# ═════════════════════════════════════════════════════════════════════════════
# 4. Two memberships, two answers, no bleed
# ═════════════════════════════════════════════════════════════════════════════

def test_each_selected_workspace_is_judged_on_its_own_entitlement(client,
                                                                  db_session,
                                                                  world):
    seller = _seller(db_session, world)
    _member(db_session, seller, world["a"])          # feature ON
    _member(db_session, seller, world["b"])          # feature OFF

    on = client.get(GATED, headers=_h(db_session, seller,
                                      workspace=world["a"].id))
    off = client.get(GATED, headers=_h(db_session, seller,
                                       workspace=world["b"].id))
    assert _allowed(on), on.text
    assert off.status_code == 402, off.text


# ═════════════════════════════════════════════════════════════════════════════
# 5. God safety
# ═════════════════════════════════════════════════════════════════════════════

def test_god_still_passes_every_gate(client, db_session, world):
    god = _user(db_session, role="god_admin", org_id=None, name="Owner")
    for org in (world["a"], world["b"]):
        r = client.get(GATED, headers=_h(db_session, god, workspace=org.id))
        assert _allowed(r), r.text


def test_a_customer_membership_still_grants_no_platform_authority(client,
                                                                  db_session,
                                                                  world):
    seller = _member(db_session, _seller(db_session, world), world["a"])
    h = _h(db_session, seller, workspace=world["a"].id)
    assert client.get("/god/customers", headers=h).status_code in (401, 403, 404)


# ═════════════════════════════════════════════════════════════════════════════
# 6. The capability gate that shares the same resolution
# ═════════════════════════════════════════════════════════════════════════════

def test_a_seconded_org_admin_holds_the_workspace_role_for_capabilities(
        db_session, world):
    """`require_feature_capability` short-circuits on org_admin. A seconded
    person's PLATFORM role is not org_admin, so the role that has to be
    consulted is the one on their membership in this workspace."""
    from app.services import workspace_access

    seller = _member(db_session, _seller(db_session, world), world["a"],
                     role="org_admin")
    assert seller.role == ROLE_SALES_MANAGER
    assert workspace_access.workspace_role(seller, db_session,
                                           world["a"].id) == "org_admin"
    assert workspace_access.workspace_role(seller, db_session,
                                           world["b"].id) is None
