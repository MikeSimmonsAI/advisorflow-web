"""
Phase 2 — the compliance gate, attribution, and the proof that nothing sends.

Two different things are under test here and they must not be confused:

  * THE ONE LIVE EMAIL PATH. The auto-send approval queue really does reach a
    provider. It had a compliance gate already; what it did not have was an
    `email_messages` row, so an approved email sent for real and then vanished
    from the timeline, the activity feed and the sent log while an approved
    SMS appeared normally. That asymmetry is what these tests pin shut.

  * THE FIVE DEAD PATHS. Bulk AI email, the pipeline auto-reply, the
    post-appointment thank-you and the two voice-call emails all called a
    sender that does not exist. They now run the authoritative compliance gate
    and then refuse, deliberately, because restoring the sender is Phase 3.
    The tests at the bottom exist to prove they STAY incapable of delivery.

NOTHING IN THIS FILE SENDS ANYTHING. Every provider is patched, and the
last test asserts that the gate module cannot reach one even by accident.
"""

import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.models.models import (
    BookingFollowup, BookingLink, EmailMessage, Lead, Message,
    Organization, SuppressionEntry, User,
)
from app.routers.auto_send_router import AutoSendItem
from app.services import send_source
from app.services.auth_service import create_access_token, hash_password


# ── helpers ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _background_ai_on(monkeypatch):
    """The AI-backed senders in this module (pipeline auto-reply, the
    post-appointment follow-up) are exercised as they behave WHEN they run, so
    the master background-AI switch - off by default, see
    tests/test_ai_spend_control.py - is on here. The outbound-email switches
    this module is about are untouched and still default off."""
    monkeypatch.setenv("AI_BACKGROUND_AUTOMATION_ENABLED", "true")

def _lead(db_session, org, advisor, *, phone="12145557001", email=None,
          status="new", allow_email=None, manual_flag=None):
    lead = Lead(organization_id=org.id, assigned_to_id=advisor.id if advisor else None,
                first_name="Gate", last_name="Lead", phone=phone, phone_raw=phone,
                email=email, status=status, allow_email=allow_email,
                manual_flag=manual_flag)
    db_session.add(lead)
    db_session.commit()
    return lead


def _item(db_session, org, advisor, lead, *, channel="email",
          subject="Following up", message="Just confirming Tuesday at 2pm."):
    item = AutoSendItem(
        id=str(uuid.uuid4()), organization_id=org.id, lead_id=lead.id,
        advisor_id=advisor.id, message=message, channel=channel, subject=subject,
        source="ai", status="pending", created_at=datetime.utcnow(),
    )
    db_session.add(item)
    db_session.commit()
    return item


def _ok_provider():
    return {"success": True, "provider_message_id": "em_test", "error": None}


def _headers(db_session, user):
    return {"Authorization": f"Bearer {create_access_token(user, db_session)}"}


def _twilio():
    client = MagicMock()
    client.messages.create.return_value = MagicMock(
        sid="SM_test", status="queued", error_code=None, error_message=None)
    return client


# ═══════════════════════════════════════════════════════════════════════════
# THE LIVE PATH — auto-send approvals
# ═══════════════════════════════════════════════════════════════════════════

def test_compliant_email_is_sent_and_writes_an_authoritative_history_row(
        client, db_session, sample_org, sample_advisor, auth_headers):
    """The defect this phase exists for: an approved email left no record."""
    lead = _lead(db_session, sample_org, sample_advisor, phone=None,
                 email="family@example.com")
    item = _item(db_session, sample_org, sample_advisor, lead)

    with patch("app.services.email_service.send_email_via_provider",
               return_value=_ok_provider()) as provider:
        response = client.post(f"/auto-send/{item.id}/approve", headers=auth_headers)

    assert response.status_code == 200
    provider.assert_called_once()

    rows = db_session.query(EmailMessage).filter(EmailMessage.lead_id == lead.id).all()
    assert len(rows) == 1, "an approved email must leave exactly one history row"
    row = rows[0]
    assert row.status == "sent"
    assert row.provider_message_id == "em_test"
    assert "Just confirming Tuesday at 2pm." in row.body_html
    # Attribution.
    assert row.send_source == send_source.AUTO_SEND
    assert row.sent_by_user_id == sample_advisor.id


