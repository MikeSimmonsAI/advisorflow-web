"""
Phase 2.5 — one switch per repaired path, and no success state without a send.

TWO GUARANTEES ARE UNDER TEST.

The first is that turning one dead email path back on cannot turn another on
with it. Each gated source carries its own environment variable, all of them
default to off, and an unknown source resolves to off rather than to "not
restricted". Nothing here enables anything permanently: every test that flips
a switch uses monkeypatch, so the process ends with all five off.

The second is transaction ordering. Two fields were written BEFORE the send
they described - voice_calls.booking_url_sent and the pipeline conversation's
ai_responses_sent / last_outbound_at - so the database has been recording
outbound email that never left. Success state is now written only after a send
actually succeeds, and the tests below assert both halves: it does not move on
failure, and it does move on success.

NO PROVIDER IS REACHED ANYWHERE IN THIS FILE.
"""

import ast
import inspect
import pathlib
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.models.models import (
    EmailMessage, Lead, LeadStatus, Message, Organization,
    PipelineConversation, Reply, User,
)
from app.routers.auto_send_router import AutoSendItem
from app.services import outbound_email_gate as gate
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

def _lead(db_session, org, advisor, *, phone="12145556001", email="fam@example.com",
          status="new"):
    lead = Lead(organization_id=org.id, assigned_to_id=advisor.id,
                first_name="Switch", last_name="Lead", phone=phone,
                phone_raw=phone, email=email, status=status)
    db_session.add(lead)
    db_session.commit()
    return lead


def _enable(monkeypatch, *sources):
    """Turn on exactly the named sources for one test, and nothing else."""
    for source in gate.GATED_SOURCES:
        monkeypatch.delenv(gate._ENV_BY_SOURCE[source], raising=False)
    for source in sources:
        monkeypatch.setenv(gate._ENV_BY_SOURCE[source], "true")


# ═══════════════════════════════════════════════════════════════════════════
# THE SWITCHES
# ═══════════════════════════════════════════════════════════════════════════

def test_every_gated_source_is_disabled_by_default(monkeypatch):
    """The statement this build makes: no repaired email path can send."""
    _enable(monkeypatch)
    assert gate.enablement_report() == {
        "bulk_ai": False,
        "voice_booking_link": False,
        "pipeline_auto_reply": False,
        "appointment_followup": False,
        "staff_escalation": False,
        # The public Discovery / Demo booking paths. Three sources rather than
        # one because they are three decisions - a brand may want its sales
        # team notified about website bookings without a single prospect being
        # emailed, and may want confirmations live while it decides whether
        # automated reminders are wanted at all.
        "public_booking_confirmation": False,
        "public_booking_internal": False,
        "public_booking_reminders": False,
    }


def test_enabling_one_source_moves_nothing_else(monkeypatch):
    """The whole point of the phase. Four customer-facing paths and one staff
    path, and each is a separate decision."""
    for target in gate.GATED_SOURCES:
        _enable(monkeypatch, target)
        report = gate.enablement_report()
        assert report[target] is True, f"{target} should be on"
        others = {s: v for s, v in report.items() if s != target}
        assert not any(others.values()), (
            f"enabling {target} also enabled {[s for s, v in others.items() if v]}")


def test_an_unknown_source_fails_closed(monkeypatch):
    _enable(monkeypatch, *gate.GATED_SOURCES)  # everything the gate knows is ON
    assert gate.source_enabled("nonsense") is False
    assert gate.source_enabled(None) is False
    assert gate.source_enabled("") is False


def test_auto_send_is_not_governed_by_this_gate(monkeypatch):
    """auto_send is already live and does not come through here. It must not
    be possible to switch a working feature off by forgetting a variable."""
    assert send_source.AUTO_SEND not in gate.GATED_SOURCES
    _enable(monkeypatch, *gate.GATED_SOURCES)
    assert gate.source_enabled(send_source.AUTO_SEND) is False


def test_there_is_no_variable_that_enables_more_than_one_source():
    """A master switch would defeat the whole mechanism, so assert the table
    is one-to-one rather than trusting it to stay that way."""
    names = list(gate._ENV_BY_SOURCE.values())
    assert len(names) == len(set(names)), "two sources share one variable"
    assert len(names) == len(gate.GATED_SOURCES)


