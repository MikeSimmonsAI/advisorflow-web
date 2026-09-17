""""INVITED" MUST MEAN SOMETHING WAS ATTEMPTED.

WHAT WAS WRONG. The God console reported a customer as **Invited** and Manage
Access recorded `sales_access_link_issued`, and both records were true — but
nothing had ever contacted a mail provider. This module only ever minted a
one-time link. There was no delivery receipt, no send event and no bounce,
because there had been no send.

A reader of those two records could not distinguish:

    "we emailed them and they have not replied"
    "we made a link somebody still has to paste into a message"

Those are very different things to be in with a customer who has gone quiet,
and the console showed them identically.

THE RULE THIS FILE ENFORCES: the platform may report a send only when it
attempted one, and must report what the provider actually said — including
when the provider refused, because a failure nobody can see is worse than no
send at all. The customer is then recorded as invited and is waiting for
nothing.
"""

import uuid

import pytest

from app.models.implementation_models import Implementation
from app.models.models import Organization, Platform, User
from app.services import launch_invitation
from app.services.auth_service import hash_password


@pytest.fixture()
def world(db_session):
    platform = Platform(name="EvoSys Pro", slug="evo-" + uuid.uuid4().hex[:6],
                        support_email="support@evosyspro.live")
    db_session.add(platform)
    db_session.commit()
    org = Organization(name="Synthetic Customer LLC",
                       slug="syn-" + uuid.uuid4().hex[:8], plan="standard",
                       platform_id=platform.id, is_active=True)
    db_session.add(org)
    db_session.commit()
    impl = Implementation(organization_id=org.id, platform_id=platform.id)
    db_session.add(impl)
    db_session.commit()
    actor = User(email="operator-%s@example.com" % uuid.uuid4().hex[:6],
                 password_hash=hash_password("TestPass123!"),
                 full_name="Operator", role="god_admin", is_active=True,
                 must_change_password=False)
    db_session.add(actor)
    db_session.commit()
    return {"platform": platform, "org": org, "impl": impl, "actor": actor}


RECIPIENT = "synthetic.recipient@example.com"


def _send(db, world, *, deliver, email=RECIPIENT):
    return launch_invitation.send(
        db, world["org"], world["impl"], world["actor"],
        email=email, confirm_email=email, full_name="Synthetic Recipient",
        base_url="https://app.example.com", deliver=deliver)


# ── 1. generating a link does not claim a send ──────────────────────────────

def test_generating_a_link_reports_generated_not_sent(db_session, world):
    out = _send(db_session, world, deliver=False)
    assert out["message_sent_by_platform"] is False
    assert out["delivery"]["state"] == launch_invitation.DELIVERY_GENERATED
    assert out["delivery"]["label"] == "Link generated — not sent"
    assert out["delivery"]["provider_message_id"] is None
    assert out["onboarding_url"]


def test_the_audit_row_for_a_link_says_no_message_was_sent(db_session, world):
    from app.models.models import AuditLogEntry
    _send(db_session, world, deliver=False)
    row = (db_session.query(AuditLogEntry)
           .filter(AuditLogEntry.action == "customer_onboarding_invitation_issued")
           .first())
    assert row is not None, "the issue was not audited"
    import json
    details = json.loads(row.details) if isinstance(row.details, str) else row.details
    assert details["message_sent_by_platform"] is False
    assert details["delivery_state"] == launch_invitation.DELIVERY_GENERATED


# ── 2. asking for a send actually attempts one ──────────────────────────────

