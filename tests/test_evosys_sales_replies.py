"""EvoSys Sales: replies stop a lead's follow-up by themselves (PR #663).

These run the real mailbox poller (inbound_mailbox_service.poll_mailbox) on
Graph-shaped messages, the same shape the production poller reads. They are not
a substitute for the live mailbox check, which is reported separately.
"""
from datetime import datetime, timedelta

import pytest

from app.models.evosys_sales_models import EvoSysOutreachMessage, EvoSysOutreachState
from app.models.inbound_mailbox_models import InboundMailbox, InboundMailboxMessage
from app.models.sales_models import Opportunity, OpportunityEvent
from app.services import inbound_mailbox_service as S
from app.services.evosys_sales import outreach as O
from app.services.evosys_sales import replies as R

from tests.test_sales_workspace import (  # noqa: F401  (fixtures)
    platform, brand, manager, manager_headers, _make_sales_user, _add_membership, _token_headers, _SEQ,
)

MON_10AM = datetime(2026, 11, 23, 16, 0)   # Monday 10:00 Central


@pytest.fixture(autouse=True)
def _brand_env(monkeypatch, brand):
    monkeypatch.setenv("EVOSYS_SALES_BRAND_ID", brand.id)


@pytest.fixture()
def box(db_session):
    b = InboundMailbox(address="support@evosyspro.live", is_active=True, last_status="ok")
    db_session.add(b); db_session.commit()
    return b


class Sender:
    def __init__(self):
        self.calls = []

    def __call__(self, to, msg, s, opp_id):
        self.calls.append((to, msg, opp_id))
        return {"success": True, "provider_message_id": "re_%d" % len(self.calls)}


def _lead(db, brand, company, email):
    o = Opportunity(brand_sales_org_id=brand.id, company_name=company, contact_name="Ana Ruiz",
                    phone="2145550100", email=email, stage="prospect", status="open")
    db.add(o); db.commit()
    return o


def _ready(db, brand):
    s = O.get_settings(db, brand.id)
    s.mailing_address, s.offer = "100 Main St, Dallas, TX 75201", "Free for 30 days."
    s.approved_at, s.sending_on = datetime.utcnow(), True
    db.commit()
    return s


def _graph(gid, sender, to, body="Yes, call me Thursday.", subject="Re: More commercial cleaning contracts",
           when=None, headers=None, imid=None):
    t = (when or datetime.utcnow()).strftime("%Y-%m-%dT%H:%M:%SZ")
    m = {"id": gid, "internetMessageId": imid or "<%s@mail.gmail.com>" % gid, "subject": subject,
         "from": {"emailAddress": {"address": sender}}, "toRecipients": [{"emailAddress": {"address": to}}],
         "receivedDateTime": t, "body": {"content": body}, "bodyPreview": body[:50]}
    if headers is not None:
        m["internetMessageHeaders"] = headers
    return m


def _sent_to(db, brand, sender):
    """Send step 1 to every enrolled lead; return {email: reply-to used}."""
    O.run_pass(db, MON_10AM, sender)
    return {to: msg["reply_to"] for to, msg, _ in sender.calls}


def test_each_lead_gets_its_own_reply_address_on_the_team_domain(db_session, brand):
    _lead(db_session, brand, "A Co", "a@x.test"); _lead(db_session, brand, "B Co", "b@x.test")
    _ready(db_session, brand)
    tags = _sent_to(db_session, brand, Sender())
    assert set(tags) == {"a@x.test", "b@x.test"}
    assert all(t.startswith("info+evs-") and t.endswith("@evosyspro.live") for t in tags.values())
    assert tags["a@x.test"] != tags["b@x.test"]


def test_reply_to_the_tag_is_recorded_stops_follow_up_and_skips_next_email(
        client, db_session, brand, box, manager_headers):
    a = _lead(db_session, brand, "Sparkle Co", "ana@sparkle.test")
    _ready(db_session, brand)
    snd = Sender()
    tag = _sent_to(db_session, brand, snd)["ana@sparkle.test"]
    res = S.poll_mailbox(db_session, box, fetch=lambda since: [
        _graph("g1", "ana@sparkle.test", tag, when=MON_10AM + timedelta(hours=1))])
    assert res["matched"] == 1
    st = db_session.get(EvoSysOutreachState, a.id)
    db_session.refresh(st)
    assert st.status == "stopped" and st.stop_reason == "replied"
    row = db_session.query(InboundMailboxMessage).one()
    assert row.outcome == "evs_reply" and a.id in row.detail
    # Conversations, the lead's page (messages + history) and Today show it.
    conv = client.get("/sales/evosys/conversations", headers=manager_headers).json()["conversations"]
    assert conv[0]["id"] == a.id and conv[0]["last_message"]["direction"] == "in"
    d = client.get("/sales/evosys/leads/%s" % a.id, headers=manager_headers).json()
    assert d["messages"][-1]["body"] == "Yes, call me Thursday." and d["next"] is None
    assert any("Replied by email" in e["summary"] for e in d["timeline"])
    assert client.get("/sales/evosys/overview", headers=manager_headers).json()["replies"][0]["lead"]["id"] == a.id
    # Step 2 is due three days later - it does not go.
    snd.calls.clear()
    O.run_pass(db_session, MON_10AM + timedelta(days=3), snd)
    assert snd.calls == []


