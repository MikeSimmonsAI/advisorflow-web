"""
SS5 — complete communication history, and a cursor that can actually reach it.

THE DEFECT. `/leads/{id}/timeline` took the 200 newest rows per channel and
had no offset, cursor or page parameter on its signature. Past 200 messages -
which a nine-touch cadence plus bulk sends reaches - the older ones could not
be retrieved by any request the API was capable of expressing.

THE SECOND DEFECT. History was assembled from message rows, so anything that
never produced one was invisible: a blocked cadence touch, a suppressed send,
a failed attempt. Those are exactly the events somebody asking "why did this
family never hear from us" needs.
"""

from datetime import datetime, timedelta

import pytest

from app.models.models import (
    BookingLink, CadenceState, CadenceTouchLog, EmailMessage, Lead, Message,
    Organization, Reply, User, VoiceCall,
)
from app.services import cadence_service as cs
from app.services import communication_history as ch
from app.services import send_source
from app.services.auth_service import create_access_token, hash_password


def _lead(db_session, org, advisor, phone="12145553001"):
    lead = Lead(organization_id=org.id, assigned_to_id=advisor.id,
                first_name="Hist", last_name="Ory", phone=phone, phone_raw=phone,
                email="hist@example.com", status="replied")
    db_session.add(lead)
    db_session.commit()
    return lead


def _sms(db_session, lead, advisor, when, body="text"):
    row = Message(lead_id=lead.id, sender_id=advisor.id, body=body,
                  twilio_sid="SM1", twilio_status="sent", sent_at=when,
                  send_source=send_source.CADENCE)
    db_session.add(row); db_session.commit(); return row


def _headers(db_session, user):
    return {"Authorization": f"Bearer {create_access_token(user, db_session)}"}


# ── the read model ──────────────────────────────────────────────────────────

def test_the_read_model_writes_nothing():
    import ast, pathlib
    tree = ast.parse(pathlib.Path("app/services/communication_history.py")
                     .read_text(encoding="utf-8"))
    writes = {"add", "add_all", "commit", "delete", "flush", "merge"}
    bad = [f"line {n.lineno}" for n in ast.walk(tree)
           if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
           and n.func.attr in writes]
    assert not bad, bad


def test_every_channel_lands_in_one_ordered_stream(
        db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor)
    base = datetime.utcnow() - timedelta(hours=6)

    _sms(db_session, lead, sample_advisor, base)
    db_session.add(EmailMessage(lead_id=lead.id, sender_id=sample_advisor.id,
                                subject="Hello", body_html="<p>hi</p>",
                                status="sent", sent_at=base + timedelta(minutes=10),
                                send_source=send_source.MANUAL))
    db_session.add(Reply(lead_id=lead.id, body="yes please",
                         received_at=base + timedelta(minutes=20)))
    db_session.add(VoiceCall(lead_id=lead.id, advisor_id=sample_advisor.id,
                             organization_id=sample_org.id, to_phone="12145553001",
                             status="completed", direction="outbound",
                             created_at=base + timedelta(minutes=30)))
    db_session.add(BookingLink(lead_id=lead.id, user_id=sample_advisor.id,
                               status="booked",
                               created_at=base + timedelta(minutes=40)))
    db_session.commit()

    out = ch.fetch(db_session, lead.id)
    channels = [e["channel"] for e in out["events"]]
    assert set(channels) == {"sms", "email", "voice", "appointment"}
    stamps = [e["timestamp"] for e in out["events"]]
    assert stamps == sorted(stamps, reverse=True), "newest first"


def test_a_cadence_touch_that_produced_no_message_still_appears(
        db_session, sample_org, sample_advisor):
    """The point of the read model. A blocked touch writes no messages row, so
    it was invisible to every history the product could assemble."""
    lead = _lead(db_session, sample_org, sample_advisor)
    state = CadenceState(lead_id=lead.id, status="active", current_touch_number=0,
                         cadence_started_at=datetime.utcnow(),
                         next_touch_due_at=datetime.utcnow())
    db_session.add(state); db_session.flush()
    db_session.add(CadenceTouchLog(
        cadence_state_id=state.id, lead_id=lead.id, organization_id=sample_org.id,
        touch_number=1, attempt_seq=1, channel="sms",
        outcome=cs.OUTCOME_BLOCKED, reason="phone is on the suppression list",
        attempted_at=datetime.utcnow()))
    db_session.commit()

    out = ch.fetch(db_session, lead.id)
    cadence = [e for e in out["events"] if e["channel"] == "cadence"]
    assert len(cadence) == 1
    assert cadence[0]["status"] == cs.OUTCOME_BLOCKED
    assert "suppression" in cadence[0]["meta"]["reason"]
    assert db_session.query(Message).count() == 0


