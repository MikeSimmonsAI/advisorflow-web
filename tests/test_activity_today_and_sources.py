"""
SS3 / SS4 / SS5 — attribution on the activity feeds, and timeline paging/scope.

SS4: /activity/sent never returned send_source, so the Activity screen could
not tell an AI-written message from one an advisor typed. The fields are
additive: every pre-existing key is still present.

SS5: /leads/{id}/timeline pages with a `before` cursor, and its scope check is
now the same helper /history uses (load_lead_in_scope), intersected with the
active-workspace org filter it always had.
"""

from datetime import datetime, timedelta

from app.models.models import EmailMessage, Lead, Message, Organization, User
from app.routers.activity_router import AI_GENERATED_SOURCES, is_ai_generated
from app.services import send_source
from app.services.auth_service import create_access_token, hash_password


LEGACY_SENT_KEYS = {
    "id", "channel", "lead_id", "lead_name", "lead_phone", "lead_email",
    "body_preview", "sent_at", "delivery_status", "delivery_status_at",
}


def _lead(db, org, advisor, phone="12145557301", email="src@example.com"):
    lead = Lead(organization_id=org.id, assigned_to_id=advisor.id,
                first_name="Src", last_name="Test", phone=phone,
                phone_raw=phone, email=email, status="new")
    db.add(lead); db.commit(); return lead


def _sms(db, lead, advisor, *, when=None, source=None, body="hello"):
    row = Message(lead_id=lead.id, sender_id=advisor.id, body=body,
                  twilio_sid="SM1", twilio_status="sent",
                  sent_at=when or datetime.utcnow(), send_source=source)
    db.add(row); db.commit(); return row


def _email(db, lead, advisor, *, when=None, source=None):
    row = EmailMessage(lead_id=lead.id, sender_id=advisor.id, subject="Subj",
                       body_html="<p>b</p>", status="sent",
                       sent_at=when or datetime.utcnow(), send_source=source)
    db.add(row); db.commit(); return row


def _headers(db, user):
    return {"Authorization": f"Bearer {create_access_token(user, db)}"}


# ── SS4 ─────────────────────────────────────────────────────────────────────

def test_ai_vocabulary_is_drawn_from_send_source():
    assert AI_GENERATED_SOURCES <= set(send_source.ALL_SOURCES)
    assert is_ai_generated(send_source.AI_CONVERSATION)
    assert is_ai_generated(send_source.BULK_AI)
    assert not is_ai_generated(send_source.MANUAL)
    assert not is_ai_generated(send_source.CADENCE)
    assert not is_ai_generated(None), "unrecorded is never reported as AI"


def test_sent_feed_carries_send_source_and_ai_flag(
        client, db_session, sample_org, sample_advisor, auth_headers):
    lead = _lead(db_session, sample_org, sample_advisor)
    now = datetime.utcnow()
    _sms(db_session, lead, sample_advisor, when=now - timedelta(minutes=3),
         source=send_source.AI_CONVERSATION)
    _sms(db_session, lead, sample_advisor, when=now - timedelta(minutes=2),
         source=send_source.MANUAL)
    _sms(db_session, lead, sample_advisor, when=now - timedelta(minutes=1),
         source=None)
    _email(db_session, lead, sample_advisor, source=send_source.BULK_AI)

    r = client.get("/activity/sent", headers=auth_headers)
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 4
    for item in items:
        # Backwards compatible: only fields were added.
        assert LEGACY_SENT_KEYS <= set(item)
        assert "send_source" in item
        assert "ai_generated" in item
        assert "send_source_label" in item

    by_source = {(i["channel"], i["send_source"]): i for i in items}
    assert by_source[("sms", "ai_conversation")]["ai_generated"] is True
    assert by_source[("sms", "manual")]["ai_generated"] is False
    assert by_source[("sms", None)]["ai_generated"] is False
    assert by_source[("sms", None)]["send_source_label"] == "Unrecorded"
    assert by_source[("email", "bulk_ai")]["ai_generated"] is True
    assert "subject" in by_source[("email", "bulk_ai")]