def test_a_disabled_source_names_its_own_switch_in_the_refusal(
        db_session, sample_org, sample_advisor, monkeypatch):
    _enable(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor, phone=None)
    with pytest.raises(gate.EmailSendDisabled) as caught:
        gate.gate_lead_email(db_session, lead, send_source=send_source.BULK_AI)
    assert "OUTBOUND_EMAIL_BULK_AI" in str(caught.value)


def test_a_disabled_source_still_never_reaches_a_provider(
        db_session, sample_org, sample_advisor, monkeypatch):
    _enable(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor, phone=None)
    with patch("app.services.email_service.send_email_via_provider") as provider:
        with pytest.raises(gate.EmailSendDisabled):
            gate.gate_lead_email(db_session, lead,
                                 send_source=send_source.APPOINTMENT_FOLLOWUP)
    provider.assert_not_called()


def test_compliance_is_checked_before_the_switch_is_consulted(
        db_session, sample_org, sample_advisor, monkeypatch):
    """A DNC family must be refused on compliance grounds whether the source is
    on or off - the reason an operator sees has to be the real one."""
    lead = _lead(db_session, sample_org, sample_advisor, phone=None, status="dnc")
    for enabled in (False, True):
        _enable(monkeypatch, *( (send_source.BULK_AI,) if enabled else () ))
        with patch("app.services.email_service.send_email_via_provider") as provider:
            with pytest.raises(ValueError, match="DNC"):
                gate.gate_lead_email(db_session, lead, send_source=send_source.BULK_AI)
        provider.assert_not_called()


def test_the_gate_clears_only_when_both_switches_are_on(
        db_session, sample_org, sample_advisor, monkeypatch):
    """The gate is now the only thing between the caller and the provider, so
    what it does when everything passes matters as much as when it refuses: it
    returns, and the caller sends. Merely clearing the gate contacts nobody -
    `gate_lead_email` resolves no provider and sends nothing itself."""
    import json as _json
    _enable(monkeypatch, send_source.BULK_AI)
    sample_org.outbound_email_sources = _json.dumps([send_source.BULK_AI])
    db_session.commit()
    lead = _lead(db_session, sample_org, sample_advisor, phone=None)
    with patch("app.services.email_service.send_email_via_provider") as provider:
        assert gate.gate_lead_email(
            db_session, lead, send_source=send_source.BULK_AI) is None
    provider.assert_not_called()


def test_the_staff_alert_has_its_own_switch(monkeypatch):
    _enable(monkeypatch, send_source.BULK_AI, send_source.VOICE_BOOKING_LINK)
    assert gate.source_enabled(gate.STAFF_ESCALATION) is False
    with pytest.raises(gate.EmailSendDisabled) as caught:
        gate.gate_staff_email("advisor@example.com", purpose="voice call escalation")
    assert "OUTBOUND_EMAIL_STAFF_ESCALATION" in str(caught.value)


def test_a_missing_staff_address_is_refused_before_the_switch(monkeypatch):
    _enable(monkeypatch, gate.STAFF_ESCALATION)
    with pytest.raises(ValueError, match="No notification address"):
        gate.gate_staff_email(None, purpose="voice call escalation")


def test_bulk_ai_email_stays_refused_end_to_end_with_the_source_off(
        client, auth_headers, db_session, sample_org, sample_advisor, monkeypatch):
    _enable(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor, phone=None)

    def fake_generate(db, lead_arg, advisor, tone="warm", ai_direction=None,
                      relationship_type=None, actor=None):
        return {"reply": "Drafted", "subject": "S", "should_stop": False,
                "reason": "", "source": "ai", "error_kind": None, "booking_url": ""}

    with patch("app.routers.ai_conversation_router.generate_auto_reply", fake_generate), \
         patch("app.services.email_service.send_email_via_provider") as provider:
        response = client.post("/ai-conversation/generate-batch", headers=auth_headers,
                               json={"lead_ids": [lead.id], "auto_send": True,
                                     "channel": "email"})

    provider.assert_not_called()
    body = response.json()
    assert body["sent"] == 0
    assert body["errors"] == 1
    assert "OUTBOUND_EMAIL_BULK_AI" in body["results"][0]["reason"]


# ═══════════════════════════════════════════════════════════════════════════
# NO SUCCESS STATE WITHOUT A SEND — pipeline
# ═══════════════════════════════════════════════════════════════════════════

def _analysis(**over):
    base = {"reply": "Happy to help — does Tuesday work?", "confidence": 99,
            "should_stop": False, "stop_reason": "", "intent": "interested",
            "stage": "ai_responding", "include_booking_link": False}
    base.update(over)
    return base


