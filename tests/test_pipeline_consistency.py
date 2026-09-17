"""
The diagnostic for conversations that recorded sends which never happened.

It is read-only by construction and the first test asserts that structurally,
because the whole value of a diagnostic run against a customer's live data is
that it cannot change it.
"""

import ast
import pathlib
from datetime import datetime, timedelta

import pytest

from app.models.models import (
    EmailMessage, Lead, Message, Organization, PipelineConversation, User,
)
from app.services import pipeline_consistency as pc
from app.services.auth_service import create_access_token, hash_password


def _lead(db_session, org, advisor, phone="12145554001", email=None):
    lead = Lead(organization_id=org.id, assigned_to_id=advisor.id,
                first_name="Pipe", last_name="Line", phone=phone,
                phone_raw=phone, email=email, status="replied")
    db_session.add(lead)
    db_session.commit()
    return lead


def _conv(db_session, org, lead, advisor, **kw):
    row = PipelineConversation(
        organization_id=org.id, lead_id=lead.id, advisor_id=advisor.id,
        stage=kw.pop("stage", "replied"),
        ai_responses_sent=kw.pop("ai_responses_sent", 0),
        messages_sent=kw.pop("messages_sent", 0),
        last_outbound_at=kw.pop("last_outbound_at", None),
        last_inbound_at=kw.pop("last_inbound_at", None),
        **kw)
    db_session.add(row)
    db_session.commit()
    return row


def _sms(db_session, lead, advisor, when=None):
    row = Message(lead_id=lead.id, sender_id=advisor.id, body="hi",
                  twilio_status="sent", sent_at=when or datetime.utcnow())
    db_session.add(row)
    db_session.commit()
    return row


def test_the_diagnostic_cannot_write_anything():
    """Parsed, not trusted. A diagnostic that can write is not a diagnostic."""
    tree = ast.parse(pathlib.Path("app/services/pipeline_consistency.py")
                     .read_text(encoding="utf-8"))
    writes = {"add", "add_all", "commit", "delete", "flush", "merge",
              "execute", "update", "bulk_save_objects"}
    offenders = [
        f"line {n.lineno}: .{n.func.attr}()"
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr in writes
    ]
    assert not offenders, offenders


def test_the_defects_arithmetic_fingerprint_is_definitely_inconsistent(
        db_session, sample_org, sample_advisor):
    """ai_responses_sent was advanced before the send; messages_sent after it.
    The difference is attempts that were counted and never left."""
    lead = _lead(db_session, sample_org, sample_advisor)
    conv = _conv(db_session, sample_org, lead, sample_advisor,
                 ai_responses_sent=3, messages_sent=1,
                 last_outbound_at=datetime.utcnow())
    _sms(db_session, lead, sample_advisor)

    result = pc.classify(db_session, conv)
    assert result["verdict"] == pc.DEFINITELY_INCONSISTENT
    assert "exceeds messages_sent" in result["reasons"][0]


def test_an_outbound_timestamp_with_no_history_at_all_is_definitely_inconsistent(
        db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor)
    conv = _conv(db_session, sample_org, lead, sample_advisor,
                 ai_responses_sent=0, messages_sent=0,
                 last_outbound_at=datetime.utcnow())
    result = pc.classify(db_session, conv)
    assert result["verdict"] == pc.DEFINITELY_INCONSISTENT
    assert any("no message or email row" in r for r in result["reasons"])


def test_more_claimed_sends_than_rows_is_only_suspicious(
        db_session, sample_org, sample_advisor):
    """A merge or a deleted row explains this honestly, so it is flagged for a
    human rather than asserted."""
    lead = _lead(db_session, sample_org, sample_advisor)
    conv = _conv(db_session, sample_org, lead, sample_advisor,
                 ai_responses_sent=2, messages_sent=2,
                 last_outbound_at=datetime.utcnow())
    _sms(db_session, lead, sample_advisor)
    result = pc.classify(db_session, conv)
    assert result["verdict"] == pc.SUSPICIOUS


def test_parked_mid_reply_with_nothing_outbound_since_is_suspicious(
        db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor)
    now = datetime.utcnow()
    _sms(db_session, lead, sample_advisor, when=now - timedelta(hours=3))
    conv = _conv(db_session, sample_org, lead, sample_advisor,
                 stage="ai_responding", ai_responses_sent=1, messages_sent=1,
                 last_inbound_at=now - timedelta(minutes=10))
    result = pc.classify(db_session, conv)
    assert result["verdict"] == pc.SUSPICIOUS
    assert any("nothing has gone out" in r for r in result["reasons"])


