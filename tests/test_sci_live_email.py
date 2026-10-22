"""SCI Live: email replies in one inbox, answered in their own thread, and
AI / Human handling that a manual message never permanently blocks."""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from app.models.inbound_mailbox_models import InboundMailbox, InboundMailboxMessage
from app.models.models import EmailMessage, Message, Reply
from app.models.program_models import ProgramConversation, ProgramConversationTurn, ProgramResponse
from app.services.programs import ai_setter
from app.services.programs import responses as R
from tests.test_outreach_program import _h, _lead_for, program  # noqa: F401 (fixture)
from tests.test_sci_appointment_setter import _Fake, _d, setter  # noqa: F401 (fixture)

SENDER = "ailene.moseley06@gmail.com"


def _email_in(db, lead, body="Thanks for checking on Estes but he is already taken care of.",
              subject="Re: A thought for you, Estes", name="Ailene Moseley", gid="AAMk-1"):
    box = db.query(InboundMailbox).filter_by(address="support@evosys.test").first()
    if box is None:
        box = InboundMailbox(address="support@evosys.test", is_active=True, refresh_token_encrypted="x")
        db.add(box)
        db.flush()
    rep = Reply(lead_id=lead.id, body=body, source="email", received_at=datetime.utcnow(),
                source_message_id="<m-%s@mail.gmail.com>" % gid)
    db.add(rep)
    db.flush()
    db.add(InboundMailboxMessage(mailbox_id=box.id, graph_message_id=gid, internet_message_id=rep.source_message_id,
                                 from_address=SENDER, from_name=name, subject=subject, outcome="matched",
                                 organization_id=lead.organization_id, lead_id=lead.id, reply_id=rep.id,
                                 received_at=datetime.utcnow()))
    db.commit()
    with patch("app.services.programs.responses._deliver_staff_alert", return_value=(True, None)):
        return R.on_inbound(db, lead, body, "email", reply_id=rep.id)


def _graph_ok():
    r = MagicMock()
    r.status_code = 202
    r.text = ""
    return r


def _estes(db, org, advisor):
    lead = _lead_for(db, org, advisor, "L001", first="Estes", last="Phillips", email=SENDER)
    return lead


def test_email_is_in_the_one_inbox_with_its_real_sender(setter, sample_advisor, client):
    db, org = setter["db"], setter["org"]
    prog = setter["prog"]
    prog.ai_setter_mode = "off"
    db.commit()
    lead = _estes(db, org, sample_advisor)
    _email_in(db, lead)
    h = _h(db, setter["admin"])
    r = client.get("/program/command", headers=h).json()
    it = [i for i in r["inbox"] if i["lead_id"] == lead.id][0]
    assert it["channel_label"] == "EMAIL" and it["sender_email"] == SENDER and it["sender_name"] == "Ailene Moseley"
    assert it["name"] == "Estes Phillips" and it["subject"].startswith("Re: A thought")   # family vs real sender
    assert r["filter_counts"]["email"] == 1 and r["filter_counts"]["unread"] == 1
    for q in ("moseley06", "thought for you"):
        assert [i["lead_id"] for i in client.get("/program/command?q=%s" % q, headers=h).json()["inbox"]] == [lead.id]
    assert client.get("/program/command?filter=text", headers=h).json()["inbox"] == []
    assert len(client.get("/program/command?filter=email", headers=h).json()["inbox"]) == 1
    fam = client.get("/program/command/family/%s" % lead.id, headers=h).json()
    assert fam["reply_to"]["channel"] == "email" and fam["reply_to"]["to"] == SENDER and fam["reply_to"]["threadable"]
    fe = [t for t in fam["thread"] if t["who"] == "family"][0]
    assert fe["sender_email"] == SENDER and fe["subject"]


