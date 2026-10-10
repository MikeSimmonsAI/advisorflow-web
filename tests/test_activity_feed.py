"""Activity & Call History: one cross-channel feed, scoped like every lead read."""
from datetime import datetime, timedelta

from app.models.models import EmailMessage, Lead, Message, Organization, Reply, User, VoiceCall
from app.models.telephony_models import Voicemail
from app.services.auth_service import create_access_token, hash_password


def _user(db, org, email, role):
    u = User(organization_id=org.id, email=email, password_hash=hash_password("TestPass123!"),
             full_name=email.split("@")[0], role=role, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _h(db, u):
    return {"Authorization": "Bearer %s" % create_access_token(u, db)}


def _world(db):
    org = Organization(name="Feed Org", slug="feed-org", plan="standard")
    other = Organization(name="Other Feed Org", slug="other-feed-org", plan="standard")
    db.add_all([org, other])
    db.commit()
    admin = _user(db, org, "admin@feed.test", "org_admin")
    adv = _user(db, org, "adv@feed.test", "advisor")
    adv2 = _user(db, org, "adv2@feed.test", "advisor")
    stranger = _user(db, other, "x@other.test", "org_admin")
    mine = Lead(organization_id=org.id, first_name="Mine", last_name="Lead", phone="+12145550101", assigned_to_id=adv.id)
    theirs = Lead(organization_id=org.id, first_name="Their", last_name="Lead", phone="+12145550102", assigned_to_id=adv2.id)
    foreign = Lead(organization_id=other.id, first_name="Foreign", last_name="Lead", phone="+12145550103")
    db.add_all([mine, theirs, foreign])
    db.commit()
    now = datetime.utcnow()
    db.add_all([
        Message(lead_id=mine.id, sender_id=adv.id, body="Hello from us", sent_at=now - timedelta(hours=5), twilio_status="delivered"),
        Reply(lead_id=mine.id, body="Got it, thanks", received_at=now - timedelta(hours=4)),
        EmailMessage(lead_id=mine.id, sender_id=adv.id, subject="Your guide", body_html="<p>Guide</p>",
                     sent_at=now - timedelta(hours=3)),
        VoiceCall(lead_id=mine.id, advisor_id=adv.id, organization_id=org.id, to_phone=mine.phone, direction="outbound",
                  outcome="no_answer", voicemail_left=True, created_at=now - timedelta(hours=2)),
        Voicemail(organization_id=org.id, lead_id=mine.id, from_e164=mine.phone, received_at=now - timedelta(hours=1), status="new"),
        Message(lead_id=theirs.id, sender_id=adv2.id, body="Other advisor's text", sent_at=now - timedelta(hours=1)),
        Message(lead_id=foreign.id, sender_id=stranger.id, body="Another company", sent_at=now - timedelta(minutes=30)),
    ])
    db.commit()
    return admin, adv, stranger


def test_feed_has_every_channel_both_directions_newest_first(client, db_session):
    admin, adv, _ = _world(db_session)
    body = client.get("/activity/feed", headers=_h(db_session, adv)).json()
    kinds = [(i["kind"], i["channel"], i["direction"]) for i in body["items"]]
    assert kinds == [("voicemail", "voice", "inbound"), ("call", "voice", "outbound"),
                     ("message", "email", "outbound"), ("message", "sms", "inbound"),
                     ("message", "sms", "outbound")]
    assert all(i["lead_name"] == "Mine Lead" for i in body["items"])
    assert len({i["id"] for i in body["items"]}) == len(body["items"])   # one row per event
    call = body["items"][1]
    assert call["voicemail_left"] is True and call["actor"] == "ai"


def test_feed_is_scoped_to_the_advisor_and_the_workspace(client, db_session):
    admin, adv, stranger = _world(db_session)
    names = lambda u: {i["lead_name"] for i in client.get("/activity/feed", headers=_h(db_session, u)).json()["items"]}  # noqa: E731
    assert names(adv) == {"Mine Lead"}
    assert names(admin) == {"Mine Lead", "Their Lead"}
    assert names(stranger) == {"Foreign Lead"}


def test_an_inbound_call_and_its_voicemail_are_one_event(client, db_session):
    admin, adv, _ = _world(db_session)
    lead = db_session.query(Lead).filter(Lead.first_name == "Mine").one()
    now = datetime.utcnow()
    db_session.add_all([
        VoiceCall(lead_id=lead.id, advisor_id=adv.id, organization_id=lead.organization_id, to_phone="+18449172171",
                  direction="inbound", call_sid="CA-same", outcome="voicemail_received", created_at=now - timedelta(minutes=5)),
        Voicemail(organization_id=lead.organization_id, lead_id=lead.id, call_sid="CA-same",
                  received_at=now - timedelta(minutes=4), status="new", transcript="Call me back"),
    ])
    db_session.commit()
    items = client.get("/activity/feed", headers=_h(db_session, adv)).json()["items"]
    linked = [i for i in items if i["kind"] == "call" and i["direction"] == "inbound"]
    assert len(linked) == 1 and linked[0]["voicemail_received"] is True and linked[0]["summary"] == "Call me back"
    assert len([i for i in items if i["kind"] == "voicemail"]) == 1   # only the unlinked one from _world
