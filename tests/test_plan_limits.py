"""FRONTEND HIDING IS NOT ENFORCEMENT.

`max_users` and `max_leads` have been on the billing catalogue since it was
built, returned to the UI, rendered on plan cards - and enforced by NOTHING. A
Starter customer whose plan advertised "up to 2 users" could add fifty, and the
only thing standing between them and doing so was a number on a marketing card.

WHAT THIS FILE DEFENDS

  1. THE CEILING IS REAL, ON THE SERVER, at the one place a customer user is
     created. 402 Payment Required, not 403: the caller is not unauthorized,
     their PLAN does not include this, and those two problems have different
     fixes and go to different people.
  2. THE MESSAGE IS ACTIONABLE. "You have reached your plan's limit of 2 users"
     is something an admin can act on; "forbidden" is not.
  3. THE CURRENT PLAN'S LIMIT APPLIES, NEVER THE PENDING DOWNGRADE'S. This is
     the decided downgrade policy expressed in code, and it is the whole
     subtlety of the module.
  4. NULL MEANS UNLIMITED, and an organization with no resolvable plan is not
     "zero of everything".
  5. NOTHING IS PRUNED. An organization already over its limit keeps every
     user it has; the ceiling stops the NEXT addition.
"""

import itertools

import pytest

from app.models.billing_models import BrandBillingPlan
from app.models.models import Organization, Platform, User
from app.services import plan_limits
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


def _platform(db, label="Alpha"):
    platform = Platform(name=label, slug="%s-%d" % (label.lower(), next(_SEQ)))
    db.add(platform)
    db.commit()
    return platform


def _plan(db, platform, key, *, monthly=49700, max_users=None, max_leads=None,
          sort_order=10):
    plan = BrandBillingPlan(
        platform_id=platform.id, key=key, name=key.title(),
        monthly_cents=monthly, currency="usd", is_purchasable=True,
        is_active=True, sort_order=sort_order,
        max_users=max_users, max_leads=max_leads)
    db.add(plan)
    db.commit()
    return plan


def _org(db, platform, plan_key=None, **kw):
    n = next(_SEQ)
    kw.setdefault("plan", plan_key or "trial")
    kw.setdefault("billing_plan_key", plan_key)
    kw.setdefault("billing_status", "active")
    org = Organization(name="Customer %d" % n, slug="customer-%d" % n,
                       platform_id=platform.id if platform else None,
                       is_active=True, **kw)
    db.add(org)
    db.commit()
    return org


def _user(db, org, *, role="advisor", is_active=True):
    user = User(organization_id=org.id,
                email="member%d@evosyspro.live" % next(_SEQ),
                password_hash=hash_password("MemberPass123!"),
                full_name="Member", role=role, is_active=is_active,
                must_change_password=False)
    db.add(user)
    db.commit()
    return user


def _admin_headers(db, org):
    """The admin is itself an ACTIVE USER of the organization, and therefore
    occupies a seat. That is not a fixture quirk - it is how a real
    organization's first seat is spent."""
    admin = _user(db, org, role="org_admin")
    return {"Authorization": "Bearer " + create_access_token(admin, db)}


def _add_user(client, headers, email="hire@evosyspro.live"):
    return client.post("/admin/users", json={
        "email": email, "full_name": "New Advisor", "role": "advisor",
    }, headers=headers)


@pytest.fixture()
def brand(db_session):
    """One brand, three tiers with real ceilings and one without."""
    platform = _platform(db_session, "Alpha")
    _plan(db_session, platform, "starter", monthly=49700,
          max_users=2, max_leads=50, sort_order=10)
    _plan(db_session, platform, "growth", monthly=99700,
          max_users=5, max_leads=500, sort_order=20)
    _plan(db_session, platform, "professional", monthly=199700,
          max_users=10, max_leads=5000, sort_order=30)
    # NULL max_users. A brand that has not set a number gets no ceiling,
    # rather than a guessed one.
    _plan(db_session, platform, "uncapped", monthly=299700,
          max_users=None, max_leads=None, sort_order=40)
    return platform


# ═══════════════════════════════════════════════════════════════════════════
# 18-19. THE SEAT LIMIT IS ENFORCED, AND IT SAYS WHAT IT IS
# ═══════════════════════════════════════════════════════════════════════════