def test_approved_sms_also_carries_attribution(
        client, db_session, sample_org, sample_advisor, auth_headers):
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145557002")
    item = _item(db_session, sample_org, sample_advisor, lead, channel="sms", subject=None)

    with patch("app.services.sms_service._resolve_twilio_creds",
               return_value=(_twilio(), "+19998887777", None)):
        response = client.post(f"/auto-send/{item.id}/approve", headers=auth_headers)

    assert response.status_code == 200
    row = db_session.query(Message).filter(Message.lead_id == lead.id).one()
    assert row.send_source == send_source.AUTO_SEND
    assert row.sent_by_user_id == sample_advisor.id


def test_dnc_blocks_the_send_and_the_provider_is_never_called(
        client, db_session, sample_org, sample_advisor, auth_headers):
    lead = _lead(db_session, sample_org, sample_advisor, phone=None,
                 email="stop@example.com", status="dnc")
    item = _item(db_session, sample_org, sample_advisor, lead)

    with patch("app.services.email_service.send_email_via_provider") as provider:
        response = client.post(f"/auto-send/{item.id}/approve", headers=auth_headers)

    provider.assert_not_called()
    assert response.json()["status"] == "failed"
    assert "DNC" in response.json()["blocked_reason"]
    # A refusal writes no history row: nothing was sent, so nothing is recorded
    # as having been sent.
    assert db_session.query(EmailMessage).filter(EmailMessage.lead_id == lead.id).count() == 0


def test_suppression_list_blocks_sms_and_calls_no_provider(
        client, db_session, sample_org, sample_advisor, auth_headers):
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145557003")
    # Stored the way the Compliance Center stores them: normalize_phone strips
    # the leading '+', so "+1214..." here would simply never match.
    db_session.add(SuppressionEntry(organization_id=sample_org.id,
                                    phone="12145557003", reason="asked to stop"))
    db_session.commit()
    item = _item(db_session, sample_org, sample_advisor, lead, channel="sms", subject=None)

    with patch("app.services.sms_service._resolve_twilio_creds") as creds:
        response = client.post(f"/auto-send/{item.id}/approve", headers=auth_headers)

    creds.assert_not_called()
    assert response.json()["status"] == "failed"
    assert db_session.query(Message).filter(Message.lead_id == lead.id).count() == 0


def test_a_suppressed_phone_does_not_block_email(
        client, db_session, sample_org, sample_advisor, auth_headers):
    """Locked in on purpose. suppression_entries is a PHONE list with no email
    column; treating it as an email prohibition would invent a cross-channel
    rule the business never made and silently stop permitted mail."""
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145557004",
                 email="reachable@example.com")
    db_session.add(SuppressionEntry(organization_id=sample_org.id,
                                    phone="12145557004", reason="texts only"))
    db_session.commit()
    item = _item(db_session, sample_org, sample_advisor, lead)

    with patch("app.services.email_service.send_email_via_provider",
               return_value=_ok_provider()) as provider:
        response = client.post(f"/auto-send/{item.id}/approve", headers=auth_headers)

    assert response.json()["status"] == "sent"
    provider.assert_called_once()


def test_an_address_flagged_unusable_is_refused(
        client, db_session, sample_org, sample_advisor, auth_headers):
    lead = _lead(db_session, sample_org, sample_advisor, phone=None,
                 email="bounces@example.com", manual_flag="bad_email")
    item = _item(db_session, sample_org, sample_advisor, lead)

    with patch("app.services.email_service.send_email_via_provider") as provider:
        response = client.post(f"/auto-send/{item.id}/approve", headers=auth_headers)

    provider.assert_not_called()
    assert response.json()["status"] == "failed"


