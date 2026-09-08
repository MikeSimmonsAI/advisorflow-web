"""Acceptance item 4 & 5 — the limit is enforced at the AUTHORITATIVE layer.

═══════════════════════════════════════════════════════════════════════════
WHAT test_plan_limits.py DOES NOT COVER
═══════════════════════════════════════════════════════════════════════════

That file proves `POST /admin/users` refuses the third seat on a two-seat
plan. It proves the guard works. It says nothing about the OTHER ten doors,
and a ceiling honoured by one door is not a ceiling.

This file covers the breadth requirement:

  · the shared service, not a per-router copy, is what decides
  · the batch counter enforces EXACTLY, not "the whole file or nothing"
  · a declared bypass is PRIVILEGED (role-checked) and AUDITED, and an
    undeclared or misspelled one is REFUSED rather than silently permissive
  · a customer-scope MEMBERSHIP consumes a seat - the second door, which the
    original count missed entirely
  · the check and the insert cannot interleave (item 5)
"""

import itertools

import pytest
from fastapi import HTTPException

from app.models.billing_models import BrandBillingPlan
from app.models.models import AuditLogEntry, Lead, Organization, Platform, User
from app.models.sales_models import Membership, SCOPE_CUSTOMER_ORG
from app.services import plan_limits, workspace_access
from app.services.auth_service import hash_password

_SEQ = itertools.count(9000)


def _platform(db, label="Alpha"):
    p = Platform(name=label, slug="%s-%d" % (label.lower(), next(_SEQ)))
    db.add(p); db.commit()
    return p


def _plan(db, platform, key, *, max_users=None, max_leads=None):
    plan = BrandBillingPlan(
        platform_id=platform.id, key=key, name=key.title(),
        monthly_cents=49700, currency="usd", is_purchasable=True,
        is_active=True, sort_order=10,
        max_users=max_users, max_leads=max_leads)
    db.add(plan); db.commit()
    return plan


def _org(db, platform, plan_key):
    n = next(_SEQ)
    org = Organization(name="Cust %d" % n, slug="cust-%d" % n,
                       platform_id=platform.id, is_active=True,
                       plan=plan_key, billing_plan_key=plan_key,
                       billing_status="active")
    db.add(org); db.commit()
    return org


def _user(db, org, *, role="advisor", is_active=True):
    u = User(organization_id=org.id if org else None,
             email="p%d@evosyspro.live" % next(_SEQ),
             password_hash=hash_password("MemberPass123!"),
             full_name="Person", role=role, is_active=is_active,
             must_change_password=False)
    db.add(u); db.commit()
    return u


def _lead(db, org):
    lead = Lead(organization_id=org.id, first_name="A",
                last_name="Lead%d" % next(_SEQ), status="new")
    db.add(lead); db.commit()
    return lead


@pytest.fixture()
def brand(db_session):
    p = _platform(db_session, "Alpha")
    _plan(db_session, p, "tiny", max_users=2, max_leads=3)
    _plan(db_session, p, "uncapped", max_users=None, max_leads=None)
    return p


# ═══════════════════════════════════════════════════════════════════════════
# THE BATCH COUNTER: EXACT, NOT ALL-OR-NOTHING
# ═══════════════════════════════════════════════════════════════════════════

def test_batch_counter_admits_exactly_the_rows_that_fit(db_session, brand):
    """A 10-row import into a 3-lead plan imports 3 and stops - it does not
    refuse the whole file, and it does not let all 10 through."""
    org = _org(db_session, brand, "tiny")
    counter = plan_limits.CapacityCounter(db_session, org, plan_limits.LIMIT_LEADS)

    admitted = 0
    for _ in range(10):
        if not counter.has_room(1):
            break
        counter.take(1)
        admitted += 1

    assert admitted == 3, "the plan holds 3 leads; %d were admitted" % admitted
    assert counter.remaining == 0


