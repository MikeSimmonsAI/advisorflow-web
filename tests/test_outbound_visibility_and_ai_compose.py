"""
SS10 Phase 1 — the contract and visibility defects, with no send path involved.

Three separate bugs are covered here, all of them silent in production and
none of them account-specific:

  1. /ai-conversation/preview returns the drafted body as `reply`. The Leads
     bulk composer read `result.message`, which has never existed, so the
     feature failed identically for every user with every API key.
  2. The same request sends `ai_direction`; the endpoint did not declare it and
     did not forward it, so the operator's typed instruction was discarded.
  3. Sent-message rows are stamped with the lead's ASSIGNED advisor
     (compose_router.acting_advisor) while the read paths filtered on the
     CALLER, so an advisor could not see their own sends on a colleague's lead
     and an org_admin saw nothing at all in the email sent log.

Nothing here sends anything. The AI client is monkeypatched to raise so the
fallback path is exercised without reaching OpenAI.
"""

import pytest

from app.models.models import EmailMessage, Lead, LeadStatus, Message, Organization, User
from app.services.auth_service import create_access_token, hash_password


def _lead(db_session, org, advisor, *, first_name="Lead", phone="12145558001", email="lead@example.com"):
    lead = Lead(
        organization_id=org.id,
        assigned_to_id=advisor.id if advisor else None,
        first_name=first_name,
        last_name="Visibility",
        phone=phone,
        phone_raw=phone,
        email=email,
        status=LeadStatus.NEW,
    )
    db_session.add(lead)
    db_session.flush()
    return lead