def test_provider_failure_records_a_failure_and_never_a_success(
        client, db_session, sample_org, sample_advisor, auth_headers):
    lead = _lead(db_session, sample_org, sample_advisor, phone=None,
                 email="family@example.com")
    item = _item(db_session, sample_org, sample_advisor, lead)

    with patch("app.services.email_service.send_email_via_provider",
               return_value={"success": False, "provider_message_id": None,
                             "error": "domain not verified"}):
        response = client.post(f"/auto-send/{item.id}/approve", headers=auth_headers)

    assert response.json()["status"] == "failed"
    db_session.refresh(item)
    assert item.status == "failed"
    # The provider's own words survive to the operator. "The email provider
    # rejected the message" is not a reason; "domain not verified" is.
    assert "domain not verified" in (item.ai_reason or "")

    row = db_session.query(EmailMessage).filter(EmailMessage.lead_id == lead.id).one()
    assert row.status == "failed", "a rejected send must not be recorded as sent"
    assert row.send_source == send_source.AUTO_SEND
    db_session.refresh(lead)
    assert lead.status != "sent"


def test_auto_send_approval_is_org_isolated(
        client, db_session, sample_org, sample_advisor, auth_headers):
    other = Organization(name="Other Gate Co", slug="other-gate", plan="trial")
    db_session.add(other)
    db_session.flush()
    outsider = User(organization_id=other.id, email="out@other.com",
                    password_hash=hash_password("OtherPass123!"),
                    full_name="Outsider", role="advisor", must_change_password=False)
    db_session.add(outsider)
    db_session.commit()
    foreign_lead = _lead(db_session, other, outsider, phone=None, email="f@other.com")
    foreign_item = _item(db_session, other, outsider, foreign_lead)

    with patch("app.services.email_service.send_email_via_provider") as provider:
        response = client.post(f"/auto-send/{foreign_item.id}/approve", headers=auth_headers)

    assert response.status_code == 404
    provider.assert_not_called()
    db_session.refresh(foreign_item)
    assert foreign_item.status == "pending"


# ═══════════════════════════════════════════════════════════════════════════
# ATTRIBUTION — the actor is not the customer-facing identity
# ═══════════════════════════════════════════════════════════════════════════

def test_sent_by_user_id_is_the_actor_while_sender_id_stays_the_family_s_advisor(
        db_session, sample_org, sample_advisor, second_advisor):
    """Advisor Two owns the lead. Advisor One presses send.

    The family must still hear from Advisor Two - that is what sender_id is
    for and it does not change. sent_by_user_id is the separate fact that
    nothing in the platform recorded before: who actually did it.
    """
    from app.services.email_service import send_email_to_lead

    lead = _lead(db_session, sample_org, second_advisor, phone=None,
                 email="family@example.com")

    with patch("app.services.email_service.send_email_via_provider",
               return_value=_ok_provider()):
        row = send_email_to_lead(
            db_session, second_advisor, lead,
            subject="Hello", body_html="<p>Hello</p>",
            send_source=send_source.MANUAL,
            sent_by_user_id=sample_advisor.id,
        )

    assert row.sender_id == second_advisor.id, "customer-facing identity is unchanged"
    assert row.sent_by_user_id == sample_advisor.id, "the actor is recorded separately"
    assert row.send_source == send_source.MANUAL


def test_an_unmigrated_path_records_no_source_rather_than_guessing(
        db_session, sample_org, sample_advisor):
    """NULL means unrecorded. It must never be quietly defaulted to 'manual'."""
    from app.services.email_service import send_email_to_lead
    lead = _lead(db_session, sample_org, sample_advisor, phone=None,
                 email="family2@example.com")

    with patch("app.services.email_service.send_email_via_provider",
               return_value=_ok_provider()):
        row = send_email_to_lead(db_session, sample_advisor, lead,
                                 subject="Hi", body_html="<p>Hi</p>")

    assert row.send_source is None
    assert row.sent_by_user_id is None


def test_every_declared_source_is_known_to_the_vocabulary():
    """Guards against a router inventing a thirteenth spelling in passing."""
    assert send_source.is_valid(None)
    assert send_source.is_valid(send_source.AUTO_SEND)
    assert not send_source.is_valid("autosend")
    assert len(set(send_source.ALL_SOURCES)) == len(send_source.ALL_SOURCES)


def test_send_email_to_lead_refuses_half_a_draft(db_session, sample_org, sample_advisor):
    from app.services.email_service import send_email_to_lead
    lead = _lead(db_session, sample_org, sample_advisor, phone=None, email="x@example.com")
    with pytest.raises(ValueError, match="both subject and body_html"):
        send_email_to_lead(db_session, sample_advisor, lead, subject="only a subject")