def test_creating_a_user_at_the_seat_limit_is_refused_with_402(
        client, db_session, brand):
    """Starter advertises "up to 2 users". The second seat is the admin's own
    and the first advisor's; the third is refused."""
    org = _org(db_session, brand, "starter")
    headers = _admin_headers(db_session, org)      # seat 1
    _user(db_session, org)                          # seat 2
    assert plan_limits.usage_for(db_session, org, plan_limits.LIMIT_USERS) == 2

    response = _add_user(client, headers)

    assert response.status_code == 402, (
        "Expected 402 Payment Required and got %d. The caller is not "
        "unauthorized - their PLAN does not include this seat, and telling "
        "somebody they lack permission when they need a bigger plan sends "
        "them to the wrong person." % response.status_code)
    detail = response.json()["detail"]
    assert "up to 2 users" in detail, detail
    assert "2 are already in use" in detail, detail
    # Nothing was created by the attempt.
    assert db_session.query(User).filter(
        User.organization_id == org.id).count() == 2


def test_under_the_limit_the_user_is_created_normally(
        client, db_session, brand):
    """The ceiling must not be a general obstacle - the ordinary path is
    untouched."""
    org = _org(db_session, brand, "growth")         # max_users = 5
    headers = _admin_headers(db_session, org)

    response = _add_user(client, headers, "underlimit@evosyspro.live")

    assert response.status_code == 200, response.text
    assert response.json()["email"] == "underlimit@evosyspro.live"
    created = (db_session.query(User)
               .filter(User.email == "underlimit@evosyspro.live").one())
    assert created.organization_id == org.id
    assert created.is_active is True


# ═══════════════════════════════════════════════════════════════════════════
# 20-21. NO LIMIT WHERE NONE IS CONFIGURED, AND NO LIMIT WHERE NO PLAN IS
# ═══════════════════════════════════════════════════════════════════════════

def test_a_null_max_users_means_unlimited(client, db_session, brand):
    """NULL is not zero. A brand that has not set a number gets no ceiling."""
    org = _org(db_session, brand, "uncapped")
    headers = _admin_headers(db_session, org)
    for _ in range(5):
        _user(db_session, org)

    assert plan_limits.limit_for(db_session, org, plan_limits.LIMIT_USERS) is None
    check = plan_limits.check(db_session, org, plan_limits.LIMIT_USERS)
    assert check["allowed"] is True
    assert check["unlimited"] is True

    response = _add_user(client, headers, "uncapped@evosyspro.live")
    assert response.status_code == 200, response.text


def test_an_organization_with_no_resolvable_plan_is_not_zero_of_everything(
        client, db_session, brand):
    """Never subscribed, mid-provisioning, or a brand catalogue not yet
    seeded. All real states, and none of them means "you may have no users"."""
    unprovisioned = _org(db_session, brand, None, plan="trial")
    assert plan_limits.effective_plan(db_session, unprovisioned) is None
    assert plan_limits.limit_for(
        db_session, unprovisioned, plan_limits.LIMIT_USERS) is None

    headers = _admin_headers(db_session, unprovisioned)
    response = _add_user(client, headers, "unprovisioned@evosyspro.live")
    assert response.status_code == 200, response.text

    # Same for a brand whose catalogue has not been created at all.
    empty_brand = _platform(db_session, "Beta")
    fresh = _org(db_session, empty_brand, "starter")
    assert plan_limits.effective_plan(db_session, fresh) is None
    fresh_headers = _admin_headers(db_session, fresh)
    assert _add_user(client, fresh_headers,
                     "freshbrand@evosyspro.live").status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# 22. ONLY ACTIVE USERS CONSUME A SEAT
# ═══════════════════════════════════════════════════════════════════════════

def test_a_deactivated_user_does_not_consume_a_seat(
        client, db_session, brand):
    """Counting deactivated accounts would mean an organization could never
    recover from hitting the ceiling except by deleting people."""
    org = _org(db_session, brand, "starter")        # max_users = 2
    headers = _admin_headers(db_session, org)       # the only ACTIVE user
    for _ in range(3):
        _user(db_session, org, is_active=False)

    assert db_session.query(User).filter(
        User.organization_id == org.id).count() == 4
    assert plan_limits.usage_for(db_session, org, plan_limits.LIMIT_USERS) == 1

    response = _add_user(client, headers, "replacement@evosyspro.live")
    assert response.status_code == 200, (
        "A deactivated account consumed a seat. Usage counts ACTIVE users "
        "only: %s" % response.text)


# ═══════════════════════════════════════════════════════════════════════════
# 23. THE CURRENT PLAN'S LIMIT APPLIES, NEVER THE PENDING DOWNGRADE'S
# ═══════════════════════════════════════════════════════════════════════════

PENDING_MUST_NOT_APPLY = (
    "The PENDING plan's seat limit was enforced. That is the decided downgrade "
    "policy inverted: this organization is on Professional, has ALREADY PAID "
    "for Professional through the end of the period, and has merely SCHEDULED "
    "a downgrade to Starter. Reading billing_pending_plan_key takes their "
    "fourth and fifth seat away the moment they click the button - for a "
    "change that has not happened and money they have not saved.\n\n"
    "plan_limits.effective_plan reads billing_plan_key, and only the webhook - "
    "when Stripe's schedule actually advances - moves it.")