def _reply(db_session, lead):
    row = Reply(lead_id=lead.id, body="Yes please")
    db_session.add(row)
    db_session.commit()
    return row


def _pipeline(db_session, org, lead, advisor):
    row = PipelineConversation(organization_id=org.id, lead_id=lead.id,
                               advisor_id=advisor.id, stage="replied",
                               auto_respond=True, confidence_threshold=50)
    db_session.add(row)
    db_session.commit()
    return row


def test_a_failed_pipeline_reply_advances_no_counter_and_no_timestamp(
        db_session, sample_org, sample_advisor, monkeypatch):
    """Both channels fail. Nothing may look like an AI response."""
    from app.services import pipeline_service
    _enable(monkeypatch)  # email source off, so the gate refuses
    lead = _lead(db_session, sample_org, sample_advisor, phone=None,
                 email="fam@example.com")
    pipeline = _pipeline(db_session, sample_org, lead, sample_advisor)
    reply = _reply(db_session, lead)

    with patch.object(pipeline_service, "analyze_and_respond",
                      return_value=_analysis()), \
         patch("app.services.email_service.send_email_via_provider") as provider:
        result = pipeline_service.process_inbound_reply(
            db_session, lead, sample_advisor, reply)

    provider.assert_not_called()
    assert result["action"] == "error"
    db_session.refresh(pipeline)
    assert (pipeline.ai_responses_sent or 0) == 0, "no AI response was sent"
    assert pipeline.last_outbound_at is None, "nothing went out"
    assert (pipeline.messages_sent or 0) == 0
    assert pipeline.stage != "ai_responding", "the conversation is not responding"