def test_owner_email_reply_goes_in_thread_once_and_marks_responded(setter, sample_advisor, client):
    db, org = setter["db"], setter["org"]
    setter["prog"].ai_setter_mode = "off"
    db.commit()
    lead = _estes(db, org, sample_advisor)
    resp = _email_in(db, lead)
    h = _h(db, setter["admin"])
    body = {"lead_id": lead.id, "channel": "email", "text": "Thank you, Ailene. We're here if you need us.",
            "idempotency_key": "k1", "response_id": resp.id}
    with patch("app.services.inbound_mailbox_service._access_token_send", return_value="tok"), \
         patch("app.services.programs.email_reply.httpx.post", return_value=_graph_ok()) as post:
        r1 = client.post("/program/command/reply", headers=h, json=body)
        r2 = client.post("/program/command/reply", headers=h, json=body)          # double tap
    assert r1.status_code == 200, r1.text and r2.status_code == 200
    assert post.call_count == 1 and r2.json()["duplicate"] is True
    url = post.call_args.args[0]
    assert url.endswith("/me/messages/AAMk-1/reply")                              # threaded, not sendMail
    assert "Thank you, Ailene" in post.call_args.kwargs["json"]["comment"]
    em = db.query(EmailMessage).filter_by(lead_id=lead.id).one()
    assert em.to_email == SENDER and em.status == "sent" and em.in_reply_to and em.subject.startswith("Re:")
    db.refresh(resp)
    assert resp.handling_status == "responded"
    fam = client.get("/program/command/family/%s" % lead.id, headers=h).json()
    out = [t for t in fam["thread"] if t["who"] == "us" and t["channel"] == "email"][0]
    assert "Thank you, Ailene" in out["text"]                                    # the body, not the subject


def test_failed_email_reply_is_reported_and_not_marked(setter, sample_advisor, client):
    db, org = setter["db"], setter["org"]
    setter["prog"].ai_setter_mode = "off"
    db.commit()
    lead = _estes(db, org, sample_advisor)
    resp = _email_in(db, lead)
    bad = MagicMock(status_code=403, text="ErrorAccessDenied")
    with patch("app.services.inbound_mailbox_service._access_token_send", return_value="tok"), \
         patch("app.services.programs.email_reply.httpx.post", return_value=bad):
        r = client.post("/program/command/reply", headers=_h(db, setter["admin"]),
                        json={"lead_id": lead.id, "channel": "email", "text": "Hi", "idempotency_key": "k2",
                              "response_id": resp.id})
    assert r.status_code == 502 and "403" in r.json()["detail"]
    db.refresh(resp)
    assert resp.handling_status == "new"
    assert db.query(EmailMessage).filter_by(lead_id=lead.id).one().status == "failed"


def test_dnc_family_is_never_emailed(setter, sample_advisor, client):
    db, org = setter["db"], setter["org"]
    lead = _estes(db, org, sample_advisor)
    resp = _email_in(db, lead)
    lead.status = "dnc"
    db.commit()
    with patch("app.services.programs.email_reply.httpx.post") as post:
        r = client.post("/program/command/reply", headers=_h(db, setter["admin"]),
                        json={"lead_id": lead.id, "channel": "email", "text": "Hi", "idempotency_key": "k3",
                              "response_id": resp.id})
    assert r.status_code == 502 and post.call_count == 0


def test_ai_answers_an_email_in_its_thread_in_auto(setter, sample_advisor):
    db, org, prog = setter["db"], setter["org"], setter["prog"]
    prog.ai_setter_mode = "auto"
    db.commit()
    lead = _estes(db, org, sample_advisor)
    fake = _Fake([_d("offer_times", "Thank you, Ailene. I'd be glad to go over your father's plans with you - would {TIMES} work?")])
    with patch.object(ai_setter, "_get_client", return_value=fake), \
         patch("app.services.inbound_mailbox_service._access_token_send", return_value="tok"), \
         patch("app.services.programs.email_reply.httpx.post", return_value=_graph_ok()) as post, \
         patch("app.services.sms_service.send_sms") as sms:
        _email_in(db, lead)
    assert post.call_count == 1 and sms.call_count == 0
    ctx = fake.calls[0]["messages"][1]["content"]
    assert SENDER in ctx and "Estes Phillips" in ctx                              # knows writer vs record
    em = db.query(EmailMessage).filter_by(lead_id=lead.id).one()
    assert em.to_email == SENDER and em.send_source == "sci_appointment_setter"