def test_asking_to_deliver_calls_the_mail_provider(db_session, world, monkeypatch):
    calls = []

    # send_email_via_provider also declares message_type, sensitivity and
    # template_id, and launch_invitation passes all three deliberately - the
    # invitation carries a one-time setup link, so the audit-copy decision is
    # stated rather than defaulted. A double that refuses them raises TypeError
    # inside the provider try/except and looks exactly like "no send was
    # attempted", which is what this test was reporting.
    def _fake(to_email, subject, body_html, attachments=None, org=None, **kwargs):
        calls.append({"to": to_email, "subject": subject, "html": body_html,
                      "org": org})
        return {"success": True, "provider_message_id": "msg_synthetic_1",
                "error": None}

    monkeypatch.setattr("app.services.email_service.send_email_via_provider", _fake)
    out = _send(db_session, world, deliver=True)

    assert len(calls) == 1, "no send was attempted"
    assert calls[0]["to"] == RECIPIENT
    assert out["message_sent_by_platform"] is True
    assert out["delivery"]["state"] == launch_invitation.DELIVERY_SENT
    assert out["delivery"]["provider_message_id"] == "msg_synthetic_1"
    assert out["delivery"]["attempted_at"] is not None


def test_the_invitation_wears_the_brands_identity(db_session, world, monkeypatch):
    """EvoSys Pro's own support address, resolved from its platform row. No
    address is typed into the invitation path, and BookaBoost would resolve
    its own from the same code."""
    calls = []
    monkeypatch.setattr(
        "app.services.email_service.send_email_via_provider",
        lambda to_email, subject, body_html, attachments=None, org=None, **kwargs:
            calls.append({"org": org, "subject": subject, "html": body_html})
            or {"success": True, "provider_message_id": "m", "error": None})
    _send(db_session, world, deliver=True)
    sender = calls[0]["org"]
    assert sender.from_email == "support@evosyspro.live"
    assert sender.reply_to_email == "support@evosyspro.live"
    # `resolved` is what stops an unconfigured brand being rescued by the
    # deployment-wide default address, which belongs to nobody.
    assert sender.resolved is True
    assert "EvoSys Pro" in calls[0]["subject"]


def test_a_provider_refusal_is_reported_not_swallowed(db_session, world, monkeypatch):
    """A FAILURE NOBODY CAN SEE IS THE WORST OUTCOME: the customer is recorded
    as invited and is waiting for a message that was never accepted."""
    monkeypatch.setattr(
        "app.services.email_service.send_email_via_provider",
        lambda *a, **k: {"success": False, "provider_message_id": None,
                         "error": "Domain not verified"})
    out = _send(db_session, world, deliver=True)
    assert out["message_sent_by_platform"] is False
    assert out["delivery"]["state"] == launch_invitation.DELIVERY_FAILED
    assert "Domain not verified" in out["delivery"]["error"]