# ═══════════════════════════════════════════════════════════════════════════
# THE FIVE DEAD PATHS — gated, and still incapable of delivery
# ═══════════════════════════════════════════════════════════════════════════

def test_the_gate_refuses_a_dnc_lead_before_anything_else(db_session, sample_org, sample_advisor):
    from app.services import outbound_email_gate
    lead = _lead(db_session, sample_org, sample_advisor, phone=None,
                 email="stop@example.com", status="dnc")
    with pytest.raises(ValueError, match="DNC"):
        outbound_email_gate.gate_lead_email(db_session, lead,
                                            send_source=send_source.BULK_AI)


def test_the_gate_passes_a_clean_lead_and_still_refuses_to_send(
        db_session, sample_org, sample_advisor):
    """The whole point of Phase 2: permitted, and still not sent."""
    from app.services import outbound_email_gate
    lead = _lead(db_session, sample_org, sample_advisor, phone=None,
                 email="fine@example.com")
    with pytest.raises(outbound_email_gate.EmailSendDisabled):
        outbound_email_gate.gate_lead_email(db_session, lead,
                                            send_source=send_source.BULK_AI)
    assert db_session.query(EmailMessage).filter(EmailMessage.lead_id == lead.id).count() == 0


def test_a_staff_alert_with_no_address_is_refused_before_the_gate(db_session):
    from app.services import outbound_email_gate
    with pytest.raises(ValueError, match="No notification address"):
        outbound_email_gate.gate_staff_email(None, purpose="voice call escalation alert")
    with pytest.raises(outbound_email_gate.EmailSendDisabled):
        outbound_email_gate.gate_staff_email("advisor@example.com",
                                             purpose="voice call escalation alert")


def test_bulk_ai_email_reports_a_block_separately_from_an_error(
        client, db_session, sample_org, sample_advisor, auth_headers):
    """Site A end to end: a refusal is counted as skipped, not as a failure,
    and the disabled sender is reported as an error rather than a green tick."""
    blocked = _lead(db_session, sample_org, sample_advisor, phone=None,
                    email="stop@example.com", status="dnc")
    allowed = _lead(db_session, sample_org, sample_advisor, phone=None,
                    email="fine@example.com")

    def fake_generate(db, lead, advisor, tone="warm", ai_direction=None,
                      relationship_type=None, actor=None):
        return {"reply": "Drafted", "subject": "Subject", "should_stop": False,
                "reason": "", "source": "ai", "error_kind": None, "booking_url": ""}

    with patch("app.routers.ai_conversation_router.generate_auto_reply", fake_generate), \
         patch("app.services.email_service.send_email_via_provider") as provider:
        response = client.post(
            "/ai-conversation/generate-batch",
            headers=auth_headers,
            json={"lead_ids": [blocked.id, allowed.id], "auto_send": True,
                  "channel": "email"},
        )

    assert response.status_code == 200
    body = response.json()
    provider.assert_not_called()
    assert body["sent"] == 0, "nothing may be reported as sent while the sender is disabled"
    assert body["skipped"] == 1, "the DNC lead is a block, not a failure"
    assert body["errors"] == 1, "the permitted lead fails loudly on the disabled sender"

    actions = {r["lead_id"]: r["action"] for r in body["results"]}
    assert actions[blocked.id] == "blocked"
    assert actions[allowed.id] == "error"


def test_no_call_site_references_the_missing_sender_any_more():
    """_send_email_via_graph is defined nowhere. Nothing may import or call it.

    Parsed, not grepped. Prose that explains why the symbol is gone - in this
    module's docstring, in the gate's, and at each repaired site - is the
    record of what happened and is meant to stay; only real imports and real
    calls are a defect.
    """
    import ast, pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    NAME = "_send_email_via_graph"
    offenders = []
    for path in root.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if any(a.name == NAME for a in node.names):
                    offenders.append(f"{path.relative_to(root.parent)}:{node.lineno} import")
            elif isinstance(node, ast.Call):
                fn = node.func
                name = getattr(fn, "id", None) or getattr(fn, "attr", None)
                if name == NAME:
                    offenders.append(f"{path.relative_to(root.parent)}:{node.lineno} call")
    assert not offenders, f"the missing sender is still imported or called at: {offenders}"