def test_human_handling_stops_ai_and_ai_handling_resumes_after_a_manual_text(setter, sample_advisor, client,
                                                                             monkeypatch):
    db, org, prog = setter["db"], setter["org"], setter["prog"]
    prog.ai_setter_mode = "auto"
    db.commit()
    monkeypatch.setattr("app.services.contact_hours.check", lambda lead, now=None: {"permitted": True})
    lead = _lead_for(db, org, sample_advisor, "L001")
    conv = ProgramConversation(organization_id=org.id, lead_id=lead.id, state="offered",
                               next_followup_at=datetime.utcnow() + timedelta(hours=1))
    db.add(conv)
    db.flush()
    db.add(ProgramConversationTurn(conversation_id=conv.id, organization_id=org.id, lead_id=lead.id, role="ai",
                                   text="held one", delivery="held"))
    db.commit()
    h = _h(db, setter["admin"])
    r = client.post("/program/command/handling", headers=h, json={"lead_id": lead.id, "owner": "human"})
    assert r.status_code == 200 and r.json()["handling"]["owner"] == "human"
    db.refresh(conv)
    assert conv.state == "human" and conv.next_followup_at is None
    assert db.query(ProgramConversationTurn).filter_by(conversation_id=conv.id, text="held one").one().delivery == "dropped"
    # A person texts by hand (an old manual message)...
    db.add(Message(lead_id=lead.id, sender_id=sample_advisor.id, body="Hi from Kerry", send_source="manual",
                   sent_at=datetime.utcnow() - timedelta(minutes=5), twilio_sid="SMm"))
    db.commit()
    # ...then hands the conversation back to the AI.
    r = client.post("/program/command/handling", headers=h, json={"lead_id": lead.id, "owner": "ai"})
    assert r.json()["handling"]["owner"] == "ai"
    fake = _Fake([_d("offer_times", "Glad to help. Would {TIMES} work for a short visit?")])
    with patch.object(ai_setter, "_get_client", return_value=fake), \
         patch("app.services.sms_service.send_sms") as send, \
         patch("app.services.programs.responses._deliver_staff_alert", return_value=(True, None)):
        send.return_value = MagicMock(id="m1")
        R.on_inbound(db, lead, "What would we talk about?", "sms")
    assert len(fake.calls) == 1 and send.call_count == 1                          # not blocked by the old manual text
    assert "Hi from Kerry" in fake.calls[0]["messages"][1]["content"]           # and it knows what was sent


def test_send_and_let_ai_continue(setter, sample_advisor, client, monkeypatch):
    db, org, prog = setter["db"], setter["org"], setter["prog"]
    prog.ai_setter_mode = "auto"
    db.commit()
    lead = _lead_for(db, org, sample_advisor, "L001")
    h = _h(db, setter["admin"])

    def fake_send(db_, adv, lead_, text, *a, **k):
        m = Message(lead_id=lead_.id, sender_id=adv.id, body=text, send_source=k.get("send_source"),
                    twilio_sid="SMx", sent_at=datetime.utcnow())
        db_.add(m)
        db_.commit()
        return m
    monkeypatch.setattr("app.services.sms_service.send_sms", fake_send)
    r = client.post("/program/command/reply", headers=h,
                    json={"lead_id": lead.id, "channel": "sms", "text": "Hi, it's Kerry.", "idempotency_key": "s1",
                          "then": "ai"})
    assert r.status_code == 200, r.text
    assert r.json()["handling"]["owner"] == "ai"
    conv = db.query(ProgramConversation).filter_by(lead_id=lead.id).one()
    assert not ai_setter._human_took_over(db, lead, conv)
    r = client.post("/program/command/reply", headers=h,
                    json={"lead_id": lead.id, "channel": "sms", "text": "One more thing.", "idempotency_key": "s2"})
    assert r.json()["handling"]["owner"] == "human"


def test_ai_cannot_take_an_opted_out_conversation(setter, sample_advisor, client):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    db.add(ProgramConversation(organization_id=org.id, lead_id=lead.id, state="stopped"))
    db.commit()
    r = client.post("/program/command/handling", headers=_h(db, setter["admin"]),
                    json={"lead_id": lead.id, "owner": "ai"})
    assert r.status_code == 409