def test_a_successful_pipeline_reply_advances_the_counters_exactly_once(
        db_session, sample_org, sample_advisor, monkeypatch):
    """The other half: the fix must not simply stop counting."""
    from app.services import pipeline_service
    _enable(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145556010")
    pipeline = _pipeline(db_session, sample_org, lead, sample_advisor)
    reply = _reply(db_session, lead)

    twilio = MagicMock()
    twilio.messages.create.return_value = MagicMock(
        sid="SM_x", status="queued", error_code=None, error_message=None)

    with patch.object(pipeline_service, "analyze_and_respond",
                      return_value=_analysis()), \
         patch("app.services.sms_service._resolve_twilio_creds",
               return_value=(twilio, "+19998887777", None)):
        result = pipeline_service.process_inbound_reply(
            db_session, lead, sample_advisor, reply)

    assert result["action"] == "auto_sent"
    assert result["channel"] == "sms"
    db_session.refresh(pipeline)
    assert pipeline.ai_responses_sent == 1
    assert pipeline.messages_sent == 1
    assert pipeline.last_outbound_at is not None
    assert pipeline.stage == "ai_responding"


def test_a_compliance_block_advances_no_pipeline_counter(
        db_session, sample_org, sample_advisor, monkeypatch):
    from app.services import pipeline_service
    _enable(monkeypatch, send_source.PIPELINE_AUTO_REPLY)  # source ON
    lead = _lead(db_session, sample_org, sample_advisor, phone=None,
                 email="stop@example.com", status="dnc")
    pipeline = _pipeline(db_session, sample_org, lead, sample_advisor)
    reply = _reply(db_session, lead)

    with patch.object(pipeline_service, "analyze_and_respond",
                      return_value=_analysis()), \
         patch("app.services.email_service.send_email_via_provider") as provider:
        pipeline_service.process_inbound_reply(db_session, lead, sample_advisor, reply)

    provider.assert_not_called()
    db_session.refresh(pipeline)
    assert (pipeline.ai_responses_sent or 0) == 0
    assert pipeline.last_outbound_at is None


# ═══════════════════════════════════════════════════════════════════════════
# NO SUCCESS STATE WITHOUT A SEND — voice booking link
# ═══════════════════════════════════════════════════════════════════════════

def _source_of(func_name, path):
    """The AST of one nested function, by name, from a module file."""
    tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8", errors="replace"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return node
    raise AssertionError(f"{func_name} not found in {path}")


def test_booking_url_sent_is_only_written_after_the_gate():
    """on_booking_detected is a closure bound to a live media stream, so this
    is asserted structurally rather than by driving a websocket.

    The defect was ordering: booking_url_sent = True sat ABOVE the send, in its
    own committed block, so it stayed True through every failure. The guarantee
    is that the only assignment to it now happens after gate_lead_email in the
    same try, which is unreachable unless a send succeeds.
    """
    fn = _source_of("on_booking_detected", "app/routers/voice_router.py")

    gate_lines = [n.lineno for n in ast.walk(fn)
                  if isinstance(n, ast.Call)
                  and getattr(n.func, "attr", None) in ("gate_lead_email",
                                                        "send_lead_email")]
    assign_lines = [n.lineno for n in ast.walk(fn)
                    if isinstance(n, ast.Assign)
                    for t in n.targets
                    if isinstance(t, ast.Attribute) and t.attr == "booking_url_sent"]

    assert len(gate_lines) == 1, "expected exactly one gated send"
    assert assign_lines, "booking_url_sent is never set - the flag would never be true"
    assert min(assign_lines) > gate_lines[0], (
        "booking_url_sent is assigned before the gated send; a refused or "
        "failed send would again be recorded as a sent booking link")


def test_the_voice_booking_email_is_refused_while_its_source_is_off(
        db_session, sample_org, sample_advisor, monkeypatch):
    """And because the gate refuses, the assignment above is unreachable."""
    _enable(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor)
    with patch("app.services.email_service.send_email_via_provider") as provider:
        with pytest.raises(gate.EmailSendDisabled):
            gate.gate_lead_email(db_session, lead,
                                 send_source=send_source.VOICE_BOOKING_LINK)
    provider.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# THE LIVE PATH IS UNTOUCHED
# ═══════════════════════════════════════════════════════════════════════════

def test_auto_send_still_sends_and_still_records_attribution(
        client, auth_headers, db_session, sample_org, sample_advisor, monkeypatch):
    """Every switch off, and the one genuinely live path still works."""
    _enable(monkeypatch)
    import uuid as _uuid
    lead = _lead(db_session, sample_org, sample_advisor, phone=None,
                 email="family@example.com")
    item = AutoSendItem(id=str(_uuid.uuid4()), organization_id=sample_org.id,
                        lead_id=lead.id, advisor_id=sample_advisor.id,
                        message="Confirming Tuesday.", channel="email",
                        subject="Following up", source="ai", status="pending",
                        created_at=datetime.utcnow())
    db_session.add(item)
    db_session.commit()

    with patch("app.services.email_service.send_email_via_provider",
               return_value={"success": True, "provider_message_id": "em_1",
                             "error": None}) as provider:
        response = client.post(f"/auto-send/{item.id}/approve", headers=auth_headers)

    assert response.json()["status"] == "sent"
    provider.assert_called_once()
    row = db_session.query(EmailMessage).filter(EmailMessage.lead_id == lead.id).one()
    assert row.send_source == send_source.AUTO_SEND
    assert row.sent_by_user_id == sample_advisor.id


def test_switch_state_does_not_leak_across_orgs(
        client, auth_headers, db_session, sample_org, sample_advisor, monkeypatch):
    """The switches are deployment-wide by design, not per tenant - so the
    thing to prove is that org isolation on the gated path is unchanged."""
    _enable(monkeypatch, send_source.BULK_AI)
    other = Organization(name="Other Switch Co", slug="other-switch", plan="trial")
    db_session.add(other)
    db_session.flush()
    outsider = User(organization_id=other.id, email="o@other.com",
                    password_hash=hash_password("OtherPass123!"),
                    full_name="Outsider", role="advisor", must_change_password=False)
    db_session.add(outsider)
    db_session.commit()
    foreign = _lead(db_session, other, outsider, phone=None, email="f@other.com")

    def fake_generate(db, lead_arg, advisor, tone="warm", ai_direction=None,
                      relationship_type=None, actor=None):
        return {"reply": "Drafted", "subject": "S", "should_stop": False,
                "reason": "", "source": "ai", "error_kind": None, "booking_url": ""}

    with patch("app.routers.ai_conversation_router.generate_auto_reply", fake_generate), \
         patch("app.services.email_service.send_email_via_provider") as provider:
        response = client.post("/ai-conversation/generate-batch", headers=auth_headers,
                               json={"lead_ids": [foreign.id], "auto_send": True,
                                     "channel": "email"})

    provider.assert_not_called()
    assert response.json()["total"] == 0, "a foreign lead is not even loaded"


def test_no_test_in_this_file_left_a_source_enabled():
    """monkeypatch unwinds per test; this asserts the process ends clean."""
    assert not any(gate.enablement_report().values())
