from app.models.models import Lead, LeadStatus, Organization, User
from app.services.auth_service import create_access_token, hash_password


def _email_lead(db_session, org_id, advisor_id, first_name, last_name, email, phone=None):
    lead = Lead(
        organization_id=org_id,
        assigned_to_id=advisor_id,
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone=phone,
        contact_channel="email_only",
        status=LeadStatus.NEW,
    )
    db_session.add(lead)
    db_session.commit()
    db_session.refresh(lead)
    return lead


def test_email_queue_search_filters_by_partial_name_and_email(client, db_session, sample_org, sample_advisor, auth_headers):
    alice = _email_lead(
        db_session,
        sample_org.id,
        sample_advisor.id,
        "Alice",
        "Stone",
        "alice.stone@example.com",
        phone="12145550101",
    )
    bob = _email_lead(
        db_session,
        sample_org.id,
        sample_advisor.id,
        "Robert",
        "Lane",
        "bob.match@example.com",
        phone=None,
    )
    _email_lead(
        db_session,
        sample_org.id,
        sample_advisor.id,
        "Carol",
        "Ignore",
        "carol.ignore@example.com",
        phone="12145550103",
    )

    name_response = client.get("/email/queue?search=Ali", headers=auth_headers)
    assert name_response.status_code == 200
    name_rows = name_response.json()
    assert [row["id"] for row in name_rows] == [alice.id]
    assert name_rows[0]["phone"] == "12145550101"

    email_response = client.get("/email/queue?search=match", headers=auth_headers)
    assert email_response.status_code == 200
    email_rows = email_response.json()
    assert [row["id"] for row in email_rows] == [bob.id]
    assert email_rows[0]["phone"] is None


def test_email_queue_phone_is_present_or_null(client, db_session, sample_org, sample_advisor, auth_headers):
    with_phone = _email_lead(
        db_session,
        sample_org.id,
        sample_advisor.id,
        "Phone",
        "Present",
        "phone.present@example.com",
        phone="19725550101",
    )
    without_phone = _email_lead(
        db_session,
        sample_org.id,
        sample_advisor.id,
        "Phone",
        "Missing",
        "phone.missing@example.com",
        phone=None,
    )

    response = client.get("/email/queue", headers=auth_headers)
    assert response.status_code == 200
    rows = {row["id"]: row for row in response.json()}

    assert rows[with_phone.id]["phone"] == "19725550101"
    assert rows[without_phone.id]["phone"] is None


def test_email_queue_search_stays_scoped_to_logged_in_advisor_org(client, db_session, sample_org, sample_advisor, auth_headers):
    other_org = Organization(name="Other Cemetery", slug="other", plan="standard")
    db_session.add(other_org)
    db_session.commit()

    other_user = User(
        organization_id=other_org.id,
        email="other@example.com",
        password_hash=hash_password("TestPass123!"),
        full_name="Other Advisor",
        role="advisor",
    )
    db_session.add(other_user)
    db_session.commit()

    _email_lead(
        db_session,
        other_org.id,
        other_user.id,
        "Alice",
        "Foreign",
        "alice.foreign@example.com",
        phone="12145559999",
    )
    _email_lead(
        db_session,
        sample_org.id,
        sample_advisor.id,
        "Alice",
        "Local",
        "alice.local@example.com",
        phone="12145550000",
    )

    response = client.get("/email/queue?search=alice", headers=auth_headers)
    assert response.status_code == 200
    rows = response.json()

    assert len(rows) == 1
    assert rows[0]["email"] == "alice.local@example.com"


# ---------------------------------------------------------------------------
# Email preview/confirm-send - the actual review-before-send flow Email
# Queue never had. Previously /email/send-batch sent immediately with no
# way to see the subject/body first, unlike SMS which always shows a
# review screen. Mirrors /leads/preview-messages + /leads/confirm-send-batch.
# ---------------------------------------------------------------------------

def test_preview_batch_drafts_subject_and_body_without_sending(client, db_session, sample_org, sample_advisor, auth_headers):
    from app.models.models import EmailMessage
    lead = _email_lead(db_session, sample_org.id, sample_advisor.id, "Preview", "Test", "preview@example.com")

    response = client.post("/email/preview-batch", json={"lead_ids": [lead.id]}, headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["lead_id"] == lead.id
    assert body[0]["skip_reason"] is None
    assert "Preview" in body[0]["draft_subject"] or "Preview" in body[0]["draft_body_html"]
    # Confirms nothing was actually sent during preview
    assert db_session.query(EmailMessage).filter(EmailMessage.lead_id == lead.id).count() == 0


def test_preview_batch_skips_lead_with_no_email(client, db_session, sample_org, sample_advisor, auth_headers):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="NoEmail", last_name="Lead", email=None, contact_channel="email_only", status=LeadStatus.NEW)
    db_session.add(lead)
    db_session.commit()

    response = client.post("/email/preview-batch", json={"lead_ids": [lead.id]}, headers=auth_headers)

    assert response.status_code == 200
    assert response.json()[0]["skip_reason"] == "No email address on file"