def test_a_healthy_conversation_is_valid(db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor)
    _sms(db_session, lead, sample_advisor)
    _sms(db_session, lead, sample_advisor)
    conv = _conv(db_session, sample_org, lead, sample_advisor,
                 stage="replied", ai_responses_sent=2, messages_sent=2,
                 last_outbound_at=datetime.utcnow())
    result = pc.classify(db_session, conv)
    assert result["verdict"] == pc.VALID
    assert result["reasons"] == []


def test_the_scan_counts_and_totals_the_overstatement(
        db_session, sample_org, sample_advisor):
    for i in range(3):
        lead = _lead(db_session, sample_org, sample_advisor, phone=f"121455540{i}0")
        _conv(db_session, sample_org, lead, sample_advisor,
              ai_responses_sent=2 + i, messages_sent=0)
    out = pc.scan(db_session, organization_id=sample_org.id)
    assert out["counts"][pc.DEFINITELY_INCONSISTENT] == 3
    assert out["overstated_ai_responses"] == 2 + 3 + 4
    assert out["cleanup_plan"]["executed"] is False


def test_the_scan_is_org_scoped(db_session, sample_org, sample_advisor):
    other = Organization(name="Other Pipe Co", slug="other-pipe", plan="trial")
    db_session.add(other)
    db_session.flush()
    outsider = User(organization_id=other.id, email="p@other.test",
                    password_hash=hash_password("PPass123!"), full_name="P",
                    role="advisor", must_change_password=False)
    db_session.add(outsider)
    db_session.commit()
    foreign_lead = _lead(db_session, other, outsider, phone="12145554999")
    _conv(db_session, other, foreign_lead, outsider,
          ai_responses_sent=9, messages_sent=0)

    mine = _lead(db_session, sample_org, sample_advisor)
    _conv(db_session, sample_org, mine, sample_advisor,
          ai_responses_sent=1, messages_sent=0)

    out = pc.scan(db_session, organization_id=sample_org.id)
    assert out["scanned"] == 1
    assert out["results"][0]["lead_id"] == mine.id


def test_a_scan_changes_nothing(db_session, sample_org, sample_advisor):
    """Belt and braces on top of the AST check: run it and compare."""
    lead = _lead(db_session, sample_org, sample_advisor)
    conv = _conv(db_session, sample_org, lead, sample_advisor,
                 ai_responses_sent=5, messages_sent=1,
                 last_outbound_at=datetime.utcnow())
    before = (conv.ai_responses_sent, conv.messages_sent, conv.stage,
              conv.last_outbound_at)
    pc.scan(db_session, organization_id=sample_org.id)
    db_session.expire_all()
    conv = db_session.query(PipelineConversation).filter(
        PipelineConversation.id == conv.id).one()
    assert (conv.ai_responses_sent, conv.messages_sent, conv.stage,
            conv.last_outbound_at) == before


def test_the_endpoint_is_god_only(client, admin_auth_headers, db_session):
    response = client.get("/god/maintenance/pipeline-consistency",
                          headers=admin_auth_headers)
    assert response.status_code in (401, 403)


def test_the_endpoint_returns_the_plan_without_executing_it(
        client, db_session, sample_org, sample_advisor):
    god = User(organization_id=None, email="god2@platform.test",
               password_hash=hash_password("GodPass123!"), full_name="God",
               role="god_admin", must_change_password=False)
    db_session.add(god)
    db_session.commit()
    headers = {"Authorization": f"Bearer {create_access_token(god, db_session)}"}

    lead = _lead(db_session, sample_org, sample_advisor)
    _conv(db_session, sample_org, lead, sample_advisor,
          ai_responses_sent=4, messages_sent=0)

    response = client.get(
        f"/god/maintenance/pipeline-consistency?organization_id={sample_org.id}",
        headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["counts"]["definitely_inconsistent"] == 1
    assert body["overstated_ai_responses"] == 4
    assert body["cleanup_plan"]["executed"] is False


def test_an_unknown_verdict_filter_is_refused(client, db_session):
    god = User(organization_id=None, email="god3@platform.test",
               password_hash=hash_password("GodPass123!"), full_name="God",
               role="god_admin", must_change_password=False)
    db_session.add(god)
    db_session.commit()
    headers = {"Authorization": f"Bearer {create_access_token(god, db_session)}"}
    response = client.get("/god/maintenance/pipeline-consistency?verdict=nonsense",
                          headers=headers)
    assert response.status_code == 400
