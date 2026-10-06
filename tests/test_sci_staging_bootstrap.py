"""SCI staging manager bootstrap: idempotence, fail-closed gates, no-resend,
activation preserves role/membership. AUTHORED, NOT RUN in the relay sandbox
(no pytest/SQLAlchemy there); the stdlib harness scripts/sci_staging_access_harness.py
covers the same wiring statically. No email leaves the process: the mailer is patched."""
from unittest.mock import patch

import pytest

from app.models.models import AuditLogEntry, Organization, User
from app.models.sales_models import Membership
from app.models.staff_models import StaffActivation
from app.services import sci_staging_bootstrap as b
from app.services import staff_activation
from app.services.workspace_access import SCOPE_CUSTOMER_ORG

EMAIL = "manager.synthetic@example.test"


@pytest.fixture()
def sci_org(db_session):
    o = Organization(name=b.SCI_ORG_NAME, slug="sci-synth", plan="standard", is_active=True)
    db_session.add(o)
    db_session.commit()
    return o


@pytest.fixture()
def env(monkeypatch, sci_org):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("DATABASE_URL", "postgresql://sci_staging:x@host.internal/sci_staging")
    monkeypatch.setenv(b.ENV_ENABLED, "true")
    monkeypatch.setenv(b.ENV_EMAIL, EMAIL)
    monkeypatch.setenv(b.ENV_NAME, "Synthetic Manager")
    monkeypatch.setenv(b.ENV_ORG, sci_org.id)
    monkeypatch.setenv(b.ENV_SEND, "true")
    return monkeypatch


def _ok_send():
    return patch("app.services.email_service.send_email_via_provider",
                 return_value={"success": True, "provider_message_id": "pm_1", "error": None})


def test_inert_without_flag(db_session, env):
    env.setenv(b.ENV_ENABLED, "false")
    assert b.run(db_session)["status"] == "inert"
    assert db_session.query(User).filter(User.email == EMAIL).count() == 0


@pytest.mark.parametrize("name,val,reason", [
    ("APP_ENV", "production", "not_staging_environment"),
    ("APP_ENV", "", "not_staging_environment"),
    ("DATABASE_URL", "postgresql://u:x@advisorflow-db.internal/advisorflow", "database_not_sci_staging"),
    (b.ENV_EMAIL, " ", "email_blank_or_invalid"),
    (b.ENV_ORG, "no-such-org", "org_not_found"),
])
def test_fails_closed(db_session, env, name, val, reason):
    env.setenv(name, val)
    with _ok_send() as m:
        out = b.run(db_session)
    assert out == {"status": "refused", "reason": reason}
    assert m.call_count == 0
    assert db_session.query(User).filter(User.email == EMAIL).count() == 0


def test_org_name_mismatch_refused(db_session, env, sci_org):
    sci_org.name = "Someone Else"
    db_session.commit()
    assert b.run(db_session)["reason"] == "org_name_mismatch"


def test_creates_once_with_one_manager_membership_and_sends_once(db_session, env, sci_org):
    with _ok_send() as m:
        first = b.run(db_session)
        second = b.run(db_session)          # restart
    assert first["user"] == "created" and second["user"] == "reused"
    users = db_session.query(User).filter(User.email == EMAIL).all()
    assert len(users) == 1
    u = users[0]
    assert (u.role, u.organization_id, u.is_active) == ("advisor", None, True)
    mem = db_session.query(Membership).filter(Membership.user_id == u.id).all()
    assert len(mem) == 1 and mem[0].scope_type == SCOPE_CUSTOMER_ORG
    assert mem[0].scope_id == sci_org.id and mem[0].role == "manager" and mem[0].is_active
    assert m.call_count == 1                                   # no restart spam
    assert second["activation"]["action"] == "skipped_already_sent"
    marker = db_session.query(AuditLogEntry).filter(AuditLogEntry.action == b.SENT_ACTION).all()
    assert len(marker) == 1
    sent_body = m.call_args.kwargs["body_html"]
    assert "/activate?token=stf_" in sent_body
    raw = sent_body.split("token=")[1].split("<")[0].split("&")[0].strip()
    assert raw not in str(vars(marker[0]))                     # full token never persisted


def test_failed_send_leaves_no_live_link_and_retries_once(db_session, env):
    with patch("app.services.email_service.send_email_via_provider",
               return_value={"success": False, "error": "boom"}):
        out = b.run(db_session)
    assert out["activation"]["sent"] is False
    u = db_session.query(User).filter(User.email == EMAIL).one()
    assert db_session.query(StaffActivation).filter(
        StaffActivation.user_id == u.id, StaffActivation.status == "pending").count() == 0
    with _ok_send() as m:
        b.run(db_session)
        b.run(db_session)
    assert m.call_count == 1


def test_pending_unmarked_link_is_revoked_and_replaced_by_exactly_one(db_session, env):
    with patch("app.services.email_service.send_email_via_provider", return_value={"success": False}):
        b.run(db_session)
    u = db_session.query(User).filter(User.email == EMAIL).one()
    old, _raw = staff_activation.issue(db_session, u, u)       # pending, raw unrecoverable
    with _ok_send():
        b.run(db_session)
    db_session.refresh(old)
    assert old.status == "revoked"
    assert db_session.query(StaffActivation).filter(
        StaffActivation.user_id == u.id, StaffActivation.status == "pending").count() == 1


def test_accepted_access_never_resent_and_activation_preserves_role_and_membership(db_session, env, sci_org):
    with _ok_send():
        b.run(db_session)
    u = db_session.query(User).filter(User.email == EMAIL).one()
    row = db_session.query(StaffActivation).filter(
        StaffActivation.user_id == u.id, StaffActivation.status == "pending").one()
    row_id = row.id
    # redeem through the real service with a freshly issued link (the original raw is unrecoverable)
    new_row, raw = staff_activation.issue(db_session, u, u)
    staff_activation.accept(db_session, raw, "A-Chosen-Password-1")
    db_session.refresh(u)
    assert (u.role, u.organization_id) == ("advisor", None)
    mem = db_session.query(Membership).filter(Membership.user_id == u.id).all()
    assert len(mem) == 1 and mem[0].role == "manager" and mem[0].scope_id == sci_org.id
    with _ok_send() as m:
        out = b.run(db_session)
    assert m.call_count == 0 and out["activation"]["action"] == "skipped_accepted"
    assert row_id


def test_existing_staging_user_is_reused_not_duplicated(db_session, env):
    db_session.add(User(email=EMAIL.upper(), full_name="Pre Existing", password_hash="x",
                        role="advisor", is_active=True))
    db_session.commit()
    with _ok_send():
        out = b.run(db_session)
    assert out["user"] == "reused"
    assert db_session.query(User).filter(User.email.ilike(EMAIL)).count() == 1