def _email(db_session, lead, sender, subject):
    row = EmailMessage(
        lead_id=lead.id,
        sender_id=sender.id,
        subject=subject,
        body_html=f"<p>{subject}</p>",
        status="sent",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _sms(db_session, lead, sender, body):
    row = Message(lead_id=lead.id, sender_id=sender.id, body=body, twilio_status="sent")
    db_session.add(row)
    db_session.flush()
    return row


def _headers(db_session, user):
    return {"Authorization": f"Bearer {create_access_token(user, db_session)}"}


# ── 1 + 2: the preview contract ────────────────────────────────────────────

def test_preview_returns_reply_and_forwards_the_typed_ai_direction(
    client, auth_headers, db_session, sample_org, sample_advisor, monkeypatch
):
    """
    The body comes back as `reply`, and `ai_direction` reaches the generator.

    Before this, `ai_direction` was not on SingleReplyRequest, so pydantic
    dropped it and preview_auto_reply never passed it on.
    """
    lead = _lead(db_session, sample_org, sample_advisor)
    db_session.commit()

    captured = {}

    def fake_generate(db, lead_arg, advisor, tone="warm", ai_direction=None, relationship_type=None, actor=None):
        captured["tone"] = tone
        captured["ai_direction"] = ai_direction
        captured["relationship_type"] = relationship_type
        return {
            "reply": "Drafted body",
            "subject": "Drafted subject",
            "should_stop": False,
            "reason": "",
            "source": "ai",
            "error_kind": None,
            "booking_url": "",
        }

    import app.routers.ai_conversation_router as router_mod
    monkeypatch.setattr(router_mod, "generate_auto_reply", fake_generate)

    response = client.post(
        "/ai-conversation/preview",
        headers=auth_headers,
        json={"lead_id": lead.id, "tone": "urgent", "ai_direction": "Mention the Saturday open house"},
    )
    assert response.status_code == 200
    body = response.json()

    # The contract the frontend now reads.
    assert body["reply"] == "Drafted body"
    assert "message" not in body, "nothing should reintroduce a second name for the body"

    # The instruction survived the round trip.
    assert captured["ai_direction"] == "Mention the Saturday open house"
    assert captured["tone"] == "urgent"


def test_preview_marks_a_failed_ai_call_as_a_fallback_with_its_failure_kind(
    client, auth_headers, db_session, sample_org, sample_advisor, monkeypatch
):
    """
    A dead API key used to produce a plausible AI-looking draft with no signal.

    The real service path runs here; only the OpenAI client is replaced, and it
    raises, so the `except` that builds the canned body is what answers.
    """
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145558002")
    db_session.commit()

    import app.services.ai_conversation_service as svc

    class FakeAuthError(Exception):
        pass

    def exploding_client():
        raise FakeAuthError("Incorrect API key provided: sk-abc...xyz")

    monkeypatch.setattr(svc, "_get_client", exploding_client)

    response = client.post(
        "/ai-conversation/preview",
        headers=auth_headers,
        json={"lead_id": lead.id, "tone": "warm"},
    )
    assert response.status_code == 200
    body = response.json()

    assert body["source"] == "fallback"
    assert body["error_kind"] == "FakeAuthError"
    assert body["reply"], "the fallback still returns a body — it just admits what it is"
    # The key fragment stays in the server log, not in the API response.
    assert "sk-abc" not in response.text


# ── 3: read/write attribution ──────────────────────────────────────────────

def test_email_sent_log_shows_sends_on_my_own_leads(
    client, db_session, sample_org, sample_advisor, second_advisor
):
    """
    The row is stamped with the lead's assigned advisor. Advisor One must see
    the email on Advisor One's lead even though Advisor Two pressed send.
    """
    mine = _lead(db_session, sample_org, sample_advisor, phone="12145558101", email="mine@example.com")
    theirs = _lead(db_session, sample_org, second_advisor, phone="12145558102", email="theirs@example.com")

    # Written the way production writes it: sender_id = the ASSIGNED advisor.
    _email(db_session, mine, sample_advisor, "On my lead")
    _email(db_session, theirs, second_advisor, "On their lead")
    db_session.commit()

    response = client.get("/email/sent-log", headers=_headers(db_session, sample_advisor))
    assert response.status_code == 200
    subjects = {row["subject"] for row in response.json()}

    assert "On my lead" in subjects
    assert "On their lead" not in subjects, "an advisor must not see a colleague's book"


def test_email_sent_log_lets_an_org_admin_see_the_whole_team(
    client, admin_auth_headers, db_session, sample_org, sample_advisor, second_advisor
):
    """Previously the admin saw nothing at all: no manager branch existed."""
    a = _lead(db_session, sample_org, sample_advisor, phone="12145558201", email="a@example.com")
    b = _lead(db_session, sample_org, second_advisor, phone="12145558202", email="b@example.com")
    _email(db_session, a, sample_advisor, "Advisor one send")
    _email(db_session, b, second_advisor, "Advisor two send")
    db_session.commit()

    response = client.get("/email/sent-log", headers=admin_auth_headers)
    assert response.status_code == 200
    subjects = {row["subject"] for row in response.json()}
    assert {"Advisor one send", "Advisor two send"} <= subjects


def test_email_sent_log_stays_inside_the_workspace_org(
    client, admin_auth_headers, db_session, sample_org, sample_advisor
):
    """Widening WITHIN an org must not widen ACROSS orgs."""
    other = Organization(name="Other Co", slug="other-visibility", plan="trial")
    db_session.add(other)
    db_session.flush()
    outsider = User(
        organization_id=other.id,
        email="outsider@other.com",
        password_hash=hash_password("OtherPass123!"),
        full_name="Outsider",
        role="advisor",
        must_change_password=False,
    )
    db_session.add(outsider)
    db_session.flush()

    foreign_lead = _lead(db_session, other, outsider, phone="12145558301", email="foreign@example.com")
    _email(db_session, foreign_lead, outsider, "Foreign org send")

    mine = _lead(db_session, sample_org, sample_advisor, phone="12145558302", email="mine2@example.com")
    _email(db_session, mine, sample_advisor, "Home org send")
    db_session.commit()

    response = client.get("/email/sent-log", headers=admin_auth_headers)
    assert response.status_code == 200
    subjects = {row["subject"] for row in response.json()}
    assert "Home org send" in subjects
    assert "Foreign org send" not in subjects


def test_activity_feed_shows_sends_on_my_leads_but_not_a_colleagues(
    client, db_session, sample_org, sample_advisor, second_advisor
):
    """Same seam, both channels, on the Activity feed."""
    mine = _lead(db_session, sample_org, sample_advisor, phone="12145558401", email="m@example.com")
    theirs = _lead(db_session, sample_org, second_advisor, phone="12145558402", email="t@example.com")

    _sms(db_session, mine, sample_advisor, "SMS on my lead")
    _email(db_session, mine, sample_advisor, "Email on my lead")
    _sms(db_session, theirs, second_advisor, "SMS on their lead")
    _email(db_session, theirs, second_advisor, "Email on their lead")
    db_session.commit()

    response = client.get("/activity/sent", headers=_headers(db_session, sample_advisor))
    assert response.status_code == 200
    items = response.json()

    lead_ids = {item["lead_id"] for item in items}
    assert mine.id in lead_ids
    assert theirs.id not in lead_ids
    # Both channels of my own lead came through.
    assert {item["channel"] for item in items if item["lead_id"] == mine.id} == {"sms", "email"}


def test_activity_feed_stays_inside_the_workspace_org(
    client, admin_auth_headers, db_session, sample_org, sample_advisor
):
    other = Organization(name="Other Activity Co", slug="other-activity", plan="trial")
    db_session.add(other)
    db_session.flush()
    outsider = User(
        organization_id=other.id,
        email="outsider2@other.com",
        password_hash=hash_password("OtherPass123!"),
        full_name="Outsider Two",
        role="advisor",
        must_change_password=False,
    )
    db_session.add(outsider)
    db_session.flush()
    foreign_lead = _lead(db_session, other, outsider, phone="12145558501", email="f2@example.com")
    _sms(db_session, foreign_lead, outsider, "Foreign SMS")

    mine = _lead(db_session, sample_org, sample_advisor, phone="12145558502", email="m2@example.com")
    _sms(db_session, mine, sample_advisor, "Home SMS")
    db_session.commit()

    response = client.get("/activity/sent", headers=admin_auth_headers)
    assert response.status_code == 200
    lead_ids = {item["lead_id"] for item in response.json()}
    assert mine.id in lead_ids
    assert foreign_lead.id not in lead_ids


def test_a_reassigned_lead_keeps_its_history_visible_to_both_advisors(
    client, db_session, sample_org, sample_advisor, second_advisor
):
    """
    The case the widening actually buys an advisor.

    Advisor One emails their lead, so the row is stamped with Advisor One. The
    lead is later reassigned to Advisor Two. Under `sender_id == caller` that
    history became invisible to the person who now owns the family - they took
    over a lead and could not see what had already been said to it, which is
    the worst possible moment to be missing the history.
    """
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145558601", email="reassigned@example.com")
    _email(db_session, lead, sample_advisor, "Sent before the handover")
    db_session.commit()

    # Handover.
    lead.assigned_to_id = second_advisor.id
    db_session.commit()

    # The new owner can see what was already sent to their family.
    taking_over = client.get("/email/sent-log", headers=_headers(db_session, second_advisor))
    assert taking_over.status_code == 200
    assert "Sent before the handover" in {row["subject"] for row in taking_over.json()}

    # And the advisor who actually sent it has not lost it either.
    original = client.get("/email/sent-log", headers=_headers(db_session, sample_advisor))
    assert original.status_code == 200
    assert "Sent before the handover" in {row["subject"] for row in original.json()}