def test_batch_counter_takes_existing_usage_into_account(db_session, brand):
    org = _org(db_session, brand, "tiny")
    _lead(db_session, org)
    _lead(db_session, org)

    counter = plan_limits.CapacityCounter(db_session, org, plan_limits.LIMIT_LEADS)
    assert counter.remaining == 1
    counter.take(1)
    assert not counter.has_room(1)
    with pytest.raises(HTTPException) as exc:
        counter.take(1)
    assert exc.value.status_code == 402


def test_batch_counter_is_unlimited_when_no_ceiling_is_configured(db_session, brand):
    org = _org(db_session, brand, "uncapped")
    counter = plan_limits.CapacityCounter(db_session, org, plan_limits.LIMIT_LEADS)
    assert counter.unlimited
    assert counter.remaining is None
    counter.take(10_000)          # must not raise


def test_batch_counter_counts_once_not_once_per_row(db_session, brand):
    """The reason CapacityCounter exists. If it re-counted per row, a large
    import would be O(n) full table counts."""
    org = _org(db_session, brand, "uncapped")
    calls = {"n": 0}
    real = plan_limits.usage_for

    def counting(db, o, key):
        calls["n"] += 1
        return real(db, o, key)

    plan_limits.usage_for = counting
    try:
        counter = plan_limits.CapacityCounter(db_session, org, plan_limits.LIMIT_LEADS)
        for _ in range(50):
            counter.take(1)
    finally:
        plan_limits.usage_for = real

    assert calls["n"] <= 1, "usage was counted %d times for one batch" % calls["n"]


# ═══════════════════════════════════════════════════════════════════════════
# BYPASSES ARE PRIVILEGED, NAMED AND AUDITED - NEVER ACCIDENTAL
# ═══════════════════════════════════════════════════════════════════════════

def test_a_bypass_without_the_role_is_refused(db_session, brand):
    org = _org(db_session, brand, "tiny")
    _user(db_session, org, role="org_admin")
    _user(db_session, org)

    plain_admin = _user(db_session, org, role="org_admin")
    with pytest.raises(plan_limits.LimitBypassDenied) as exc:
        plan_limits.require_capacity(
            db_session, org, plan_limits.LIMIT_USERS, adding=1,
            bypass=plan_limits.BYPASS_PLATFORM_PROVISIONING, actor=plain_admin)
    assert exc.value.status_code == 403


def test_an_unknown_bypass_reason_is_refused_not_silently_permissive(db_session, brand):
    """A typo'd bypass string must not become 'skip the check'."""
    org = _org(db_session, brand, "tiny")
    god = _user(db_session, None, role="god_admin")
    with pytest.raises(plan_limits.LimitBypassDenied):
        plan_limits.require_capacity(
            db_session, org, plan_limits.LIMIT_USERS, adding=1,
            bypass="platform_provisionning", actor=god)   # deliberate typo


def test_a_bypass_with_no_actor_at_all_is_refused(db_session, brand):
    org = _org(db_session, brand, "tiny")
    with pytest.raises(plan_limits.LimitBypassDenied):
        plan_limits.require_capacity(
            db_session, org, plan_limits.LIMIT_USERS, adding=1,
            bypass=plan_limits.BYPASS_PLATFORM_PROVISIONING, actor=None)


def test_a_legitimate_bypass_is_allowed_and_writes_an_audit_row(db_session, brand):
    org = _org(db_session, brand, "tiny")
    _user(db_session, org, role="org_admin")
    _user(db_session, org)                      # org is now full at 2/2
    god = _user(db_session, None, role="god_admin")

    before = db_session.query(AuditLogEntry).filter(
        AuditLogEntry.action == "plan_limit.bypass").count()

    plan_limits.require_capacity(
        db_session, org, plan_limits.LIMIT_USERS, adding=1,
        bypass=plan_limits.BYPASS_PLATFORM_PROVISIONING, actor=god)
    db_session.commit()

    after = db_session.query(AuditLogEntry).filter(
        AuditLogEntry.action == "plan_limit.bypass").all()
    assert len(after) == before + 1, "a bypass must leave a trace"

    row = after[-1]
    assert row.target_id == org.id
    assert "platform_provisioning" in (row.details or "")


