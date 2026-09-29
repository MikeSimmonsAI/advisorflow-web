"""Replies to the shared sending mailbox reach the right lead (2026-09-29).

Joshua replied to an Atlantis email sent From support@evosyspro.live; the reply
sat in that Outlook mailbox and never appeared in EvoSys. Graph is faked here
(fetch=...); no network, no mail.
"""
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.inbound_mailbox_models import InboundMailbox, InboundMailboxMessage
from app.models.models import EmailMessage, Lead, Notification, Organization, Reply
from app.services import inbound_mailbox_service as S

BOX = "support@evosyspro.live"


@pytest.fixture()
def world(db_session, sample_advisor, monkeypatch):
    atl = Organization(name="Atlantis Test", slug="a-%s" % uuid.uuid4().hex[:6], is_active=True)
    other = Organization(name="Other Test", slug="o-%s" % uuid.uuid4().hex[:6], is_active=True)
    quiet = Organization(name="Own Domain Co", slug="q-%s" % uuid.uuid4().hex[:6], is_active=True)
    db_session.add_all([atl, other, quiet])
    db_session.commit()

    def ident(db, org_id):
        return SimpleNamespace(from_email=BOX if org_id in (atl.id, other.id) else "hello@own.test",
                               reply_to_email=None)
    monkeypatch.setattr("app.services.public_identity.sending_identity_for_org", ident)
    lead = Lead(organization_id=atl.id, first_name="Joshua", last_name="S", email="Josh@Example.com",
                status="sent", assigned_to_id=sample_advisor.id)
    db_session.add(lead)
    box = InboundMailbox(address=BOX, is_active=True)
    db_session.add(box)
    db_session.commit()
    return SimpleNamespace(atl=atl, other=other, quiet=quiet, lead=lead, box=box, advisor=sample_advisor)


def _msg(gid, sender, body, minutes_ago=5, subject="Re: Explore Your Energy Options"):
    t = (datetime.utcnow() - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": gid, "internetMessageId": "<%s@mail.gmail.com>" % gid, "subject": subject,
            "from": {"emailAddress": {"address": sender}}, "receivedDateTime": t,
            "body": {"content": body}, "bodyPreview": body[:50]}


def test_a_reply_to_the_shared_mailbox_lands_on_the_lead(db_session, world):
    msgs = [_msg("g1", "josh@example.com",
                 "Yes, I'm interested. Call me Friday.\n\nOn Tue, Sep 29, 2026 at 2:29 PM Atlantis Light & Power <support@evosyspro.live> wrote:\n> Hi Joshua")]
    res = S.poll_mailbox(db_session, world.box, fetch=lambda since: msgs)
    assert res == {"mailbox": BOX, "checked": 1, "matched": 1, "errors": 0}
    reply = db_session.query(Reply).filter(Reply.lead_id == world.lead.id).one()
    assert reply.body == "Yes, I'm interested. Call me Friday." and reply.source == "email"
    db_session.refresh(world.lead)
    assert world.lead.status == "replied"
    assert db_session.query(Notification).filter(Notification.lead_id == world.lead.id,
                                                 Notification.user_id == world.advisor.id).count() == 1
    row = db_session.query(InboundMailboxMessage).one()
    assert row.outcome == "matched" and row.organization_id == world.atl.id
    # Running again (overlapping window) changes nothing.
    S.poll_mailbox(db_session, world.box, fetch=lambda since: msgs)
    assert db_session.query(Reply).filter(Reply.lead_id == world.lead.id).count() == 1


def test_first_run_reads_three_days_back_then_follows_the_cursor(db_session, world):
    seen = []
    S.poll_mailbox(db_session, world.box, fetch=lambda since: seen.append(since) or [
        _msg("g1", "josh@example.com", "hello", minutes_ago=60)])
    assert datetime.utcnow() - seen[0] > timedelta(days=2, hours=23)
    S.poll_mailbox(db_session, world.box, fetch=lambda since: seen.append(since) or [])
    assert seen[1] > datetime.utcnow() - timedelta(minutes=75)