def test_a_scheduled_downgrade_does_not_take_seats_away_early(
        client, db_session, brand):
    """Professional (10 seats) with a downgrade to Starter (2 seats) pending.
    Three seats are in use - already over Starter's ceiling, and irrelevant
    until the change lands."""
    org = _org(db_session, brand, "professional",
               billing_pending_plan_key="starter")
    headers = _admin_headers(db_session, org)
    _user(db_session, org)
    _user(db_session, org)
    assert plan_limits.usage_for(db_session, org, plan_limits.LIMIT_USERS) == 3

    assert plan_limits.effective_plan(db_session, org).key == "professional", \
        PENDING_MUST_NOT_APPLY
    assert plan_limits.limit_for(
        db_session, org, plan_limits.LIMIT_USERS) == 10, PENDING_MUST_NOT_APPLY

    response = _add_user(client, headers, "fourthseat@evosyspro.live")
    assert response.status_code == 200, PENDING_MUST_NOT_APPLY + (
        "\n\nGot %d: %s" % (response.status_code, response.text))

    # And once the change actually lands, the lower ceiling is in force.
    org.billing_plan_key = "starter"
    org.plan = "starter"
    org.billing_pending_plan_key = None
    db_session.commit()
    assert plan_limits.limit_for(db_session, org, plan_limits.LIMIT_USERS) == 2
    assert _add_user(client, headers,
                     "afterlanding@evosyspro.live").status_code == 402


# ═══════════════════════════════════════════════════════════════════════════
# 24. ALREADY OVER THE LIMIT: NOTHING IS PRUNED
# ═══════════════════════════════════════════════════════════════════════════

def test_an_organization_over_its_limit_keeps_every_user_it_has(
        client, db_session, brand):
    """An organization already over its ceiling - because it downgraded, or
    because the limit was introduced after it grew - is not broken, locked, or
    pruned. Deleting a customer's users to make a number fit is not a billing
    decision anyone would sanction."""
    org = _org(db_session, brand, "starter")        # max_users = 2
    headers = _admin_headers(db_session, org)
    for _ in range(4):
        _user(db_session, org)
    assert plan_limits.usage_for(db_session, org, plan_limits.LIMIT_USERS) == 5

    refused = _add_user(client, headers, "sixth@evosyspro.live")
    assert refused.status_code == 402
    assert "5 are already in use" in refused.json()["detail"]

    # Every existing account is untouched and still works.
    assert plan_limits.usage_for(db_session, org, plan_limits.LIMIT_USERS) == 5
    members = db_session.query(User).filter(
        User.organization_id == org.id).all()
    assert len(members) == 5
    assert all(u.is_active for u in members)
    listed = client.get("/admin/users", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 5
    assert all(u["is_active"] for u in listed.json())


# ═══════════════════════════════════════════════════════════════════════════
# 25. THE REPORT: WHAT APPLIES NOW, WHAT IS USED, AND WHAT IS COMING
# ═══════════════════════════════════════════════════════════════════════════

def test_the_report_shows_current_limits_usage_and_the_pending_plans_limits(
        db_session, brand):
    """A customer should see what they will drop to BEFORE it happens, rather
    than discovering it when an action starts failing."""
    from datetime import datetime
    effective_at = datetime(2026, 10, 1)
    org = _org(db_session, brand, "professional",
               billing_pending_plan_key="starter",
               billing_pending_effective_at=effective_at)
    for _ in range(3):
        _user(db_session, org)

    report = plan_limits.report(db_session, org)

    assert report["plan"] == "professional"
    assert report["limits"][plan_limits.LIMIT_USERS]["limit"] == 10
    assert report["limits"][plan_limits.LIMIT_USERS]["used"] == 3
    assert report["limits"][plan_limits.LIMIT_USERS]["allowed"] is True
    assert report["limits"][plan_limits.LIMIT_LEADS]["limit"] == 5000
    assert report["limits"][plan_limits.LIMIT_LEADS]["used"] == 0

    assert report["pending_plan"] == "starter"
    assert report["pending_effective_at"] == effective_at
    assert report["pending_limits"][plan_limits.LIMIT_USERS]["limit"] == 2
    assert report["pending_limits"][plan_limits.LIMIT_USERS]["used"] == 3
    assert report["pending_limits"][plan_limits.LIMIT_LEADS]["limit"] == 50


def test_the_report_carries_no_pending_limits_when_nothing_is_scheduled(
        db_session, brand):
    org = _org(db_session, brand, "growth")
    report = plan_limits.report(db_session, org)
    assert report["plan"] == "growth"
    assert report["pending_plan"] is None
    assert report["pending_limits"] == {}
    assert report["limits"][plan_limits.LIMIT_USERS]["limit"] == 5
