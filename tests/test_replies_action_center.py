"""
Tests for GET /sms/replies/counts and the bucket= filter on GET
/sms/replies - the Replies "action center" Mike asked for directly:
"it should not just send me back to the lead sheet... it should feel
like an action center, not just a message list."

Deliberately built on the 5 buckets that already have real, tracked
data (hot, callback, question, not_interested/wrong_number/dnc, needs
follow-up, reviewed) - "Appointment interest" and "Objections" from
Mike's original notes aren't real ReplyClassification values yet and
were NOT faked here; they're a separate future classification project.
"""

from app.models.models import Reply, ReplyClassification, Lead, Organization, User
from app.services.auth_service import hash_password


def test_reply_counts_requires_auth(client):
    response = client.get("/sms/replies/counts")
    assert response.status_code == 401


def test_reply_counts_buckets_match_real_classifications(client, auth_headers, db_session, sample_lead):
    replies = [
        Reply(lead_id=sample_lead.id, body="Yes interested", classification=ReplyClassification.INTERESTED),
        Reply(lead_id=sample_lead.id, body="Call me", classification=ReplyClassification.CALLBACK),
        Reply(lead_id=sample_lead.id, body="What time?", classification=ReplyClassification.QUESTION),
        Reply(lead_id=sample_lead.id, body="Not interested", classification=ReplyClassification.NOT_INTERESTED),
        Reply(lead_id=sample_lead.id, body="Wrong person", classification=ReplyClassification.WRONG_NUMBER),
        Reply(lead_id=sample_lead.id, body="STOP", classification=ReplyClassification.DNC),
        Reply(lead_id=sample_lead.id, body="ok", classification=ReplyClassification.NEUTRAL),
    ]
    db_session.add_all(replies)
    db_session.commit()

    response = client.get("/sms/replies/counts", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["hot"] == 1
    assert body["callback"] == 1
    assert body["question"] == 1
    assert body["not_interested"] == 1
    assert body["wrong_number"] == 1
    assert body["dnc"] == 1
    assert body["neutral"] == 1
    assert body["total"] == 7


def test_reply_counts_needs_follow_up_excludes_already_reviewed(client, auth_headers, db_session, sample_lead):
    from datetime import datetime, timezone

    unreviewed_hot = Reply(lead_id=sample_lead.id, body="Yes!", classification=ReplyClassification.INTERESTED)
    reviewed_hot = Reply(lead_id=sample_lead.id, body="Yes already handled", classification=ReplyClassification.INTERESTED,
                          reviewed_at=datetime.now(timezone.utc))
    db_session.add_all([unreviewed_hot, reviewed_hot])
    db_session.commit()

    response = client.get("/sms/replies/counts", headers=auth_headers)
    body = response.json()
    assert body["needs_follow_up"] == 1
    assert body["hot"] == 2  # both are still "hot" by classification
    assert body["reviewed"] == 1


def test_reply_counts_scoped_to_own_leads_only(client, auth_headers, db_session, sample_org, second_advisor):
    other_lead = Lead(organization_id=sample_org.id, assigned_to_id=second_advisor.id,
                       first_name="Other", last_name="Advisor", phone="12145559091")
    db_session.add(other_lead)
    db_session.flush()
    other_reply = Reply(lead_id=other_lead.id, body="Not mine", classification=ReplyClassification.INTERESTED)
    db_session.add(other_reply)
    db_session.commit()

    response = client.get("/sms/replies/counts", headers=auth_headers)
    assert response.json()["hot"] == 0


def test_reply_counts_org_isolated(client, auth_headers, db_session, sample_org):
    other_org = Organization(name="Other Counts Org", slug="other-counts-org", plan="trial")
    db_session.add(other_org)
    db_session.commit()
    other_advisor = User(organization_id=other_org.id, email="other-counts@example.com",
                          password_hash=hash_password("x"), full_name="Other", role="advisor")
    db_session.add(other_advisor)
    db_session.commit()
    other_lead = Lead(organization_id=other_org.id, assigned_to_id=other_advisor.id,
                       first_name="Cross", last_name="Org", phone="12145559092")
    db_session.add(other_lead)
    db_session.flush()
    db_session.add(Reply(lead_id=other_lead.id, body="Cross org reply", classification=ReplyClassification.INTERESTED))
    db_session.commit()

    response = client.get("/sms/replies/counts", headers=auth_headers)
    assert response.json()["hot"] == 0


def test_reply_counts_all_zero_when_no_replies(client, auth_headers):
    response = client.get("/sms/replies/counts", headers=auth_headers)
    body = response.json()
    assert body["total"] == 0
    assert all(v == 0 for v in body.values())


# ---------------------------------------------------------------------------
# bucket= filter on GET /sms/replies - clicking a scorecard on the action
# center filters down to exactly that bucket's replies.
# ---------------------------------------------------------------------------

def test_list_replies_bucket_filter_matches_classification(client, auth_headers, db_session, sample_lead):
    hot = Reply(lead_id=sample_lead.id, body="Yes!", classification=ReplyClassification.INTERESTED)
    neutral = Reply(lead_id=sample_lead.id, body="ok", classification=ReplyClassification.NEUTRAL)
    db_session.add_all([hot, neutral])
    db_session.commit()

    response = client.get("/sms/replies?bucket=hot", headers=auth_headers)
    bodies = [r["body"] for r in response.json()]
    assert "Yes!" in bodies
    assert "ok" not in bodies


def test_list_replies_bucket_needs_follow_up(client, auth_headers, db_session, sample_lead):
    from datetime import datetime, timezone

    unreviewed = Reply(lead_id=sample_lead.id, body="Needs followup", classification=ReplyClassification.CALLBACK)
    reviewed = Reply(lead_id=sample_lead.id, body="Already handled", classification=ReplyClassification.CALLBACK,
                      reviewed_at=datetime.now(timezone.utc))
    db_session.add_all([unreviewed, reviewed])
    db_session.commit()

    response = client.get("/sms/replies?bucket=needs_follow_up", headers=auth_headers)
    bodies = [r["body"] for r in response.json()]
    assert "Needs followup" in bodies
    assert "Already handled" not in bodies


def test_list_replies_bucket_reviewed(client, auth_headers, db_session, sample_lead):
    from datetime import datetime, timezone

    reviewed = Reply(lead_id=sample_lead.id, body="Done", classification=ReplyClassification.NEUTRAL,
                      reviewed_at=datetime.now(timezone.utc))
    unreviewed = Reply(lead_id=sample_lead.id, body="Pending", classification=ReplyClassification.NEUTRAL)
    db_session.add_all([reviewed, unreviewed])
    db_session.commit()

    response = client.get("/sms/replies?bucket=reviewed", headers=auth_headers)
    bodies = [r["body"] for r in response.json()]
    assert "Done" in bodies
    assert "Pending" not in bodies


def test_list_replies_rejects_unknown_bucket(client, auth_headers):
    response = client.get("/sms/replies?bucket=not_a_real_bucket", headers=auth_headers)
    assert response.status_code == 400


def test_list_replies_with_no_bucket_returns_everything_unfiltered(client, auth_headers, db_session, sample_lead):
    """Confirms omitting bucket entirely preserves the original, pre-existing behavior."""
    reply = Reply(lead_id=sample_lead.id, body="Anything", classification=ReplyClassification.DNC)
    db_session.add(reply)
    db_session.commit()

    response = client.get("/sms/replies", headers=auth_headers)
    bodies = [r["body"] for r in response.json()]
    assert "Anything" in bodies
