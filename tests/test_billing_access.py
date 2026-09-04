"""P3 — billing authority: ACTIVE WORKSPACE FIRST.

The defect these tests pin closed: billing used to decide whose money it was
looking at from `users.organization_id`, the tenant a person was historically
attached to. For anybody holding more than one membership that is the wrong
organization, and it was both readable and writable.

Most of this file is negative. A billing authorization test that only proves
the happy path proves nothing about the thing that matters.
"""

import pytest
from fastapi import HTTPException

from app.models.models import Organization, User
from app.services import billing_access as access
from app.services.auth_service import hash_password
from app.services.billing_access import BILLING_MANAGE, BILLING_VIEW


# ── helpers ──────────────────────────────────────────────────────────────────

def _org(db, name):
    o = Organization(name=name, slug=name.lower().replace(" ", "-"),
                     plan="standard")
    db.add(o)
    db.commit()
    return o


def _user(db, org, role="org_admin", email=None):
    u = User(organization_id=org.id if org else None,
             email=email or ("%s@example.com" % (role + str(id(org))[-6:])),
             password_hash=hash_password("TestPass123!"),
             full_name="Billing Person", role=role,
             must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _grant(db, user, org, key):
    """Grant a capability the way production does - both gates."""
    import json
    from app.models.models import UserCapabilityGrant
    existing = json.loads(org.delegated_capabilities or "[]")
    if key not in existing:
        existing.append(key)
    org.delegated_capabilities = json.dumps(existing)
    db.add(UserCapabilityGrant(user_id=user.id, organization_id=org.id,
                               capability=key, is_active=True))
    db.commit()


# ── the baseline: existing org admins keep working ───────────────────────────

def test_org_admin_can_view_and_manage_their_active_org(db_session):
    """NO LOCKOUT. An org_admin who could open billing yesterday can open it
    today - now correctly scoped to the active organization."""
    org = _org(db_session, "Restland")
    admin = _user(db_session, org, "org_admin")

    scope = access.resolve_billing_scope(db_session, admin)

    assert scope.organization_id == org.id
    assert scope.can_view is True
    assert scope.can_manage is True


def test_a_plain_advisor_has_no_billing_access(db_session):
    org = _org(db_session, "Restland")
    advisor = _user(db_session, org, "advisor")

    scope = access.resolve_billing_scope(db_session, advisor)

    assert scope.can_view is False
    assert scope.can_manage is False


def test_a_platform_identity_with_no_workspace_has_no_billing_subject(db_session):
    """A brand salesperson holds no customer membership. There is nobody to
    bill, and that must read as 'no subject', not as 'the default tenant'."""
    seller = _user(db_session, None, "advisor", email="seller@brand.com")

    scope = access.resolve_billing_scope(db_session, seller)

    assert scope.organization is None
    assert scope.can_view is False


# ── capabilities extend the baseline ─────────────────────────────────────────

def test_billing_view_grant_lets_a_non_admin_read_but_not_manage(db_session):
    """The reason these are capabilities and not a role check: a bookkeeper who
    is not an org_admin can reconcile the books without being able to move
    money."""
    org = _org(db_session, "Restland")
    bookkeeper = _user(db_session, org, "org_admin", email="book@example.com")
    # Downgrade to a non-admin role, then grant only view.
    bookkeeper.role = "advisor"
    db_session.commit()
    _grant(db_session, bookkeeper, org, BILLING_VIEW)

    scope = access.resolve_billing_scope(db_session, bookkeeper)

    assert scope.can_view is True
    assert scope.can_manage is False


def test_billing_manage_grant_implies_view(db_session):
    org = _org(db_session, "Restland")
    person = _user(db_session, org, "advisor", email="mgr@example.com")
    _grant(db_session, person, org, BILLING_MANAGE)

    scope = access.resolve_billing_scope(db_session, person)

    assert scope.can_manage is True
    assert scope.can_view is True


def test_a_grant_in_one_org_does_not_carry_to_another(db_session):
    """A capability is scoped to the organization it was granted in. The same
    person standing in a different workspace holds nothing."""
    org_a = _org(db_session, "Restland")
    org_b = _org(db_session, "Somebody Else")
    person = _user(db_session, org_a, "advisor", email="x@example.com")
    _grant(db_session, person, org_a, BILLING_MANAGE)

    # Standing in A: granted.
    assert access.resolve_billing_scope(db_session, person).can_manage is True

    # Moved to B: the grant does not follow.
    person.organization_id = org_b.id
    db_session.commit()
    scope = access.resolve_billing_scope(db_session, person)
    assert scope.organization_id == org_b.id
    assert scope.can_manage is False


# ── cross-tenant refusal ─────────────────────────────────────────────────────

def test_scope_never_returns_an_organization_the_caller_did_not_resolve_into(db_session):
    """THE CENTRAL PROPERTY. There is no parameter - path, body or query - that
    names the organization, so knowing another tenant's UUID buys nothing. The
    subject is always what the workspace resolver returned."""
    mine = _org(db_session, "Restland")
    theirs = _org(db_session, "Somebody Else")
    admin = _user(db_session, mine, "org_admin")

    scope = access.resolve_billing_scope(db_session, admin)

    assert scope.organization_id == mine.id
    assert scope.organization_id != theirs.id


def test_another_organizations_stripe_customer_is_refused(db_session):
    """Supplying a Stripe customer id that belongs to a different tenant must
    404 - not 403, which would confirm the id exists."""
    mine = _org(db_session, "Restland")
    mine.stripe_customer_id = "cus_mine"
    theirs = _org(db_session, "Somebody Else")
    theirs.stripe_customer_id = "cus_theirs"
    db_session.commit()
    admin = _user(db_session, mine, "org_admin")
    scope = access.resolve_billing_scope(db_session, admin)

    access.assert_owns_stripe_customer(scope, "cus_mine")      # allowed

    with pytest.raises(HTTPException) as exc:
        access.assert_owns_stripe_customer(scope, "cus_theirs")
    assert exc.value.status_code == 404


def test_an_unknown_stripe_customer_is_refused(db_session):
    org = _org(db_session, "Restland")
    org.stripe_customer_id = "cus_mine"
    db_session.commit()
    scope = access.resolve_billing_scope(
        db_session, _user(db_session, org, "org_admin"))

    with pytest.raises(HTTPException):
        access.assert_owns_stripe_customer(scope, "cus_invented")


# ── the dependencies refuse, they do not merely report ───────────────────────

def test_require_billing_view_refuses_a_user_without_access(db_session):
    org = _org(db_session, "Restland")
    advisor = _user(db_session, org, "advisor")

    with pytest.raises(HTTPException) as exc:
        access.require_billing_view(request=None, db=db_session,
                                    current_user=advisor)
    assert exc.value.status_code == 403


def test_require_billing_manage_refuses_a_view_only_user(db_session):
    """View is not manage. A read grant must not open a payment flow."""
    org = _org(db_session, "Restland")
    person = _user(db_session, org, "advisor", email="ro@example.com")
    _grant(db_session, person, org, BILLING_VIEW)

    assert access.require_billing_view(request=None, db=db_session,
                                       current_user=person).can_view is True

    with pytest.raises(HTTPException) as exc:
        access.require_billing_manage(request=None, db=db_session,
                                      current_user=person)
    assert exc.value.status_code == 403


def test_require_billing_view_refuses_a_caller_with_no_workspace(db_session):
    seller = _user(db_session, None, "advisor", email="noorg@brand.com")

    with pytest.raises(HTTPException) as exc:
        access.require_billing_view(request=None, db=db_session,
                                    current_user=seller)
    assert exc.value.status_code == 403


# ── platform access follows the existing model ───────────────────────────────

def test_god_admin_billing_subject_still_comes_from_the_active_workspace(db_session):
    """The owner's authority is not a customer's grant - but WHICH customer
    they are acting on is still the active workspace. god with no customer
    selected has no billing subject, which is correct: there is nobody to
    bill."""
    god = _user(db_session, None, "god_admin", email="god@platform.com")

    scope = access.resolve_billing_scope(db_session, god)

    assert scope.can_view is True and scope.can_manage is True
    assert scope.organization is None

    org = _org(db_session, "Restland")
    god.organization_id = org.id
    db_session.commit()
    assert access.resolve_billing_scope(db_session, god).organization_id == org.id


# ── a capability check that cannot run is a denial ───────────────────────────

def test_a_failing_capability_lookup_denies_rather_than_allows(db_session, monkeypatch):
    """Fail closed. A resolver that raises must never read as a pass."""
    org = _org(db_session, "Restland")
    person = _user(db_session, org, "advisor", email="boom@example.com")

    def _explode(*a, **kw):
        raise RuntimeError("capability backend unavailable")

    monkeypatch.setattr(access.caps, "user_has_grant", _explode)

    scope = access.resolve_billing_scope(db_session, person)
    assert scope.can_view is False
    assert scope.can_manage is False


# ── the webhook route is a system flow, not a tenant UI flow ─────────────────

def test_the_stripe_webhook_route_is_not_behind_tenant_authorization():
    """P0's webhook is called by Stripe, which holds no session and belongs to
    no workspace. Putting tenant authorization on it would break every
    delivery; its authentication is the signature, checked inside the handler."""
    import inspect
    from app.routers import billing_router
    src = inspect.getsource(billing_router.stripe_webhook)
    assert "require_billing_view" not in src
    assert "require_billing_manage" not in src


def test_every_tenant_billing_route_is_gated_by_the_new_helpers():
    """STRUCTURAL. A route added later that forgets the guard, or that goes
    back to the legacy column, fails here rather than in production."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "app" / "routers" / "billing_router.py").read_text(encoding="utf-8")

    # The legacy pairing is gone from every route body.
    assert "Depends(_require_admin)" not in src
    assert "Organization.id == current_user.organization_id" not in src

    for route in ('@router.get("/plans")', '@router.get("/subscription")',
                  '@router.post("/checkout")', '@router.post("/portal")'):
        start = src.index(route)
        body = src[start:start + 400]
        assert ("require_billing_view" in body
                or "require_billing_manage" in body), \
            "%s is not gated by a billing_access dependency" % route