def test_today_feed_items_carry_ai_flag(
        client, db_session, sample_org, sample_advisor, auth_headers):
    lead = _lead(db_session, sample_org, sample_advisor)
    _sms(db_session, lead, sample_advisor, source=send_source.PIPELINE_AUTO_REPLY)

    r = client.get("/activity/today?tz=UTC", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["send_source"] == send_source.PIPELINE_AUTO_REPLY
    assert item["ai_generated"] is True


# ── SS5 ─────────────────────────────────────────────────────────────────────

def test_timeline_before_cursor_walks_back_to_the_oldest_message(
        client, db_session, sample_org, sample_advisor, auth_headers):
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145557302")
    base = datetime.utcnow().replace(microsecond=0)
    for i in range(7):
        _sms(db_session, lead, sample_advisor,
             when=base - timedelta(hours=i), body=f"m{i}")

    seen = []
    params = {"limit": 3}
    for _ in range(10):
        r = client.get(f"/leads/{lead.id}/timeline", params=params,
                       headers=auth_headers)
        assert r.status_code == 200
        page = r.json()
        seen.extend(e["body"] for e in page["events"] if e["channel"] == "sms")
        if not page["has_more"]:
            break
        params = {"limit": 3, "before": page["next_before"]}

    assert sorted(set(seen)) == sorted(f"m{i}" for i in range(7))


def test_timeline_is_404_for_another_advisors_lead(
        client, db_session, sample_org, sample_advisor, second_advisor):
    theirs = _lead(db_session, sample_org, second_advisor, phone="12145557303")
    r = client.get(f"/leads/{theirs.id}/timeline",
                   headers=_headers(db_session, sample_advisor))
    assert r.status_code == 404


def test_timeline_manager_sees_team_lead(
        client, db_session, sample_org, second_advisor, admin_auth_headers):
    theirs = _lead(db_session, sample_org, second_advisor, phone="12145557304")
    r = client.get(f"/leads/{theirs.id}/timeline", headers=admin_auth_headers)
    assert r.status_code == 200
    assert r.json()["lead"]["id"] == theirs.id


def test_timeline_is_404_across_tenants(client, db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145557305")
    other_org = Organization(name="Other TL Org", slug="other-tl-org", plan="trial")
    db_session.add(other_org); db_session.commit()
    other_admin = User(organization_id=other_org.id, email="tl-other@test.com",
                       password_hash=hash_password("x"), full_name="Other",
                       role="org_admin", must_change_password=False)
    db_session.add(other_admin); db_session.commit()

    r = client.get(f"/leads/{lead.id}/timeline",
                   headers=_headers(db_session, other_admin))
    assert r.status_code == 404


def test_timeline_cursor_never_skips_a_busy_channel(client, db_session, auth_headers, sample_org):
    """A busy channel and a quiet one reaching further back: walking the cursor
    must return every row exactly once."""
    from datetime import datetime, timedelta
    from app.models.models import Lead, Message, Reply, User
    owner = db_session.query(User).filter(User.organization_id == sample_org.id).first()
    lead = Lead(organization_id=sample_org.id, first_name="Cur", last_name="Sor", phone="12145559001",
                assigned_to_id=owner.id)
    db_session.add(lead); db_session.commit()
    now = datetime.utcnow()
    for i in range(7):                      # busy: sms every hour, newest first
        db_session.add(Message(lead_id=lead.id, sender_id=owner.id, body="m%d" % i, sent_at=now - timedelta(hours=i)))
    for i in range(2):                      # quiet: replies long ago
        db_session.add(Reply(lead_id=lead.id, body="r%d" % i, received_at=now - timedelta(days=10 + i)))
    db_session.commit()
    seen, before = [], None
    for _ in range(10):
        url = "/leads/%s/timeline?limit=3" % lead.id + ("&before=%s" % before if before else "")
        r = client.get(url, headers=auth_headers)
        assert r.status_code == 200, r.text
        body = r.json()
        seen += [(e["type"], e.get("body")) for e in body["events"] if e["type"] in ("outbound", "inbound")]
        if not body["has_more"]:
            break
        before = body["next_before"]
    bodies = sorted(b for _, b in seen)
    assert bodies == sorted(["m%d" % i for i in range(7)] + ["r0", "r1"]), bodies