def test_same_reply_read_twice_is_recorded_once(db_session, brand, box):
    a = _lead(db_session, brand, "Sparkle Co", "ana@sparkle.test")
    _ready(db_session, brand)
    tag = _sent_to(db_session, brand, Sender())["ana@sparkle.test"]
    msg = _graph("g1", "ana@sparkle.test", tag)
    S.poll_mailbox(db_session, box, fetch=lambda since: [msg])
    S.poll_mailbox(db_session, box, fetch=lambda since: [msg])
    # The same message under a new Graph id (moved between folders) too.
    S.poll_mailbox(db_session, box, fetch=lambda since: [dict(msg, id="g1-moved")])
    ins = db_session.query(EvoSysOutreachMessage).filter_by(opportunity_id=a.id, direction="in").all()
    assert len(ins) == 1
    assert db_session.query(OpportunityEvent).filter_by(opportunity_id=a.id, event_type="outreach_reply").count() == 1


@pytest.mark.parametrize("subject,headers", [
    ("Automatic reply: More commercial cleaning contracts", None),
    ("Out of Office", None),
    ("Re: More commercial cleaning contracts", [{"name": "Auto-Submitted", "value": "auto-replied"}]),
    ("Re: More commercial cleaning contracts", [{"name": "X-Autoreply", "value": "yes"}]),
    ("Re: More commercial cleaning contracts", [{"name": "Precedence", "value": "auto_reply"}]),
])
def test_automatic_replies_are_ignored(db_session, brand, box, subject, headers):
    a = _lead(db_session, brand, "Sparkle Co", "ana@sparkle.test")
    _ready(db_session, brand)
    snd = Sender()
    tag = _sent_to(db_session, brand, snd)["ana@sparkle.test"]
    S.poll_mailbox(db_session, box, fetch=lambda since: [
        _graph("g1", "ana@sparkle.test", tag, body="I am out until Monday.", subject=subject, headers=headers)])
    st = db_session.get(EvoSysOutreachState, a.id)
    db_session.refresh(st)
    assert st.status == "active"
    assert db_session.query(InboundMailboxMessage).one().outcome == "evs_auto_reply"
    assert db_session.query(EvoSysOutreachMessage).filter_by(direction="in").count() == 0
    snd.calls.clear()
    O.run_pass(db_session, MON_10AM + timedelta(days=3), snd)
    assert len(snd.calls) == 1   # follow-up continues


def test_untagged_reply_matches_only_a_single_emailed_lead(db_session, brand, box):
    a = _lead(db_session, brand, "Solo Co", "solo@x.test")
    _lead(db_session, brand, "Twin 1", "twin@x.test"); _lead(db_session, brand, "Twin 2", "twin@x.test")
    _ready(db_session, brand)
    _sent_to(db_session, brand, Sender())
    S.poll_mailbox(db_session, box, fetch=lambda since: [
        _graph("g1", "solo@x.test", "info@evosyspro.live"),
        _graph("g2", "twin@x.test", "info@evosyspro.live"),
        _graph("g3", "never-emailed@x.test", "info@evosyspro.live")])
    out = {r.graph_message_id: r.outcome for r in db_session.query(InboundMailboxMessage).all()}
    assert out["g1"] == "evs_reply"
    assert out["g2"] == "evs_ambiguous"            # two leads: attached to neither
    assert out["g3"] not in ("evs_reply", "evs_ambiguous")
    assert db_session.get(EvoSysOutreachState, a.id).stop_reason == "replied"
    twins = db_session.query(EvoSysOutreachState).filter(EvoSysOutreachState.stop_reason == "replied").count()
    assert twins == 1


def test_unknown_tag_attaches_to_nobody(db_session, brand, box):
    _lead(db_session, brand, "A Co", "a@x.test")
    _ready(db_session, brand)
    _sent_to(db_session, brand, Sender())
    S.poll_mailbox(db_session, box, fetch=lambda since: [_graph("g1", "a@x.test", "info+evs-zzzzzzzzzz@evosyspro.live")])
    assert db_session.query(InboundMailboxMessage).one().outcome == "evs_unknown_tag"
    assert db_session.query(EvoSysOutreachMessage).filter_by(direction="in").count() == 0


def test_a_reply_recorded_mid_pass_stops_the_queued_email(db_session, brand):
    """The sender re-reads each lead right before sending."""
    a = _lead(db_session, brand, "First Co", "first@x.test")
    b = _lead(db_session, brand, "Second Co", "second@x.test")
    _ready(db_session, brand)
    O.enroll_new(db_session, brand.id, MON_10AM)
    db_session.get(EvoSysOutreachState, a.id).next_due_at = MON_10AM - timedelta(minutes=2)
    db_session.get(EvoSysOutreachState, b.id).next_due_at = MON_10AM - timedelta(minutes=1)
    db_session.commit()
    sent = []

    def snd(to, msg, s, opp_id):
        sent.append(to)
        if to == "first@x.test":
            # While First is being emailed, Second's reply is recorded.
            R.record(db_session, db_session.get(Opportunity, b.id), subject="Re: hi", body="stop",
                     received_at=MON_10AM, internet_message_id="<x@y>", method="reply_address",
                     sender="second@x.test")
            db_session.commit()
        return {"success": True}
    O.run_pass(db_session, MON_10AM, snd)
    assert sent == ["first@x.test"]


def test_reply_tracking_reports_the_connected_mailbox(client, db_session, brand, box, manager_headers):
    t = client.get("/sales/evosys/outreach", headers=manager_headers).json()["reply_tracking"]
    assert t["automatic"] is True and t["mailbox"] == "support@evosyspro.live"
    box.last_status = "auth_error"; db_session.commit()
    t = client.get("/sales/evosys/outreach", headers=manager_headers).json()["reply_tracking"]
    assert t["automatic"] is False


def test_reply_address_must_be_on_the_team_domain(db_session, brand):
    s = _ready(db_session, brand)
    s.reply_to_email = "mike@gmail.com"
    assert any("Replies must go to" in p for p in O.settings_problems(s))