def test_a_provider_exception_is_reported_not_raised(db_session, world, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("connection reset")
    monkeypatch.setattr("app.services.email_service.send_email_via_provider", _boom)
    out = _send(db_session, world, deliver=True)
    assert out["delivery"]["state"] == launch_invitation.DELIVERY_FAILED
    assert "connection reset" in out["delivery"]["error"]
    # The ACCESS still exists — the person was granted their role whether or
    # not the notification got through.
    assert out["recipient"]["email"] == RECIPIENT


def test_a_brand_with_no_sender_fails_with_the_reason(db_session, world):
    world["platform"].support_email = None
    db_session.commit()
    out = _send(db_session, world, deliver=True)
    assert out["delivery"]["state"] == launch_invitation.DELIVERY_FAILED
    assert "verified sending address" in out["delivery"]["error"].lower()


# ── 3. the audit record states what happened ────────────────────────────────

def test_a_real_send_is_audited_as_a_send(db_session, world, monkeypatch):
    from app.models.models import AuditLogEntry
    monkeypatch.setattr(
        "app.services.email_service.send_email_via_provider",
        lambda *a, **k: {"success": True, "provider_message_id": "m2", "error": None})
    _send(db_session, world, deliver=True)
    row = (db_session.query(AuditLogEntry)
           .filter(AuditLogEntry.action == "customer_onboarding_invitation_sent")
           .first())
    assert row is not None, "a send was not distinguishable from an issue"
    import json
    details = json.loads(row.details) if isinstance(row.details, str) else row.details
    assert details["message_sent_by_platform"] is True
    assert details["delivery_state"] == launch_invitation.DELIVERY_SENT
    assert details["provider_message_id"] == "m2"


def test_the_two_actions_are_different_audit_actions(db_session, world, monkeypatch):
    """An operator reading the log can tell the two apart, which is the whole
    point — they could not before."""
    from app.models.models import AuditLogEntry
    monkeypatch.setattr(
        "app.services.email_service.send_email_via_provider",
        lambda *a, **k: {"success": True, "provider_message_id": "m3", "error": None})
    _send(db_session, world, deliver=False)
    _send(db_session, world, deliver=True)
    actions = {r.action for r in db_session.query(AuditLogEntry).all()}
    assert "customer_onboarding_invitation_issued" in actions
    assert "customer_onboarding_invitation_sent" in actions


# ── 4. nothing is sent unless it is asked for ───────────────────────────────

def test_nothing_is_mailed_when_only_a_link_was_asked_for(db_session, world,
                                                          monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.services.email_service.send_email_via_provider",
        lambda *a, **k: calls.append(a) or {"success": True,
                                            "provider_message_id": "x",
                                            "error": None})
    _send(db_session, world, deliver=False)
    assert calls == [], "a send happened without being asked for"


def test_the_envelope_record_names_the_operator_who_sent_it(db_session, world, monkeypatch):
    """REGRESSION: this row could not be written at all.

    `email.credential_delivery` passed actor_user_id=None into log_action, and
    audit_log_entries.actor_user_id is NOT NULL with a foreign key to users -
    the only non-nullable actor on any audit or event table in this codebase,
    and deliberately so. The flush raised IntegrityError, the surrounding
    `except Exception` swallowed it, and the poisoned session then made the
    REAL audit write fail with PendingRollbackError: on Postgres, a delivered
    invitation whose transaction could not commit.

    The fix is not a sentinel user and not a relaxed column. This event was
    never system-initiated - an operator pressed send, and `send` already
    audits that same action in their name. The actor is now threaded through.
    """
    from app.models.models import AuditLogEntry
    monkeypatch.setattr(
        "app.services.email_service.send_email_via_provider",
        lambda *a, **k: {"success": True, "provider_message_id": "m3", "error": None})

    out = _send(db_session, world, deliver=True)
    assert out["delivery"]["state"] == launch_invitation.DELIVERY_SENT

    envelope = (db_session.query(AuditLogEntry)
                .filter(AuditLogEntry.action == "email.credential_delivery")
                .first())
    assert envelope is not None, "the envelope record was not written"
    assert envelope.actor_user_id, "an audit row with no actor cannot exist"
    assert envelope.actor_user_id == world["actor"].id

    # And it is the same person the send row names, because it is the same act.
    sent = (db_session.query(AuditLogEntry)
            .filter(AuditLogEntry.action == "customer_onboarding_invitation_sent")
            .first())
    assert sent.actor_user_id == envelope.actor_user_id


def test_no_audit_row_anywhere_is_written_without_an_actor(db_session, world, monkeypatch):
    """The column is NOT NULL for a reason; nothing in this flow may forge it
    either. `launch_delivery.py` passes the literal string "system", which
    satisfies SQLite and violates the foreign key on Postgres - this asserts
    that this flow does not acquire that habit."""
    from app.models.models import AuditLogEntry, User
    monkeypatch.setattr(
        "app.services.email_service.send_email_via_provider",
        lambda *a, **k: {"success": True, "provider_message_id": "m4", "error": None})
    _send(db_session, world, deliver=True)

    real_ids = {u.id for u in db_session.query(User).all()}
    for row in db_session.query(AuditLogEntry).all():
        assert row.actor_user_id in real_ids, (
            f"audit row {row.action!r} names actor {row.actor_user_id!r}, "
            f"which is not a real user - the foreign key would refuse it")