def test_routing_never_crosses_into_a_workspace_that_does_not_send_from_the_mailbox(db_session, world):
    db_session.add(Lead(organization_id=world.quiet.id, first_name="X", email="stranger@example.com"))
    db_session.commit()
    S.poll_mailbox(db_session, world.box, fetch=lambda since: [_msg("g2", "stranger@example.com", "hi")])
    row = db_session.query(InboundMailboxMessage).one()
    assert row.outcome == "no_lead" and db_session.query(Reply).count() == 0


def test_same_address_in_two_workspaces_goes_to_the_one_we_emailed(db_session, world):
    twin = Lead(organization_id=world.other.id, first_name="Joshua", email="josh@example.com", status="new")
    db_session.add(twin)
    db_session.commit()
    db_session.add(EmailMessage(lead_id=world.lead.id, sender_id=world.advisor.id, subject="s",
                                body_html="b", status="sent", sent_at=datetime.utcnow() - timedelta(hours=1)))
    db_session.commit()
    S.poll_mailbox(db_session, world.box, fetch=lambda since: [_msg("g3", "josh@example.com", "yes")])
    assert db_session.query(Reply).filter(Reply.lead_id == world.lead.id).count() == 1
    assert db_session.query(Reply).filter(Reply.lead_id == twin.id).count() == 0


def test_ambiguous_without_an_outbound_is_not_attached(db_session, world):
    db_session.add(Lead(organization_id=world.other.id, first_name="J2", email="josh@example.com"))
    db_session.commit()
    S.poll_mailbox(db_session, world.box, fetch=lambda since: [_msg("g4", "josh@example.com", "yes")])
    assert db_session.query(InboundMailboxMessage).one().outcome == "ambiguous"
    assert db_session.query(Reply).count() == 0


def test_a_read_failure_is_an_error_not_an_empty_inbox(db_session, world):
    def boom(since):
        raise RuntimeError("Graph inbox read failed 403: ErrorAccessDenied")
    res = S.poll_mailbox(db_session, world.box, fetch=boom)
    assert res["errors"] == 1
    db_session.refresh(world.box)
    assert world.box.last_status == "error" and "403" in world.box.last_error


def test_own_mail_is_skipped(db_session, world):
    S.poll_mailbox(db_session, world.box, fetch=lambda since: [_msg("g5", BOX, "auto")])
    assert db_session.query(InboundMailboxMessage).one().outcome == "own_mail"


def test_no_automated_conversation_is_started(db_session, world, monkeypatch):
    called = []
    monkeypatch.setattr("app.services.pipeline_service.process_inbound_reply",
                        lambda *a, **k: called.append(1))
    S.poll_mailbox(db_session, world.box, fetch=lambda since: [_msg("g6", "josh@example.com", "ok")])
    assert called == []


@pytest.fixture()
def god_headers(db_session):
    from app.models.models import User
    from app.services.auth_service import create_access_token, hash_password
    g = User(organization_id=None, email="god-%s@x.test" % uuid.uuid4().hex[:6],
             password_hash=hash_password("Pass12345!"), full_name="Platform Owner",
             role="god_admin", must_change_password=False, is_active=True)
    db_session.add(g)
    db_session.commit()
    return {"Authorization": "Bearer " + create_access_token(g, db_session)}


def test_god_endpoints(client, db_session, world, god_headers):
    r = client.get("/god/email/inbound-mailboxes", headers=god_headers)
    assert r.status_code == 200
    box = r.json()["mailboxes"][0]
    assert box["address"] == BOX and {o["id"] for o in box["routes_to"]} == {world.atl.id, world.other.id}


def test_god_endpoints_refuse_others(client, auth_headers):
    assert client.get("/god/email/inbound-mailboxes", headers=auth_headers).status_code in (401, 403)