def test_attribution_survives_into_the_history(
        db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor)
    row = _sms(db_session, lead, sample_advisor, datetime.utcnow())
    row.sent_by_user_id = sample_advisor.id
    db_session.commit()

    event = ch.fetch(db_session, lead.id)["events"][0]
    assert event["send_source"] == send_source.CADENCE
    assert event["sent_by_user_id"] == sample_advisor.id
    assert event["sender_id"] == sample_advisor.id


def test_the_cursor_walks_all_the_way_back_past_the_old_dead_end(
        db_session, sample_org, sample_advisor):
    """250 messages: more than the old hard cap of 200, which had no parameter
    that could reach beyond it."""
    lead = _lead(db_session, sample_org, sample_advisor)
    base = datetime.utcnow() - timedelta(days=30)
    for i in range(250):
        db_session.add(Message(lead_id=lead.id, sender_id=sample_advisor.id,
                               body=f"msg {i}", twilio_status="sent",
                               sent_at=base + timedelta(minutes=i)))
    db_session.commit()

    seen = []
    before = None
    for _ in range(10):
        page = ch.fetch(db_session, lead.id, limit=100, before=before)
        seen.extend(e["id"] for e in page["events"])
        if not page["has_more"]:
            break
        before = page["next_before"]

    assert len(set(seen)) == 250, "every message is reachable"


def test_a_page_reports_whether_more_exists(db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor)
    base = datetime.utcnow() - timedelta(hours=5)
    for i in range(5):
        db_session.add(Message(lead_id=lead.id, sender_id=sample_advisor.id,
                               body=f"m{i}", twilio_status="sent",
                               sent_at=base + timedelta(minutes=i)))
    db_session.commit()

    page = ch.fetch(db_session, lead.id, limit=2)
    assert len(page["events"]) == 2 and page["has_more"] is True
    last = ch.fetch(db_session, lead.id, limit=50)
    assert last["has_more"] is False


def test_channels_can_be_narrowed(db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor)
    now = datetime.utcnow()
    _sms(db_session, lead, sample_advisor, now)
    db_session.add(EmailMessage(lead_id=lead.id, sender_id=sample_advisor.id,
                                subject="s", body_html="<p>b</p>", status="sent",
                                sent_at=now))
    db_session.commit()
    out = ch.fetch(db_session, lead.id, channels=["email"])
    assert {e["channel"] for e in out["events"]} == {"email"}


# ── the endpoints ───────────────────────────────────────────────────────────

def test_the_timeline_now_takes_a_cursor_and_defaults_unchanged(
        client, auth_headers, db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor)
    base = datetime.utcnow() - timedelta(hours=3)
    for i in range(5):
        db_session.add(Message(lead_id=lead.id, sender_id=sample_advisor.id,
                               body=f"m{i}", twilio_status="sent",
                               sent_at=base + timedelta(minutes=i)))
    db_session.commit()

    full = client.get(f"/leads/{lead.id}/timeline", headers=auth_headers)
    assert full.status_code == 200
    assert len(full.json()["events"]) == 5
    assert full.json()["has_more"] is False

    paged = client.get(f"/leads/{lead.id}/timeline?limit=2", headers=auth_headers)
    assert len(paged.json()["events"]) == 2
    assert paged.json()["has_more"] is True
    assert paged.json()["next_before"] is not None


def test_the_history_endpoint_is_lead_scoped(
        client, auth_headers, db_session, sample_org, sample_advisor):
    other = Organization(name="Other Hist Co", slug="other-hist", plan="trial")
    db_session.add(other); db_session.flush()
    outsider = User(organization_id=other.id, email="h@other.test",
                    password_hash=hash_password("HPass123!"), full_name="H",
                    role="advisor", must_change_password=False)
    db_session.add(outsider); db_session.commit()
    foreign = _lead(db_session, other, outsider, phone="12145553099")

    response = client.get(f"/leads/{foreign.id}/history", headers=auth_headers)
    assert response.status_code in (403, 404)


def test_the_history_endpoint_returns_the_unified_stream(
        client, auth_headers, db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor)
    now = datetime.utcnow()
    _sms(db_session, lead, sample_advisor, now)
    db_session.add(Reply(lead_id=lead.id, body="ok", received_at=now))
    db_session.commit()

    response = client.get(f"/leads/{lead.id}/history?limit=10", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    kinds = {e["kind"] for e in body["events"]}
    assert kinds == {"outbound", "inbound"}
    assert body["counts"]["sms"] == 2