def test_demo_seed_bypass_accepts_super_admin_but_provisioning_does_not(db_session, brand):
    """The two exceptions have different privilege bars, deliberately."""
    org = _org(db_session, brand, "tiny")
    _user(db_session, org, role="org_admin")
    _user(db_session, org)
    supers = _user(db_session, org, role="super_admin")

    # Allowed: seeding demo fixtures.
    plan_limits.require_capacity(
        db_session, org, plan_limits.LIMIT_LEADS, adding=100,
        bypass=plan_limits.BYPASS_DEMO_SEED, actor=supers)

    # Refused: provisioning is god-only.
    with pytest.raises(plan_limits.LimitBypassDenied):
        plan_limits.require_capacity(
            db_session, org, plan_limits.LIMIT_USERS, adding=1,
            bypass=plan_limits.BYPASS_PLATFORM_PROVISIONING, actor=supers)


# ═══════════════════════════════════════════════════════════════════════════
# THE SECOND SEAT DOOR: CUSTOMER-SCOPE MEMBERSHIPS
# ═══════════════════════════════════════════════════════════════════════════

def test_a_customer_scope_membership_consumes_a_seat(db_session, brand):
    """Someone seconded into a customer's workspace is a seat, even though
    their User.organization_id points somewhere else entirely."""
    org = _org(db_session, brand, "tiny")
    _user(db_session, org, role="org_admin")               # seat 1

    outsider = _user(db_session, None, role="advisor")     # no home org
    assert plan_limits.usage_for(db_session, org, plan_limits.LIMIT_USERS) == 1

    workspace_access.grant_workspace_membership(
        db_session, user_id=outsider.id, organization_id=org.id)

    assert plan_limits.usage_for(db_session, org, plan_limits.LIMIT_USERS) == 2, (
        "a live customer-scope membership must count as a seat")


def test_granting_a_membership_past_the_seat_limit_is_refused(db_session, brand):
    org = _org(db_session, brand, "tiny")
    _user(db_session, org, role="org_admin")
    _user(db_session, org)                                  # 2/2, full

    outsider = _user(db_session, None, role="advisor")
    with pytest.raises(HTTPException) as exc:
        workspace_access.grant_workspace_membership(
            db_session, user_id=outsider.id, organization_id=org.id)
    assert exc.value.status_code == 402


def test_reactivating_an_existing_membership_does_not_need_a_new_seat(db_session, brand):
    """A role change or re-invite must not be refused for capacity the person
    is already occupying."""
    org = _org(db_session, brand, "tiny")
    _user(db_session, org, role="org_admin")
    outsider = _user(db_session, None, role="advisor")

    m = workspace_access.grant_workspace_membership(
        db_session, user_id=outsider.id, organization_id=org.id)
    assert plan_limits.usage_for(db_session, org, plan_limits.LIMIT_USERS) == 2

    # Org is now full. Re-granting the SAME person must still work.
    again = workspace_access.grant_workspace_membership(
        db_session, user_id=outsider.id, organization_id=org.id, role="org_admin")
    assert again.id == m.id
    assert again.role == "org_admin"


def test_one_person_holding_both_a_home_and_a_membership_is_one_seat(db_session, brand):
    org = _org(db_session, brand, "uncapped")
    person = _user(db_session, org, role="advisor")
    workspace_access.grant_workspace_membership(
        db_session, user_id=person.id, organization_id=org.id)
    assert plan_limits.usage_for(db_session, org, plan_limits.LIMIT_USERS) == 1


def test_a_revoked_membership_stops_consuming_a_seat(db_session, brand):
    org = _org(db_session, brand, "tiny")
    _user(db_session, org, role="org_admin")
    outsider = _user(db_session, None, role="advisor")
    workspace_access.grant_workspace_membership(
        db_session, user_id=outsider.id, organization_id=org.id)
    assert plan_limits.usage_for(db_session, org, plan_limits.LIMIT_USERS) == 2

    workspace_access.revoke_workspace_membership(
        db_session, user_id=outsider.id, organization_id=org.id)
    assert plan_limits.usage_for(db_session, org, plan_limits.LIMIT_USERS) == 1