def test_preview_batch_org_isolated(client, db_session, sample_org, sample_advisor, auth_headers):
    other_org = Organization(name="Other Email Org", slug="other-email-org", plan="trial")
    db_session.add(other_org)
    db_session.commit()
    other_advisor = User(organization_id=other_org.id, email="other-email-advisor@example.com",
                          password_hash=hash_password("x"), full_name="Other", role="advisor")
    db_session.add(other_advisor)
    db_session.commit()
    other_lead = _email_lead(db_session, other_org.id, other_advisor.id, "Other", "Org", "otherorg@example.com")

    response = client.post("/email/preview-batch", json={"lead_ids": [other_lead.id]}, headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == []


def test_confirm_send_batch_persists_email_message_and_updates_lead_status(client, db_session, sample_org, sample_advisor, auth_headers, monkeypatch):
    import app.services.email_service as email_service
    monkeypatch.setattr(email_service, "send_email_via_provider", lambda *a, **k: {"success": True, "provider_message_id": "test-msg-1", "error": None})

    lead = _email_lead(db_session, sample_org.id, sample_advisor.id, "Confirm", "Send", "confirm@example.com")

    response = client.post("/email/confirm-send-batch", json={
        "items": [{"lead_id": lead.id, "subject": "Edited subject", "body_html": "<p>Edited body</p>"}],
    }, headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["sent_count"] == 1

    db_session.refresh(lead)
    assert lead.status == "sent"

    from app.models.models import EmailMessage
    msg = db_session.query(EmailMessage).filter(EmailMessage.lead_id == lead.id).first()
    assert msg is not None
    assert msg.subject == "Edited subject"
    assert msg.status == "sent"


def test_confirm_send_batch_skips_lead_with_no_email(client, db_session, sample_org, sample_advisor, auth_headers):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="NoEmail2", last_name="Lead", email=None, contact_channel="email_only", status=LeadStatus.NEW)
    db_session.add(lead)
    db_session.commit()

    response = client.post("/email/confirm-send-batch", json={
        "items": [{"lead_id": lead.id, "subject": "x", "body_html": "x"}],
    }, headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["skipped_count"] == 1
    assert response.json()["sent_count"] == 0


def test_confirm_send_batch_records_failure_status_on_provider_failure(client, db_session, sample_org, sample_advisor, auth_headers, monkeypatch):
    import app.services.email_service as email_service
    monkeypatch.setattr(email_service, "send_email_via_provider", lambda *a, **k: {"success": False, "provider_message_id": None, "error": "simulated failure"})

    lead = _email_lead(db_session, sample_org.id, sample_advisor.id, "Fail", "Case", "fail@example.com")

    response = client.post("/email/confirm-send-batch", json={
        "items": [{"lead_id": lead.id, "subject": "x", "body_html": "x"}],
    }, headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["failed_count"] == 1
    db_session.refresh(lead)
    assert lead.status != "sent"


# ---------------------------------------------------------------------------
# Sent history - leads previously vanished from the queue entirely once
# emailed (queue filters status=='new'), with no way to look back.
# ---------------------------------------------------------------------------

def test_sent_history_shows_leads_already_emailed(client, db_session, sample_org, sample_advisor, auth_headers, monkeypatch):
    import app.services.email_service as email_service
    monkeypatch.setattr(email_service, "send_email_via_provider", lambda *a, **k: {"success": True, "provider_message_id": "msg-2", "error": None})

    lead = _email_lead(db_session, sample_org.id, sample_advisor.id, "AlreadySent", "Lead", "alreadysent@example.com")
    client.post("/email/confirm-send-batch", json={
        "items": [{"lead_id": lead.id, "subject": "Hello there", "body_html": "<p>hi</p>"}],
    }, headers=auth_headers)

    # Confirm it no longer shows in the active queue...
    queue_response = client.get("/email/queue", headers=auth_headers)
    assert lead.id not in {l["id"] for l in queue_response.json()}

    # ...but DOES show in sent history.
    sent_response = client.get("/email/sent", headers=auth_headers)
    assert sent_response.status_code == 200
    sent_ids = {row["lead_id"] for row in sent_response.json()}
    assert lead.id in sent_ids
    matching = next(row for row in sent_response.json() if row["lead_id"] == lead.id)
    assert matching["subject"] == "Hello there"
    assert matching["status"] == "sent"


def test_sent_history_scoped_to_logged_in_advisor(client, db_session, sample_org, sample_advisor, second_advisor, auth_headers, monkeypatch):
    import app.services.email_service as email_service
    monkeypatch.setattr(email_service, "send_email_via_provider", lambda *a, **k: {"success": True, "provider_message_id": "msg-3", "error": None})

    own_lead = _email_lead(db_session, sample_org.id, sample_advisor.id, "Mine", "Lead", "mine@example.com")
    client.post("/email/confirm-send-batch", json={
        "items": [{"lead_id": own_lead.id, "subject": "s", "body_html": "b"}],
    }, headers=auth_headers)

    other_lead = _email_lead(db_session, sample_org.id, second_advisor.id, "Theirs", "Lead", "theirs@example.com")
    from app.models.models import EmailMessage
    db_session.add(EmailMessage(lead_id=other_lead.id, sender_id=second_advisor.id, subject="other", body_html="b", status="sent"))
    db_session.commit()

    response = client.get("/email/sent", headers=auth_headers)
    sent_ids = {row["lead_id"] for row in response.json()}
    assert own_lead.id in sent_ids
    assert other_lead.id not in sent_ids
