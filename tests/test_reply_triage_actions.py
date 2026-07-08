from app.models.models import (
    Organization,
    User,
    Lead,
    LeadStatus,
    Reply,
    ReplyClassification,
)
from app.services.auth_service import hash_password, create_access_token


def _make_foreign_reply(db_session):
    other_org = Organization(name="Other Org", slug="other-reply-triage", plan="trial")
    db_session.add(other_org)
    db_session.commit()

    other_advisor = User(
        organization_id=other_org.id,
        email="other-triage@example.com",
        password_hash=hash_password("TestPass123!"),
        full_name="Other Advisor",
        role="advisor",
    )
    db_session.add(other_advisor)
    db_session.commit()

    foreign_lead = Lead(
        organization_id=other_org.id,
        assigned_to_id=other_advisor.id,
        first_name="Foreign",
        last_name="Lead",
        phone="12145550003",
        status=LeadStatus.NEW,
    )
    db_session.add(foreign_lead)
    db_session.commit()

    foreign_reply = Reply(
        lead_id=foreign_lead.id,
        body="Call me later",
        classification=ReplyClassification.CALLBACK,
    )
    db_session.add(foreign_reply)
    db_session.commit()
    return foreign_reply, foreign_lead


def test_mark_reviewed_sets_reviewed_at_for_same_org_reply(client, auth_headers, db_session, sample_lead):
    reply = Reply(
        lead_id=sample_lead.id,
        body="Yes, I am interested",
        classification=ReplyClassification.INTERESTED,
    )
    db_session.add(reply)
    db_session.commit()

    response = client.patch(f"/sms/replies/{reply.id}/mark-reviewed", headers=auth_headers, json={})

    assert response.status_code == 200
    assert response.json()["reviewed_at"] is not None

    db_session.refresh(reply)
    assert reply.reviewed_at is not None


def test_mark_reviewed_cannot_touch_reply_from_another_org(client, auth_headers, db_session):
    foreign_reply, _ = _make_foreign_reply(db_session)

    response = client.patch(f"/sms/replies/{foreign_reply.id}/mark-reviewed", headers=auth_headers, json={})

    assert response.status_code == 404
    db_session.refresh(foreign_reply)
    assert foreign_reply.reviewed_at is None


def test_reclassify_updates_reply_but_dnc_does_not_change_lead_status(client, auth_headers, db_session, sample_lead):
    sample_lead.status = LeadStatus.REPLIED
    reply = Reply(
        lead_id=sample_lead.id,
        body="Actually don't contact me",
        classification=ReplyClassification.NEUTRAL,
    )
    db_session.add(reply)
    db_session.commit()

    response = client.patch(
        f"/sms/replies/{reply.id}/reclassify",
        headers=auth_headers,
        json={"classification": "dnc"},
    )

    assert response.status_code == 200
    assert response.json()["classification"] == "dnc"

    db_session.refresh(reply)
    db_session.refresh(sample_lead)
    assert reply.classification == ReplyClassification.DNC
    assert sample_lead.status == LeadStatus.REPLIED


def test_reclassify_cannot_touch_reply_from_another_org(client, auth_headers, db_session):
    foreign_reply, foreign_lead = _make_foreign_reply(db_session)

    response = client.patch(
        f"/sms/replies/{foreign_reply.id}/reclassify",
        headers=auth_headers,
        json={"classification": "interested"},
    )

    assert response.status_code == 404
    db_session.refresh(foreign_reply)
    db_session.refresh(foreign_lead)
    assert foreign_reply.classification == ReplyClassification.CALLBACK
    assert foreign_lead.status == LeadStatus.NEW


def test_reclassify_rejects_invalid_classification(client, auth_headers, db_session, sample_lead):
    reply = Reply(lead_id=sample_lead.id, body="Maybe", classification=ReplyClassification.NEUTRAL)
    db_session.add(reply)
    db_session.commit()

    response = client.patch(
        f"/sms/replies/{reply.id}/reclassify",
        headers=auth_headers,
        json={"classification": "super_hot"},
    )

    assert response.status_code == 422