def test_a_membership_in_a_DIFFERENT_org_does_not_consume_this_orgs_seat(db_session, brand):
    """Tenant isolation of the seat count itself."""
    a = _org(db_session, brand, "tiny")
    b = _org(db_session, brand, "tiny")
    _user(db_session, a, role="org_admin")
    outsider = _user(db_session, None, role="advisor")

    workspace_access.grant_workspace_membership(
        db_session, user_id=outsider.id, organization_id=b.id)

    assert plan_limits.usage_for(db_session, a, plan_limits.LIMIT_USERS) == 1
    assert plan_limits.usage_for(db_session, b, plan_limits.LIMIT_USERS) == 1


# ═══════════════════════════════════════════════════════════════════════════
# ITEM 5 — THE CHECK AND THE INSERT CANNOT INTERLEAVE
# ═══════════════════════════════════════════════════════════════════════════

def test_the_capacity_check_locks_the_organization_row_on_postgres(db_session, brand):
    """The race is count-then-insert. Two requests both count 1 on a 2-seat
    plan, both conclude there is room, both insert: three seats.

    The defence is a row lock taken BEFORE the count and held to the end of
    the caller's transaction. On SQLite (this suite) FOR UPDATE does not
    exist, so what is asserted here is that the lock is ATTEMPTED and that it
    precedes the count - the ordering is the part that can regress silently.
    """
    org = _org(db_session, brand, "tiny")
    events = []

    real_lock = plan_limits._lock_org
    real_usage = plan_limits.usage_for

    def lock(db, o):
        events.append("lock")
        return real_lock(db, o)

    def usage(db, o, key):
        events.append("count")
        return real_usage(db, o, key)

    plan_limits._lock_org = lock
    plan_limits.usage_for = usage
    try:
        plan_limits.require_capacity(db_session, org, plan_limits.LIMIT_USERS, adding=1)
    finally:
        plan_limits._lock_org = real_lock
        plan_limits.usage_for = real_usage

    assert events, "neither the lock nor the count ran"
    assert events[0] == "lock", (
        "the count ran before the lock (%r) - a count taken outside the lock "
        "can go stale before the caller inserts" % events)


def test_the_batch_counter_also_locks_before_counting(db_session, brand):
    org = _org(db_session, brand, "tiny")
    events = []
    real_lock = plan_limits._lock_org
    real_usage = plan_limits.usage_for

    plan_limits._lock_org = lambda db, o: events.append("lock")
    plan_limits.usage_for = lambda db, o, k: (events.append("count"), 0)[1]
    try:
        plan_limits.CapacityCounter(db_session, org, plan_limits.LIMIT_LEADS)
    finally:
        plan_limits._lock_org = real_lock
        plan_limits.usage_for = real_usage

    assert events[0] == "lock", events


def test_the_lock_statement_is_a_real_row_lock_and_targets_one_org(db_session):
    """The SQL itself, since SQLite cannot execute it. A lock that omitted the
    WHERE would serialize every organization on the platform."""
    import inspect
    src = inspect.getsource(plan_limits._lock_org)
    assert "FOR UPDATE" in src
    assert "WHERE id = :oid" in src
    assert "postgresql" in src, (
        "the lock must be dialect-guarded; emitting FOR UPDATE on SQLite "
        "would break the entire test suite rather than protect anything")


def test_a_failure_to_lock_does_not_skip_the_count(db_session, brand):
    """Fail open on the LOCK, never on the CHECK."""
    org = _org(db_session, brand, "tiny")
    _user(db_session, org, role="org_admin")
    _user(db_session, org)                       # full

    real_lock = plan_limits._lock_org

    def exploding(db, o):
        raise RuntimeError("no lock available")

    plan_limits._lock_org = exploding
    try:
        with pytest.raises(RuntimeError):
            plan_limits.require_capacity(db_session, org, plan_limits.LIMIT_USERS)
    finally:
        plan_limits._lock_org = real_lock

    # And with the real (internally-guarded) lock, the ceiling still applies.
    with pytest.raises(HTTPException) as exc:
        plan_limits.require_capacity(db_session, org, plan_limits.LIMIT_USERS)
    assert exc.value.status_code == 402