def test_another_workspace_cannot_reply_or_switch(setter, sample_advisor, client):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    h = _h(db, setter["admin"])
    assert client.post("/program/command/handling", headers=h,
                       json={"lead_id": "someone-else", "owner": "ai"}).status_code == 404
    assert client.post("/program/command/reply", headers=h,
                       json={"lead_id": "someone-else", "channel": "sms", "text": "x",
                             "idempotency_key": "z"}).status_code == 404
    assert lead


def test_only_the_owners_own_test_address_is_ever_emailed(setter, sample_advisor):
    from app.models.models import Lead
    from app.services import test_records as TR
    from app.services.programs import email_reply
    db, org, prog = setter["db"], setter["org"], setter["prog"]                 # alert_email owner@example.com
    me = Lead(organization_id=org.id, first_name="Owner", last_name=TR.OWNER_TEST_LAST_NAME, is_test=True,
              email="owner@example.com", status="sent")
    plain = Lead(organization_id=org.id, first_name="Sim", last_name="X", is_test=True, email="owner@example.com")
    db.add_all([me, plain])
    db.commit()
    assert email_reply._blocked(db, me, "owner@example.com") is None
    assert email_reply._blocked(db, me, "someone@else.com")
    assert email_reply._blocked(db, plain, "owner@example.com")


def test_test_conversations_never_show_in_sci_live_even_to_the_owner(setter, sample_advisor, client):
    from app.models.models import Lead
    from app.models.program_models import ProgramUnmatchedReply
    from app.services import test_records as TR
    db, org = setter["db"], setter["org"]
    me = Lead(organization_id=org.id, first_name="Owner", last_name=TR.OWNER_TEST_LAST_NAME, is_test=True,
              email="owner@example.com", status="sent", phone="+18435550000")
    db.add(me)
    db.add(ProgramUnmatchedReply(organization_id=org.id, from_address="+18435550000", body_excerpt="Nice",
                                 received_at=datetime.utcnow(), reason="x", status="open"))
    db.commit()
    _email_in(db, me, body="Testing the email path", gid="AAMk-owner")
    admin = setter["admin"]
    admin.role = "god_admin"
    db.commit()
    r = client.get("/program/command", headers=dict(_h(db, admin), **{"X-Org-Override": org.id})).json()
    assert [i for i in r["inbox"] if i["lead_id"] == me.id] == [] and r["unknown"] == []

def test_one_signature_and_the_guide_link(setter, sample_advisor, client, monkeypatch):
    from app.services.programs import email_reply
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    monkeypatch.setattr(email_reply, "signature", lambda db_, l: "Kerry Allan, Alabama Heritage Cemetery")
    for draft in ("Thanks for writing.\n\nKerry Allan\nAlabama Heritage Cemetery",
                  "Thanks for writing.\n- Kerry Allan, Alabama Heritage Cemetery",
                  "Thanks for writing.\n\nThanks,\nKerry", "Thanks for writing."):
        assert email_reply.strip_signature(db, lead, draft) == "Thanks for writing."
    monkeypatch.setattr("app.services.programs.identity.planning_guide_link",
                        lambda prof, db=None, family=None: "https://guide.example/x")
    monkeypatch.setattr("app.services.programs.identity.default_planning_guide", lambda: "https://guide.example/x")
    assert "https://guide.example/x" in email_reply.guide_token(db, lead, "Here it is: {GUIDE}")
    sent = {}

    def fake_send(db_, adv, lead_, text, *a, **k):
        sent["text"] = text
        m = Message(lead_id=lead_.id, sender_id=adv.id, body=text, send_source=k.get("send_source"),
                    twilio_sid="SMg", sent_at=datetime.utcnow())
        db_.add(m)
        db_.commit()
        return m
    monkeypatch.setattr("app.services.sms_service.send_sms", fake_send)
    r = client.post("/program/command/reply", headers=_h(db, setter["admin"]),
                    json={"lead_id": lead.id, "channel": "sms", "idempotency_key": "g1",
                          "text": "Here is your guide: {GUIDE}\n\nKerry Allan\nAlabama Heritage Cemetery"})
    assert r.status_code == 200, r.text
    assert sent["text"] == "Here is your guide: https://guide.example/x"


