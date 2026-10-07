"""Atlantis inbound reply -> cadence pause -> human handoff (synthetic, zero sends).

All data is invented (example.test addresses). Graph is faked via fetch=...;
every outbound path (httpx, SMTP-style senders) is replaced with a recorder that
must stay empty.
"""
import uuid
from datetime import datetime, timedelta

import pytest

from app.models.models import (CadenceState, EmailMessage, Lead, Notification, NotificationType,
                               Organization, Reply, User)
from app.models.inbound_mailbox_models import InboundMailbox, InboundMailboxMessage
from app.services import inbound_mailbox_service as S

BOX = "atlantis-sender@example.test"


def _user(org, email, role):
    return User(organization_id=org.id, email=email, password_hash="x", full_name=email, role=role,
                must_change_password=False)


@pytest.fixture()
def world(db_session, monkeypatch):
    sent = []
    # Nothing in this chain may call out. httpx is only reached by Graph, which is faked.
    monkeypatch.setattr(S.httpx, "post", lambda *a, **k: sent.append(("post", a)) or pytest.fail("outbound call"))
    monkeypatch.setattr(S.httpx, "get", lambda *a, **k: sent.append(("get", a)) or pytest.fail("outbound call"))
    atl = Organization(name="Atlantis Synthetic", slug="atl-%s" % uuid.uuid4().hex[:6], is_active=True,
                       from_email=BOX, reply_to_email=BOX)
    other = Organization(name="Twin Tenant", slug="twin-%s" % uuid.uuid4().hex[:6], is_active=True,
                         from_email="hello@twin.example.test")
    db_session.add_all([atl, other])
    db_session.commit()
    atl_admin, twin_admin = _user(atl, "admin@atl.example.test", "org_admin"), _user(other, "admin@twin.example.test", "org_admin")
    atl_adv = _user(atl, "adv@atl.example.test", "advisor")
    db_session.add_all([atl_admin, twin_admin, atl_adv])
    box = InboundMailbox(address=BOX, organization_id=atl.id, is_active=True)
    lead = Lead(organization_id=atl.id, first_name="Pat", email="Pat@Customer.example.test", status="sent")
    twin = Lead(organization_id=other.id, first_name="Pat", email="pat@customer.example.test", status="sent")
    db_session.add_all([box, lead, twin])
    db_session.commit()
    db_session.add(EmailMessage(lead_id=lead.id, sender_id=atl_adv.id, subject="Your energy options",
                                body_html="b", status="sent", sent_at=datetime.utcnow() - timedelta(days=1)))
    db_session.add(CadenceState(lead_id=lead.id, status="active", current_touch_number=1,
                                next_touch_due_at=datetime.utcnow() + timedelta(hours=1)))
    db_session.add(CadenceState(lead_id=twin.id, status="active", current_touch_number=1,
                                next_touch_due_at=datetime.utcnow() + timedelta(hours=1)))
    db_session.commit()
    return type("W", (), dict(atl=atl, other=other, lead=lead, twin=twin, box=box, sent=sent,
                              atl_admin=atl_admin, twin_admin=twin_admin, atl_adv=atl_adv))


def _msg(gid="g1", sender="pat@customer.example.test", subject="Re: Your energy options"):
    t = (datetime.utcnow() - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": gid, "internetMessageId": "<%s@mail.example.test>" % gid, "subject": subject,
            "from": {"emailAddress": {"address": sender}}, "receivedDateTime": t,
            "body": {"content": "Yes, call me."}, "bodyPreview": "Yes, call me."}


def test_atlantis_identity_is_atlantis_specific(db_session, world):
    addrs = S.org_sending_addresses(db_session)
    assert addrs[world.atl.id] == {BOX}
    assert BOX not in addrs[world.other.id]


