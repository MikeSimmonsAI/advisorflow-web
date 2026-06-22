from app.models.models import BookingLink, Reply
import app.services.draft_reply_service as draft_reply_service


def test_draft_reply_never_raises_without_openai_key_and_creates_booking_link(
    client,
    db_session,
    sample_lead,
    auth_headers,
    monkeypatch,
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    draft_reply_service._client = None

    reply = Reply(lead_id=sample_lead.id, body="Yes, can someone call me tomorrow?")
    db_session.add(reply)
    db_session.commit()

    response = client.post(f"/sms/draft-reply/{sample_lead.id}", headers=auth_headers, json={})

    assert response.status_code == 200
    body = response.json()
    assert body["suggested_reply"]
    assert body["source"] == "fallback"
    assert body["booking_url"] in body["suggested_reply"]

    links = db_session.query(BookingLink).filter(BookingLink.lead_id == sample_lead.id).all()
    assert len(links) == 1
    assert body["booking_link_id"] == links[0].id
    assert body["booking_url"].endswith(f"/book/{links[0].token}")


def test_draft_reply_reuses_existing_booking_link(client, db_session, sample_lead, sample_advisor, auth_headers, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    draft_reply_service._client = None

    existing = BookingLink(lead_id=sample_lead.id, user_id=sample_advisor.id, status="pending")
    db_session.add(existing)
    db_session.commit()
    db_session.refresh(existing)

    response = client.post(f"/sms/draft-reply/{sample_lead.id}", headers=auth_headers, json={})

    assert response.status_code == 200
    body = response.json()
    assert body["booking_link_id"] == existing.id
    assert body["booking_url"].endswith(f"/book/{existing.token}")

    links = db_session.query(BookingLink).filter(BookingLink.lead_id == sample_lead.id).all()
    assert len(links) == 1


def test_draft_reply_is_org_scoped(client, db_session, sample_org, auth_headers):
    from app.models.models import Lead, LeadTier, LeadStatus, MessageTrack, Organization, User
    from app.services.auth_service import hash_password

    other_org = Organization(name="Other Org", slug="other-org", plan="standard")
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

    other_lead = Lead(
        organization_id=other_org.id,
        assigned_to_id=other_user.id,
        first_name="Other",
        last_name="Lead",
        phone="12145550000",
        tier=LeadTier.PRE_NEED,
        message_track=MessageTrack.PRE_NEED_LOCK_PRICE,
        status=LeadStatus.NEW,
    )
    db_session.add(other_lead)
    db_session.commit()

    response = client.post(f"/sms/draft-reply/{other_lead.id}", headers=auth_headers, json={})

    assert response.status_code == 404
    assert db_session.query(BookingLink).filter(BookingLink.lead_id == other_lead.id).count() == 0