def test_an_opted_out_family_shows_as_opted_out(setter, sample_advisor, client):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    lead.status = "dnc"
    db.commit()
    fam = client.get("/program/command/family/%s" % lead.id, headers=_h(db, setter["admin"])).json()
    assert fam["handling"]["opted_out"] and fam["handling"]["owner"] == "human"


def test_one_scheduled_follow_up_respects_spacing_and_handling(setter, sample_advisor, client, monkeypatch):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    db.add(Message(lead_id=lead.id, sender_id=sample_advisor.id, body="Thank you", send_source="manual",
                   twilio_sid="SMf", sent_at=datetime.utcnow()))
    db.commit()
    monkeypatch.setattr("app.services.contact_hours.check", lambda lead, now=None: {"permitted": True})
    h = _h(db, setter["admin"])
    r = client.post("/program/command/follow-up", headers=h,
                    json={"lead_id": lead.id, "reason": "invite them in to go over the guide; offer two real times"})
    assert r.status_code == 200, r.text
    conv = db.query(ProgramConversation).filter_by(lead_id=lead.id).one()
    assert conv.next_followup_at >= datetime.utcnow() + timedelta(hours=19)     # 20-hour spacing kept
    client.post("/program/command/handling", headers=h, json={"lead_id": lead.id, "owner": "human"})
    db.refresh(conv)
    assert conv.next_followup_at is None                                         # a person took it: cancelled
    assert client.post("/program/command/follow-up", headers=h,
                       json={"lead_id": lead.id, "reason": "x"}).status_code == 409


def test_outcomes_never_count_a_guide_or_text_as_an_appointment(setter, sample_advisor, client):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    conv = ai_setter.conversation_for(db, lead)
    conv.memory = '{"mail_request": {"requested": true, "done": true}}'
    db.commit()
    o = client.get("/program/setter/stats", headers=_h(db, setter["admin"])).json()["outcomes"]
    assert o["appointments_accepted"] == 0 and o["confirmed_on_google_calendar"] == 0
    assert o["information_only_fulfilled"] == 1


def test_a_follow_up_may_honour_a_mail_request_and_invite_in(setter, sample_advisor, monkeypatch):
    db, org, prog = setter["db"], setter["org"], setter["prog"]
    prog.ai_setter_mode = "auto"
    db.commit()
    monkeypatch.setattr("app.services.contact_hours.check", lambda lead, now=None: {"permitted": True})
    lead = _lead_for(db, org, sample_advisor, "L001")
    conv = ai_setter.conversation_for(db, lead)
    conv.state, conv.next_followup_at = "info_requested", datetime.utcnow() - timedelta(minutes=1)
    conv.followup_reason = "invite him in"
    db.commit()
    fake = _Fake([_d("mail_guide", "We're arranging your printed guide. Would {TIMES} work to go over it in person?")])
    with patch.object(ai_setter, "_get_client", return_value=fake), \
         patch("app.services.sms_service.send_sms") as send:
        send.return_value = MagicMock(id="m1")
        ai_setter.followup_sweep(db)
    assert send.call_count == 1 and "Would " in send.call_args.args[3] and "{TIMES}" not in send.call_args.args[3]


def test_an_ai_text_reply_marks_the_message_answered(setter, sample_advisor, monkeypatch):
    db, org, prog = setter["db"], setter["org"], setter["prog"]
    prog.ai_setter_mode = "auto"
    db.commit()
    monkeypatch.setattr("app.services.contact_hours.check", lambda lead, now=None: {"permitted": True})
    lead = _lead_for(db, org, sample_advisor, "L001")
    fake = _Fake([_d("offer_times", "Glad to help. Would {TIMES} work for a short visit?")])
    with patch.object(ai_setter, "_get_client", return_value=fake), \
         patch("app.services.sms_service.send_sms") as send, \
         patch("app.services.programs.responses._deliver_staff_alert", return_value=(True, None)):
        send.return_value = MagicMock(id="m1")
        resp = R.on_inbound(db, lead, "What would we talk about?", "sms")
    db.refresh(resp)
    assert send.call_count == 1 and resp.handling_status == "responded"