def test_reply_pauses_cadence_only_for_the_atlantis_lead_and_sends_nothing(db_session, world):
    res = S.poll_mailbox(db_session, world.box, fetch=lambda since: [_msg()])
    assert res["matched"] == 1
    assert db_session.query(CadenceState).filter_by(lead_id=world.lead.id).one().status == "stopped_replied"
    assert db_session.query(CadenceState).filter_by(lead_id=world.twin.id).one().status == "active"
    assert db_session.query(Reply).filter_by(lead_id=world.twin.id).count() == 0
    assert world.sent == []


def test_unassigned_lead_reply_notifies_own_workspace_admin_only(db_session, world):
    S.poll_mailbox(db_session, world.box, fetch=lambda since: [_msg()])
    notes = db_session.query(Notification).all()
    assert [(n.user_id, n.lead_id, n.type) for n in notes] == [
        (world.atl_admin.id, world.lead.id, NotificationType.REPLY_RECEIVED)]


def test_assigned_lead_notifies_the_advisor_not_the_admin(db_session, world):
    world.lead.assigned_to_id = world.atl_adv.id
    db_session.commit()
    S.poll_mailbox(db_session, world.box, fetch=lambda since: [_msg()])
    assert [n.user_id for n in db_session.query(Notification).all()] == [world.atl_adv.id]


def test_replay_is_one_reply_one_notification_one_transition(db_session, world):
    msgs = [_msg()]
    S.poll_mailbox(db_session, world.box, fetch=lambda since: msgs)
    S.poll_mailbox(db_session, world.box, fetch=lambda since: msgs)
    assert db_session.query(Reply).filter_by(lead_id=world.lead.id).count() == 1
    assert db_session.query(Notification).count() == 1
    assert db_session.query(InboundMailboxMessage).count() == 1
    db_session.refresh(world.lead)
    assert world.lead.status == "replied"


def test_unknown_sender_attaches_nowhere_and_pauses_nothing(db_session, world):
    S.poll_mailbox(db_session, world.box, fetch=lambda since: [_msg(sender="nobody@elsewhere.example.test")])
    assert db_session.query(InboundMailboxMessage).one().outcome == "no_lead"
    assert db_session.query(Reply).count() == 0 and db_session.query(Notification).count() == 0
    assert {c.status for c in db_session.query(CadenceState).all()} == {"active"}


# -- Conversation visibility + manager scope (real route, synthetic, zero sends) --
def _hdr(db_session, user):
    from app.services.auth_service import create_access_token
    return {"Authorization": "Bearer " + create_access_token(user, db_session)}


def test_stored_email_reply_is_visible_in_the_conversation_route(db_session, world, client):
    S.poll_mailbox(db_session, world.box, fetch=lambda since: [_msg()])
    r = client.get("/leads/%s/timeline" % world.lead.id, headers=_hdr(db_session, world.atl_admin))
    assert r.status_code == 200
    inbound = [e for e in r.json()["events"] if e["type"] == "inbound"]
    assert len(inbound) == 1
    assert inbound[0]["channel"] == "email" and inbound[0]["body"] == "Yes, call me."
    assert inbound[0]["id"] and inbound[0]["timestamp"]
    assert world.sent == []


def test_manager_scope_missing_and_cross_tenant_are_indistinguishable(db_session, world, client):
    S.poll_mailbox(db_session, world.box, fetch=lambda since: [_msg()])
    h = _hdr(db_session, world.atl_admin)
    cross = client.get("/leads/%s/timeline" % world.twin.id, headers=h)
    missing = client.get("/leads/%s/timeline" % uuid.uuid4(), headers=h)
    assert cross.status_code == missing.status_code
    assert cross.json() == missing.json()
    assert "Yes, call me." not in cross.text
    other = client.get("/leads/%s/timeline" % world.lead.id, headers=_hdr(db_session, world.twin_admin))
    assert other.status_code == missing.status_code and "Yes, call me." not in other.text


def test_manager_gets_no_god_or_platform_billing_access(db_session, world, client):
    h = _hdr(db_session, world.atl_admin)
    for path in ("/god/billing", "/god/organizations"):
        assert client.get(path, headers=h).status_code in (401, 403, 404)