def test_the_gate_functions_themselves_cannot_reach_a_provider():
    """A structural guarantee, narrowed to where it still belongs.

    The module now also hosts the restored senders, which of course reach a
    provider - that is their job. What must remain true is that the two GATE
    functions do not: clearing a gate is a decision, not a send, and a caller
    that only gates must be unable to contact anybody by accident.

    Parsed per function rather than grepped over the file, so adding a sender
    beside them cannot quietly weaken it.
    """
    import ast
    import pathlib
    tree = ast.parse(pathlib.Path("app/services/outbound_email_gate.py")
                     .read_text(encoding="utf-8"))
    forbidden = {"send_email_via_provider", "send_email_to_lead",
                 "_send_email_resend", "send_email", "Emails"}
    for name in ("gate_lead_email", "gate_staff_email"):
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == name), None)
        assert fn is not None, f"{name} not found"
        called = {getattr(c.func, "id", None) or getattr(c.func, "attr", None)
                  for c in ast.walk(fn) if isinstance(c, ast.Call)}
        imported = {a.name for n in ast.walk(fn)
                    if isinstance(n, ast.ImportFrom) for a in n.names}
        leak = (called | imported) & forbidden
        assert not leak, f"{name} can reach a provider via {sorted(leak)}"


# ═══════════════════════════════════════════════════════════════════════════
# BACKLOG REPLAY — the only timer-driven path over persisted rows
# ═══════════════════════════════════════════════════════════════════════════

def test_post_appointment_sweep_cannot_replay_historical_appointments(
        db_session, sample_org, sample_advisor):
    """Two independent guards, asserted independently.

    A booking older than the three-hour window is structurally unselectable,
    and a booking that already has a BookingFollowup row is excluded whatever
    its age - and that row is written even when the send fails, so every past
    failed attempt already holds its own permanent exclusion.
    """
    from app.services.post_appointment_service import check_and_send_followups
    now = datetime.utcnow()

    stale_lead = _lead(db_session, sample_org, sample_advisor, phone=None,
                       email="stale@example.com")
    stale = BookingLink(lead_id=stale_lead.id, user_id=sample_advisor.id,
                        status="booked", booked_time=now - timedelta(hours=9))

    tried_lead = _lead(db_session, sample_org, sample_advisor, phone=None,
                       email="tried@example.com")
    tried = BookingLink(lead_id=tried_lead.id, user_id=sample_advisor.id,
                        status="booked", booked_time=now - timedelta(minutes=30))
    db_session.add_all([stale, tried])
    db_session.flush()
    # The permanent exclusion a previous failed attempt already left behind.
    db_session.add(BookingFollowup(booking_link_id=tried.id, lead_id=tried_lead.id,
                                   advisor_id=sample_advisor.id,
                                   channel="email", thank_you_sent=False,
                                   error="No reachable channel or send failed"))
    db_session.commit()

    before = db_session.query(BookingFollowup).count()
    with patch("app.services.email_service.send_email_via_provider") as provider:
        sent = check_and_send_followups(db_session)

    provider.assert_not_called()
    assert sent == 0
    assert db_session.query(BookingFollowup).count() == before, \
        "no historical booking may be picked up by the sweep"


def test_a_genuinely_new_appointment_is_picked_up_but_still_not_emailed(
        db_session, sample_org, sample_advisor):
    """The other half of the same guarantee: the sweep is not simply broken.

    A new in-window appointment IS selected - and the gate then refuses, so it
    is recorded as not sent rather than delivered.
    """
    from app.services.post_appointment_service import check_and_send_followups
    lead = _lead(db_session, sample_org, sample_advisor, phone=None,
                 email="fresh@example.com")
    booking = BookingLink(lead_id=lead.id, user_id=sample_advisor.id,
                          status="booked",
                          booked_time=datetime.utcnow() - timedelta(minutes=10))
    db_session.add(booking)
    db_session.commit()

    with patch("app.services.email_service.send_email_via_provider") as provider:
        check_and_send_followups(db_session)

    provider.assert_not_called()
    followup = db_session.query(BookingFollowup).filter(
        BookingFollowup.booking_link_id == booking.id).one()
    assert followup.thank_you_sent is False
    assert followup.error
