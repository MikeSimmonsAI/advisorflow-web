"""OPS-01: a platform bypass with no organization must still be recordable.

THE DEFECT, exactly as it behaved. `audit_log_entries.target_id` is NOT NULL.
`_authorize_bypass` wrote `target_id=getattr(org, "id", None)`, and on the
`platform_provisioning` path there is legitimately no organization yet - that
is what the bypass is for. `log_action` flushes, the flush violated the
constraint, the surrounding `except Exception` caught it and `require_capacity`
RETURNED NORMALLY, so the caller believed the bypass was authorised and
audited. The caller's session was by then rolled back, and the failure
reappeared at its own commit as PendingRollbackError - a confusing secondary
error, in another function, after the real one had been swallowed.

An audit failure had become a data-loss failure mid-provision, which is the one
thing the comment on that `except` says must not happen.
"""

import uuid

import pytest

from app.models.models import AuditLogEntry, Organization, User
from app.services import plan_limits
from app.services.auth_service import hash_password


def _god(db_session, email=None):
    user = User(id=str(uuid.uuid4()), organization_id=None,
                email=email or ("god-%s@example.invalid" % uuid.uuid4().hex[:8]),
                password_hash=hash_password("TestPass123!"),
                full_name="Platform Owner", role="god_admin",
                must_change_password=False)
    db_session.add(user)
    db_session.commit()
    return user


def test_a_bypass_with_no_organization_is_recorded_not_dropped(db_session):
    god = _god(db_session)
    before = db_session.query(AuditLogEntry).count()

    plan_limits.require_capacity(
        db_session, None, plan_limits.LIMIT_USERS, adding=1,
        bypass=plan_limits.BYPASS_PLATFORM_PROVISIONING, actor=god)

    # THE CALLER'S OWN COMMIT. This is where the defect surfaced, one frame
    # away from the code that caused it.
    db_session.commit()

    rows = db_session.query(AuditLogEntry).all()
    assert len(rows) == before + 1, "the bypass left no trace"
    row = rows[-1]
    assert row.action == "plan_limit.bypass"
    assert row.organization_id is None
    # NOT NULL, so it has to say something. With no organization, the target is
    # the platform - named, rather than empty.
    assert row.target_id, "target_id must not be empty on a NOT NULL column"
    assert row.target_type == "platform"


def test_a_bypass_with_an_organization_still_targets_that_organization(db_session):
    god = _god(db_session)
    org = Organization(name="Bypass Co", slug="bypass-co-%s" % uuid.uuid4().hex[:6],
                       plan="trial", industry="generic")
    db_session.add(org)
    db_session.commit()

    plan_limits.require_capacity(
        db_session, org, plan_limits.LIMIT_USERS, adding=1,
        bypass=plan_limits.BYPASS_PLATFORM_PROVISIONING, actor=god)
    db_session.commit()

    row = db_session.query(AuditLogEntry).order_by(
        AuditLogEntry.created_at.desc()).first()
    assert row.target_type == "organization"
    assert row.target_id == org.id
    assert row.organization_id == org.id


def test_the_session_is_still_usable_after_a_bypass(db_session):
    """The regression that actually cost a provision.

    A poisoned session does not fail here; it fails at whatever the caller
    writes next, which is why this asserts on a write rather than on a return
    value.
    """
    god = _god(db_session)
    plan_limits.require_capacity(
        db_session, None, plan_limits.LIMIT_LEADS, adding=1,
        bypass=plan_limits.BYPASS_PLATFORM_PROVISIONING, actor=god)

    survivor = Organization(name="Provisioned After Bypass",
                            slug="after-bypass-%s" % uuid.uuid4().hex[:6],
                            plan="trial", industry="generic")
    db_session.add(survivor)
    db_session.commit()          # PendingRollbackError before the fix
    assert db_session.query(Organization).filter(
        Organization.id == survivor.id).one()


def test_an_unprivileged_caller_still_cannot_bypass(db_session):
    """The fix must not have widened who may bypass."""
    advisor = User(id=str(uuid.uuid4()), organization_id=None,
                   email="nobody-%s@example.invalid" % uuid.uuid4().hex[:8],
                   password_hash=hash_password("TestPass123!"),
                   full_name="Nobody", role="advisor",
                   must_change_password=False)
    db_session.add(advisor)
    db_session.commit()

    with pytest.raises(plan_limits.LimitBypassDenied):
        plan_limits.require_capacity(
            db_session, None, plan_limits.LIMIT_USERS, adding=1,
            bypass=plan_limits.BYPASS_PLATFORM_PROVISIONING, actor=advisor)


def test_an_unknown_bypass_reason_is_still_refused(db_session):
    god = _god(db_session)
    with pytest.raises(plan_limits.LimitBypassDenied):
        plan_limits.require_capacity(
            db_session, None, plan_limits.LIMIT_USERS, adding=1,
            bypass="not_a_real_reason", actor=god)
